#Requires -Version 5.1
<#
.SYNOPSIS
    Run the chatbot in the background - no console window to keep open.

.DESCRIPTION
    Starts the server with pythonw.exe, which has no console at all, so nothing
    stays on screen and closing your terminal does not kill the server. Logs go
    to logs\server.log instead (see scripts\serve.py for why that is needed).

    State is kept in data\server.pid and data\server.port so stop/status can
    find the process again after you close the window you started it from.

    For a real Windows service that survives reboots and starts before login,
    see service.ps1 (uses NSSM).

    ASCII-only on purpose - see the note in run.ps1.

.PARAMETER Action
    start | stop | restart | status | logs

.PARAMETER Port
    Port for 'start'. Default 8000. If busy, the next free port is used.

.PARAMETER Follow
    For 'logs': keep watching for new lines (like tail -f).

.PARAMETER Lines
    For 'logs': how many lines to show. Default 40.

.EXAMPLE
    .\serve.ps1 start
.EXAMPLE
    .\serve.ps1 status
.EXAMPLE
    .\serve.ps1 logs -Follow
.EXAMPLE
    .\serve.ps1 stop
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet("start", "stop", "restart", "status", "logs")]
    [string]$Action = "status",

    [int]$Port = 8000,
    [switch]$Follow,
    [int]$Lines = 40
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Pyw = Join-Path $Root ".venv\Scripts\pythonw.exe"
$Py = Join-Path $Root ".venv\Scripts\python.exe"
$PidFile = Join-Path $Root "data\server.pid"
$PortFile = Join-Path $Root "data\server.port"
$LogFile = Join-Path $Root "logs\server.log"

function Get-ServerProcess {
    # Returns the running process, or $null. Also clears a stale pid file.
    if (-not (Test-Path $PidFile)) { return $null }
    $raw = (Get-Content $PidFile -Raw).Trim()
    if (-not $raw) { return $null }
    $proc = Get-Process -Id ([int]$raw) -ErrorAction SilentlyContinue
    if (-not $proc) {
        # The process died without cleaning up - do not leave a misleading pid file.
        Remove-Item $PidFile -ErrorAction SilentlyContinue
        return $null
    }
    # Guard against pid reuse: Windows recycles pids, so confirm it is really ours.
    if ($proc.ProcessName -notmatch "^pythonw?$") {
        Remove-Item $PidFile -ErrorAction SilentlyContinue
        return $null
    }
    return $proc
}

function Get-ServerPort {
    if (Test-Path $PortFile) { return (Get-Content $PortFile -Raw).Trim() }
    return $null
}

function Test-PortBusy([int]$p) {
    $listener = $null
    try {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $p)
        $listener.Start(); return $false
    } catch { return $true } finally { if ($listener) { try { $listener.Stop() } catch { } } }
}

function Show-Status {
    $proc = Get-ServerProcess
    if (-not $proc) {
        Write-Host "  [stopped] the server is not running" -ForegroundColor Yellow
        Write-Host "            start it with:  .\serve.ps1 start" -ForegroundColor DarkGray
        return $false
    }
    $p = Get-ServerPort
    $url = "http://127.0.0.1:$p"
    $uptime = (Get-Date) - $proc.StartTime
    Write-Host "  [running] pid $($proc.Id)  up $([int]$uptime.TotalMinutes) min" -ForegroundColor Green
    Write-Host "            $url" -ForegroundColor Cyan
    try {
        $h = Invoke-RestMethod -Uri "$url/health" -TimeoutSec 3
        $rag = if ($h.rag) { "ready" } else { "not ready" }
        $dbs = if ($h.db) { "ready" } else { "off" }
        Write-Host "            rag: $rag   db: $dbs" -ForegroundColor DarkGray
    } catch {
        Write-Host "            (health check did not answer yet)" -ForegroundColor Yellow
    }
    return $true
}

function Start-Server {
    if (Get-ServerProcess) {
        Write-Host "  [WARN] already running" -ForegroundColor Yellow
        Show-Status | Out-Null
        return
    }
    if (-not (Test-Path $Pyw)) {
        Write-Host "  [FAIL] .venv not found at $Pyw" -ForegroundColor Red
        Write-Host "         python -m venv .venv" -ForegroundColor Yellow
        Write-Host "         .\.venv\Scripts\python.exe -m pip install -r requirements-rag.txt" -ForegroundColor Yellow
        exit 1
    }

    $chosen = $Port
    while ((Test-PortBusy $chosen) -and ($chosen -lt $Port + 20)) { $chosen++ }
    if (Test-PortBusy $chosen) {
        Write-Host "  [FAIL] no free port from $Port upwards" -ForegroundColor Red
        exit 1
    }

    New-Item -ItemType Directory -Force -Path (Join-Path $Root "data") | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $Root "logs") | Out-Null

    # pythonw.exe has no console, so the server keeps running after this window
    # closes and nothing is displayed. All output goes to logs\server.log.
    #
    # Every path argument MUST be quoted: Start-Process joins -ArgumentList with
    # spaces and does no quoting of its own, so an unquoted "D:\Project IS\..."
    # arrives as two arguments and python exits immediately. With pythonw there
    # is no stderr, so that failure is completely silent - quote it here.
    $ServeScript = Join-Path $Root "scripts\serve.py"
    $argList = @("`"$ServeScript`"", "--port", "$chosen")

    $proc = Start-Process -FilePath $Pyw -ArgumentList $argList `
        -WorkingDirectory $Root -WindowStyle Hidden -PassThru

    $proc.Id | Out-File $PidFile -Encoding ascii -NoNewline
    "$chosen" | Out-File $PortFile -Encoding ascii -NoNewline

    $url = "http://127.0.0.1:$chosen"
    Write-Host "  starting..." -ForegroundColor DarkGray

    # Wait for /health so we report a real result, not just "process launched".
    for ($i = 0; $i -lt 120; $i++) {
        Start-Sleep -Milliseconds 500
        if (-not (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue)) {
            Write-Host "  [FAIL] the process exited straight away" -ForegroundColor Red
            Remove-Item $PidFile -ErrorAction SilentlyContinue

            # pythonw swallows stderr, so a crash before logging starts leaves no
            # trace at all. Re-run the same command with python.exe, which does
            # have stderr, purely to capture the error and show it.
            Write-Host "  re-running with python.exe to capture the error..." -ForegroundColor DarkGray
            $errFile = Join-Path $env:TEMP "chatbot-start-error.txt"
            Start-Process -FilePath $Py -ArgumentList $argList -WorkingDirectory $Root `
                -WindowStyle Hidden -Wait -RedirectStandardError $errFile | Out-Null
            if ((Test-Path $errFile) -and (Get-Item $errFile).Length -gt 0) {
                Write-Host ""
                Get-Content $errFile | Select-Object -Last 25 | ForEach-Object {
                    Write-Host "    $_" -ForegroundColor Red
                }
                Write-Host ""
            } else {
                Write-Host "         no error output - check the log:  .\serve.ps1 logs" -ForegroundColor Yellow
            }
            exit 1
        }
        try {
            Invoke-RestMethod -Uri "$url/health" -TimeoutSec 2 | Out-Null
            Write-Host "  [ok] running in the background, pid $($proc.Id)" -ForegroundColor Green
            Write-Host "       $url" -ForegroundColor Cyan
            Write-Host "       logs:  .\serve.ps1 logs -Follow" -ForegroundColor DarkGray
            Write-Host "       stop:  .\serve.ps1 stop" -ForegroundColor DarkGray
            Start-Process $url
            return
        } catch { }
    }
    Write-Host "  [WARN] started (pid $($proc.Id)) but /health did not answer within 60s" -ForegroundColor Yellow
    Write-Host "         the RAG index may still be loading - check:  .\serve.ps1 logs" -ForegroundColor Yellow
}

function Stop-Server {
    $proc = Get-ServerProcess
    if (-not $proc) {
        Write-Host "  [stopped] nothing to stop" -ForegroundColor Yellow
        return
    }
    $id = $proc.Id
    Stop-Process -Id $id -Force
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Milliseconds 250
        if (-not (Get-Process -Id $id -ErrorAction SilentlyContinue)) { break }
    }
    Remove-Item $PidFile -ErrorAction SilentlyContinue
    Remove-Item $PortFile -ErrorAction SilentlyContinue
    Write-Host "  [ok] stopped (pid $id)" -ForegroundColor Green
}

Write-Host ""
switch ($Action) {
    "start"   { Start-Server }
    "stop"    { Stop-Server }
    "restart" { Stop-Server; Start-Sleep -Seconds 1; Start-Server }
    "status"  { Show-Status | Out-Null }
    "logs" {
        if (-not (Test-Path $LogFile)) {
            Write-Host "  no log file yet at $LogFile" -ForegroundColor Yellow
        } elseif ($Follow) {
            Write-Host "  watching $LogFile  (Ctrl+C to stop)" -ForegroundColor DarkGray
            Write-Host ""
            Get-Content $LogFile -Tail $Lines -Wait -Encoding UTF8
        } else {
            Get-Content $LogFile -Tail $Lines -Encoding UTF8
        }
    }
}
Write-Host ""

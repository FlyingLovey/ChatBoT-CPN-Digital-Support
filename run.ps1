#Requires -Version 5.1
<#
.SYNOPSIS
    Start the Digital Support Chatbot.

.DESCRIPTION
    Runs pre-flight checks, picks a free port, then starts uvicorn.
    Works from any directory - all paths are resolved from this script's location.

    NOTE: this file is deliberately ASCII-only. Thai text in a .ps1 renders as
    mojibake on consoles whose code page or font does not cover it, which made
    earlier versions look broken even when they ran fine.

.PARAMETER Port
    Port to listen on. Default 8000. If busy, the next free port is used.

.PARAMETER Reload
    Enable uvicorn auto-reload (for development). Off by default: it spawns a
    second process, which is how a stale listener ended up holding port 8000.

.PARAMETER NoBrowser
    Do not open the browser automatically.

.EXAMPLE
    .\run.ps1
.EXAMPLE
    .\run.ps1 -Port 8001 -Reload
#>
param(
    [int]$Port = 8000,
    [switch]$Reload,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Py = Join-Path $Root ".venv\Scripts\python.exe"

Write-Host ""
Write-Host "  Digital Support Chatbot" -ForegroundColor Cyan
Write-Host "  $Root" -ForegroundColor DarkGray
Write-Host ""

# --- 1. virtualenv -----------------------------------------------------------
if (-not (Test-Path $Py)) {
    Write-Host "  [FAIL] .venv not found" -ForegroundColor Red
    Write-Host "         python -m venv .venv" -ForegroundColor Yellow
    Write-Host "         .\.venv\Scripts\python.exe -m pip install -r requirements-rag.txt" -ForegroundColor Yellow
    Write-Host ""
    exit 1
}
Write-Host "  [ok]   virtualenv" -ForegroundColor Green

# --- 2. RAG index ------------------------------------------------------------
$IndexFile = Join-Path $Root "rag_index\chunks.jsonl"
if (Test-Path $IndexFile) {
    $nChunks = (Get-Content $IndexFile -ReadCount 0).Count
    Write-Host "  [ok]   RAG index ($nChunks chunks)" -ForegroundColor Green
} else {
    Write-Host "  [WARN] no RAG index - the bot will answer without the knowledge base" -ForegroundColor Yellow
    Write-Host "         .\.venv\Scripts\python.exe scripts\build_index.py" -ForegroundColor Yellow
}

# --- 3. LM Studio ------------------------------------------------------------
# Only matters for the "offline" mode, which is the default. Checked here so a
# forgotten Start-Server shows up now, not after the first question fails.
try {
    $models = (Invoke-RestMethod -Uri "http://127.0.0.1:1234/v1/models" -TimeoutSec 3).data
    Write-Host "  [ok]   LM Studio ($($models.Count) models)" -ForegroundColor Green
} catch {
    Write-Host "  [WARN] LM Studio not reachable on 127.0.0.1:1234" -ForegroundColor Yellow
    Write-Host "         Open LM Studio -> Developer tab -> Start Server" -ForegroundColor Yellow
    Write-Host "         Or switch the page to 'online' mode." -ForegroundColor Yellow
}

# --- 4. free port ------------------------------------------------------------
# A crashed process can leave a listening socket behind with no owner, which
# makes the browser talk to nothing. Stepping to the next free port avoids it.
function Test-PortBusy([int]$p) {
    # Try to bind the port ourselves - exactly what uvicorn is about to do.
    # More reliable than Get-NetTCPConnection, which is Windows-only and can be
    # unavailable in restricted environments, and it also catches sockets whose
    # owning process has died but whose listener is still registered.
    $listener = $null
    try {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $p)
        $listener.Start()
        return $false
    } catch {
        return $true
    } finally {
        if ($listener) { try { $listener.Stop() } catch { } }
    }
}

$chosen = $Port
while ((Test-PortBusy $chosen) -and ($chosen -lt $Port + 20)) {
    Write-Host "  [WARN] port $chosen is in use, trying $($chosen + 1)" -ForegroundColor Yellow
    $chosen++
}
if (Test-PortBusy $chosen) {
    Write-Host "  [FAIL] no free port between $Port and $chosen" -ForegroundColor Red
    exit 1
}

$Url = "http://127.0.0.1:$chosen"
Write-Host "  [ok]   port $chosen" -ForegroundColor Green
Write-Host ""
Write-Host "  $Url" -ForegroundColor Cyan
Write-Host "  Press Ctrl+C to stop." -ForegroundColor DarkGray
Write-Host ""

# --- 5. open the browser once the server answers -----------------------------
if (-not $NoBrowser) {
    Start-Job -ScriptBlock {
        param($u)
        for ($i = 0; $i -lt 60; $i++) {
            Start-Sleep -Milliseconds 500
            try {
                Invoke-WebRequest -Uri "$u/health" -TimeoutSec 2 -UseBasicParsing | Out-Null
                Start-Process $u
                return
            } catch { }
        }
    } -ArgumentList $Url | Out-Null
}

# --- 6. run ------------------------------------------------------------------
Set-Location $Root
$uvicornArgs = @("-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "$chosen")
if ($Reload) { $uvicornArgs += "--reload" }

# uvicorn writes its normal INFO log to stderr. Two rules matter here:
#
#   1. Do NOT pipe or redirect it. Windows PowerShell 5.1 wraps redirected
#      native stderr into ErrorRecord objects, and with ErrorActionPreference
#      set to Stop that becomes a terminating NativeCommandError - so ordinary
#      startup lines get reported as a script failure. Left alone, the stream
#      goes straight to the console and prints normally.
#   2. Relax ErrorActionPreference first, so a stderr write cannot abort us.
$ErrorActionPreference = "Continue"
& $Py @uvicornArgs

exit $LASTEXITCODE

#Requires -Version 5.1
<#
.SYNOPSIS
    Install the chatbot as a real Windows service using NSSM.

.DESCRIPTION
    serve.ps1 is enough for day-to-day use: no console window, survives closing
    your terminal. Use this script instead when you need the stronger guarantees
    of a Windows service:

      - starts on boot, before anyone logs in
      - Windows restarts it automatically if it crashes
      - managed from services.msc like any other service

    Trade-off: needs NSSM installed and an elevated (Administrator) PowerShell.

    Why NSSM rather than writing a service in Python: uvicorn's shutdown logic
    installs signal handlers, which only work on the main thread. Running it
    inside a pywin32 service class is a known source of
    "signal only works in main thread of the main interpreter" failures. NSSM
    sidesteps all of it by supervising an ordinary process from outside.

    Install NSSM first (any one of these):
        winget install NSSM.NSSM
        choco install nssm
        or download from https://nssm.cc and put nssm.exe on PATH

    ASCII-only on purpose - see the note in run.ps1.

.PARAMETER Action
    install | uninstall | start | stop | restart | status

.PARAMETER Port
    Port the service listens on. Default 8000. Used by 'install'.

.PARAMETER Name
    Service name. Default DigitalSupportChatbot.

.EXAMPLE
    .\service.ps1 install      # run as Administrator
.EXAMPLE
    .\service.ps1 status
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet("install", "uninstall", "start", "stop", "restart", "status")]
    [string]$Action = "status",

    [int]$Port = 8000,
    [string]$Name = "DigitalSupportChatbot"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Py = Join-Path $Root ".venv\Scripts\python.exe"
$ServeScript = Join-Path $Root "scripts\serve.py"
$LogDir = Join-Path $Root "logs"

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltinRole]::Administrator)
}

function Get-Nssm {
    $cmd = Get-Command nssm.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    Write-Host "  [FAIL] nssm.exe not found on PATH" -ForegroundColor Red
    Write-Host "         winget install NSSM.NSSM" -ForegroundColor Yellow
    Write-Host "         (or choco install nssm, or download from https://nssm.cc)" -ForegroundColor Yellow
    Write-Host "         Open a new terminal afterwards so PATH refreshes." -ForegroundColor Yellow
    exit 1
}

function Require-Admin {
    if (-not (Test-Admin)) {
        Write-Host "  [FAIL] this action needs an Administrator PowerShell" -ForegroundColor Red
        Write-Host "         right-click PowerShell -> Run as administrator" -ForegroundColor Yellow
        exit 1
    }
}

Write-Host ""
Write-Host "  Windows service: $Name" -ForegroundColor Cyan
Write-Host ""

switch ($Action) {

    "install" {
        Require-Admin
        $nssm = Get-Nssm
        if (-not (Test-Path $Py)) {
            Write-Host "  [FAIL] .venv not found at $Py" -ForegroundColor Red
            exit 1
        }
        if (Get-Service -Name $Name -ErrorAction SilentlyContinue) {
            Write-Host "  [WARN] the service already exists - uninstall it first" -ForegroundColor Yellow
            Write-Host "         .\service.ps1 uninstall" -ForegroundColor Yellow
            exit 1
        }
        New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

        # Set Application and AppParameters explicitly rather than passing them all
        # to `nssm install`. The project path contains spaces ("D:\Project IS\..."),
        # and letting them flow through argument joining is how the script path ends
        # up split in two. Quote the script path inside AppParameters ourselves.
        & $nssm install $Name $Py | Out-Null
        & $nssm set $Name Application $Py | Out-Null
        & $nssm set $Name AppParameters "`"$ServeScript`" --port $Port" | Out-Null
        & $nssm set $Name AppDirectory $Root | Out-Null
        & $nssm set $Name DisplayName "Digital Support Chatbot" | Out-Null
        & $nssm set $Name Description "RAG chatbot for IT/application support (IS2 project)" | Out-Null
        & $nssm set $Name Start SERVICE_AUTO_START | Out-Null
        # Restart on crash, but back off so a broken config does not spin forever
        & $nssm set $Name AppExit Default Restart | Out-Null
        & $nssm set $Name AppRestartDelay 5000 | Out-Null
        # serve.py already writes logs\server.log; these catch anything printed
        # before logging is configured (import errors, for example)
        & $nssm set $Name AppStdout (Join-Path $LogDir "service-out.log") | Out-Null
        & $nssm set $Name AppStderr (Join-Path $LogDir "service-err.log") | Out-Null
        & $nssm set $Name AppRotateFiles 1 | Out-Null
        & $nssm set $Name AppRotateBytes 5242880 | Out-Null

        Write-Host "  [ok] installed" -ForegroundColor Green
        Write-Host "       port    : $Port" -ForegroundColor DarkGray
        Write-Host "       autostart: yes (starts at boot)" -ForegroundColor DarkGray
        Write-Host ""
        Write-Host "  start it with:  .\service.ps1 start" -ForegroundColor Cyan
    }

    "uninstall" {
        Require-Admin
        $nssm = Get-Nssm
        if (-not (Get-Service -Name $Name -ErrorAction SilentlyContinue)) {
            Write-Host "  [WARN] no such service" -ForegroundColor Yellow
            exit 0
        }
        & $nssm stop $Name | Out-Null
        & $nssm remove $Name confirm | Out-Null
        Write-Host "  [ok] removed" -ForegroundColor Green
    }

    "start" {
        Require-Admin
        Start-Service -Name $Name
        Start-Sleep -Seconds 2
        Write-Host "  [ok] started - it may take a minute to load the RAG index" -ForegroundColor Green
        Write-Host "       http://127.0.0.1:$Port" -ForegroundColor Cyan
        Write-Host "       logs: Get-Content logs\server.log -Tail 40 -Wait" -ForegroundColor DarkGray
    }

    "stop" {
        Require-Admin
        Stop-Service -Name $Name
        Write-Host "  [ok] stopped" -ForegroundColor Green
    }

    "restart" {
        Require-Admin
        Restart-Service -Name $Name
        Write-Host "  [ok] restarted" -ForegroundColor Green
    }

    "status" {
        $svc = Get-Service -Name $Name -ErrorAction SilentlyContinue
        if (-not $svc) {
            Write-Host "  [not installed]" -ForegroundColor Yellow
            Write-Host "  install with (as Administrator):  .\service.ps1 install" -ForegroundColor DarkGray
            Write-Host "  or just run in the background:    .\serve.ps1 start" -ForegroundColor DarkGray
            break
        }
        $colour = if ($svc.Status -eq "Running") { "Green" } else { "Yellow" }
        Write-Host "  status : $($svc.Status)" -ForegroundColor $colour
        Write-Host "  startup: $($svc.StartType)" -ForegroundColor DarkGray
        if ($svc.Status -eq "Running") {
            try {
                $h = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 3
                $rag = if ($h.rag) { "ready" } else { "not ready" }
                $dbs = if ($h.db) { "ready" } else { "off" }
                Write-Host "  rag: $rag   db: $dbs" -ForegroundColor DarkGray
            } catch {
                Write-Host "  (health check did not answer on port $Port)" -ForegroundColor Yellow
            }
        }
    }
}
Write-Host ""

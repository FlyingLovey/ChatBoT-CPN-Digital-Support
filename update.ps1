#Requires -Version 5.1
<#
.SYNOPSIS
    Rebuild the RAG index after adding or changing documents, then start the app.

.DESCRIPTION
    Run this after you drop new manuals into
        Model AI\lm_studio_eval\knowledge_base\Applications\<system>\

    The index is rebuilt from scratch every time (there is no incremental mode),
    so this takes the same time as the first build. The embedding model is
    already cached, so nothing is downloaded again.

    Before/after counts are printed so you can confirm the new documents landed.

    ASCII-only on purpose - see the note in run.ps1.

.PARAMETER NoRun
    Rebuild only. Do not start the app afterwards.

.PARAMETER Port
    Passed through to run.ps1.

.EXAMPLE
    .\update.ps1
.EXAMPLE
    .\update.ps1 -NoRun
#>
param(
    [switch]$NoRun,
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Py = Join-Path $Root ".venv\Scripts\python.exe"
$IndexFile = Join-Path $Root "rag_index\chunks.jsonl"
$MetaFile = Join-Path $Root "rag_index\meta.json"

function Get-IndexStats {
    if (-not (Test-Path $MetaFile)) { return $null }
    try {
        $m = Get-Content $MetaFile -Raw | ConvertFrom-Json
        return [pscustomobject]@{
            Chunks  = $m.n_chunks
            Docs    = $m.n_docs
            Systems = $m.systems.Count
        }
    } catch { return $null }
}

Write-Host ""
Write-Host "  Update knowledge base" -ForegroundColor Cyan
Write-Host "  $Root" -ForegroundColor DarkGray
Write-Host ""

if (-not (Test-Path $Py)) {
    Write-Host "  [FAIL] .venv not found" -ForegroundColor Red
    Write-Host "         python -m venv .venv" -ForegroundColor Yellow
    Write-Host "         .\.venv\Scripts\python.exe -m pip install -r requirements-rag.txt" -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

$before = Get-IndexStats
if ($before) {
    Write-Host "  before : $($before.Chunks) chunks / $($before.Docs) docs / $($before.Systems) systems" -ForegroundColor DarkGray
} else {
    Write-Host "  before : no index yet" -ForegroundColor DarkGray
}
Write-Host ""
Write-Host "  Rebuilding - this takes a while, do not close this window." -ForegroundColor Yellow
Write-Host ""

# The build script writes progress to stderr. Do not pipe it: Windows PowerShell
# turns redirected native stderr into terminating NativeCommandError under
# ErrorActionPreference=Stop, which would abort a perfectly healthy build.
$ErrorActionPreference = "Continue"
& $Py (Join-Path $Root "scripts\build_index.py")
$buildExit = $LASTEXITCODE
$ErrorActionPreference = "Stop"

Write-Host ""
if ($buildExit -ne 0) {
    Write-Host "  [FAIL] build_index.py exited with code $buildExit" -ForegroundColor Red
    Write-Host "         The old index (if any) is left untouched." -ForegroundColor Yellow
    Write-Host ""
    exit $buildExit
}

if (-not (Test-Path $IndexFile)) {
    Write-Host "  [FAIL] build finished but rag_index\chunks.jsonl is missing" -ForegroundColor Red
    Write-Host ""
    exit 1
}

$after = Get-IndexStats
if ($after) {
    Write-Host "  after  : $($after.Chunks) chunks / $($after.Docs) docs / $($after.Systems) systems" -ForegroundColor Green
    if ($before) {
        $dc = $after.Chunks - $before.Chunks
        $dd = $after.Docs - $before.Docs
        $ds = $after.Systems - $before.Systems
        $sign = { param($n) if ($n -ge 0) { "+$n" } else { "$n" } }
        $delta = "  change : $(& $sign $dc) chunks / $(& $sign $dd) docs / $(& $sign $ds) systems"
        if ($dc -eq 0 -and $dd -eq 0 -and $ds -eq 0) {
            Write-Host $delta -ForegroundColor Yellow
            Write-Host "           Nothing changed. If you added files, check that they are" -ForegroundColor Yellow
            Write-Host "           .pdf/.docx/.xlsx/.msg/.txt, are not byte-identical copies of" -ForegroundColor Yellow
            Write-Host "           existing files, and that scanned PDFs have a text layer." -ForegroundColor Yellow
        } else {
            Write-Host $delta -ForegroundColor Green
        }
    }
}
Write-Host ""

if ($NoRun) {
    Write-Host "  Done. Start the app with:  .\run.ps1" -ForegroundColor Cyan
    Write-Host ""
    exit 0
}

Write-Host "  Starting the app..." -ForegroundColor Cyan
Write-Host ""
& (Join-Path $Root "run.ps1") -Port $Port

# เปิดแชทบอท — ใช้แทนการพิมพ์ cd / activate / uvicorn ทีละบรรทัด
#
# วิธีใช้: คลิกขวาที่ไฟล์นี้ -> Run with PowerShell
#         หรือสั่งจากเทอร์มินัลไหนก็ได้ ไม่ต้อง cd มาก่อน:  D:\...\run.ps1
#
# สคริปต์นี้อ้าง path จากตำแหน่งของตัวเองเสมอ จึงรันจากโฟลเดอร์ไหนก็ได้ และเรียก
# python ใน .venv ตรงๆ โดยไม่ต้อง activate — ตัดปัญหา PATH ที่เจอบ่อยออกไปทั้งหมด

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Py = Join-Path $Root ".venv\Scripts\python.exe"

Write-Host "โฟลเดอร์โปรเจกต์: $Root" -ForegroundColor DarkGray

if (-not (Test-Path $Py)) {
    Write-Host "[!] ไม่พบ .venv — สร้างก่อนด้วย:" -ForegroundColor Red
    Write-Host "    python -m venv .venv" -ForegroundColor Yellow
    Write-Host "    .\.venv\Scripts\python.exe -m pip install -r requirements-rag.txt" -ForegroundColor Yellow
    exit 1
}

# เตือนล่วงหน้าถ้ายังไม่มีดัชนี จะได้ไม่เปิดมาเจอป้ายแดงแล้วงงว่าทำไม
if (-not (Test-Path (Join-Path $Root "rag_index\chunks.jsonl"))) {
    Write-Host "[!] ยังไม่มีดัชนี RAG — แชทบอทจะทำงานโดยไม่ใช้ฐานความรู้" -ForegroundColor Yellow
    Write-Host "    สร้างก่อนด้วย: .\.venv\Scripts\python.exe scripts\build_index.py" -ForegroundColor Yellow
    Write-Host ""
}

# เตือนถ้า LM Studio ยังไม่เปิด (โหมดเริ่มต้นคือ offline ซึ่งต้องใช้ LM Studio)
try {
    Invoke-RestMethod -Uri "http://127.0.0.1:1234/v1/models" -TimeoutSec 2 | Out-Null
    Write-Host "[ok] LM Studio พร้อมใช้งาน" -ForegroundColor Green
} catch {
    Write-Host "[!] ต่อ LM Studio ไม่ได้ (http://127.0.0.1:1234)" -ForegroundColor Yellow
    Write-Host "    เปิด LM Studio -> แท็บ Developer -> Start Server" -ForegroundColor Yellow
    Write-Host "    หรือสลับไปโหมด 'ออนไลน์' บนหน้าเว็บแทน" -ForegroundColor Yellow
    Write-Host ""
}

Write-Host "เปิดเซิร์ฟเวอร์ที่ http://127.0.0.1:8000  (กด Ctrl+C เพื่อหยุด)" -ForegroundColor Cyan
Write-Host ""

Set-Location $Root
& $Py -m uvicorn main:app --reload

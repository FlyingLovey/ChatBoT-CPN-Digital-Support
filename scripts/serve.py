"""เปิดเซิร์ฟเวอร์แบบเขียน log ลงไฟล์ สำหรับรันเบื้องหลังโดยไม่มีหน้าต่างคอนโซล

ทำไมต้องมีไฟล์นี้แยกจากการเรียก `uvicorn main:app` ตรงๆ:

ถ้ารันด้วย pythonw.exe (ตัวที่ไม่สร้างหน้าต่างคอนโซล) จะไม่มี stdout/stderr ให้เขียน
log เลย ข้อความทั้งหมดจะหายไปเฉยๆ ไฟล์นี้จึงตั้ง logging ให้เขียนลงไฟล์ก่อน แล้วค่อย
เรียก uvicorn ด้วย log_config=None เพื่อไม่ให้ uvicorn ไปตั้ง handler ทับของเรา

วิธีรัน (ปกติเรียกผ่าน serve.ps1 ไม่ได้เรียกเอง):
    pythonw.exe scripts\\serve.py --port 8000
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def setup_logging(log_file: Path, level: str = "INFO") -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for h in list(root.handlers):        # ล้าง handler เดิมกันข้อความซ้ำ
        root.removeHandler(h)

    # หมุนไฟล์เมื่อโตเกิน 5 MB เก็บย้อนหลัง 5 ไฟล์ — กันไฟล์โตไม่จำกัดเมื่อรันยาวๆ
    handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    ))
    root.addHandler(handler)

    # uvicorn ตั้ง logger ของตัวเองไม่ให้ส่งต่อขึ้น root ต้องเปิด propagate เอง
    # ไม่งั้น log การเข้าถึงและ log ตอนสตาร์ตจะไม่เข้าไฟล์
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True


def main() -> None:
    ap = argparse.ArgumentParser(description="เปิดเซิร์ฟเวอร์แบบเขียน log ลงไฟล์")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--log-file", type=Path, default=PROJECT_ROOT / "logs" / "server.log")
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    setup_logging(args.log_file, args.log_level)
    log = logging.getLogger("serve")
    log.info("=" * 70)
    log.info("เริ่มเซิร์ฟเวอร์ที่ http://%s:%s", args.host, args.port)
    log.info("โฟลเดอร์โปรเจกต์: %s", PROJECT_ROOT)

    try:
        import uvicorn
        from main import app

        # log_config=None สำคัญมาก: ถ้าไม่ใส่ uvicorn จะตั้ง logging ของตัวเองทับ
        # ทำให้ handler ที่ชี้ไปไฟล์หายไป แล้ว log จะหายหมดเพราะ pythonw ไม่มีคอนโซล
        uvicorn.run(app, host=args.host, port=args.port, log_config=None)
    except Exception:
        log.exception("เซิร์ฟเวอร์หยุดทำงานเพราะข้อผิดพลาด")
        raise
    finally:
        # หมายเหตุ: บรรทัดนี้อาจไม่ได้เขียนเมื่อถูกสั่งปิดด้วยสัญญาณ (เช่น Stop-Process)
        # เพราะ uvicorn ปิดโปรเซสจากใน signal handler ของตัวเองก่อน — ไม่เป็นไร
        # เพราะ uvicorn เขียน "Finished server process" ให้อยู่แล้ว
        log.info("เซิร์ฟเวอร์ปิดแล้ว")


if __name__ == "__main__":
    main()

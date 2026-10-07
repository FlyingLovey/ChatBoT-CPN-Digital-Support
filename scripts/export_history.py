"""ส่งออกประวัติคำถาม-คำตอบเป็น CSV ไว้วิเคราะห์ต่อใน Excel หรือ pandas

วิธีรัน (จากโฟลเดอร์โปรเจกต์):
    .\.venv\Scripts\python.exe scripts\export_history.py
    .\.venv\Scripts\python.exe scripts\export_history.py --out D:\history.csv --limit 5000

ไฟล์ที่ได้เป็น UTF-8 with BOM เพื่อให้ Excel บน Windows เปิดแล้วภาษาไทยไม่เพี้ยน
(Excel เดาว่าเป็น ANSI ถ้าไม่มี BOM)
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

FIELDS = ["id", "created_at", "session_id", "question", "answer", "mode", "model",
          "system_selected", "system_used", "rag_used", "n_sources", "abstained",
          "latency_ms", "error"]


def main() -> None:
    ap = argparse.ArgumentParser(description="ส่งออกประวัติคำถาม-คำตอบเป็น CSV")
    ap.add_argument("--out", type=Path, default=PROJECT_ROOT / "data" / "history.csv")
    ap.add_argument("--limit", type=int, default=10000, help="จำนวนรอบล่าสุดที่ดึง")
    ap.add_argument("--session", type=str, default=None, help="กรองเฉพาะ session เดียว")
    args = ap.parse_args()

    from config import settings
    from services import db

    if not db.init_db(settings.database_url):
        raise SystemExit("[fail] เชื่อมต่อฐานข้อมูลไม่ได้ ตรวจสอบค่า DATABASE_URL ใน .env")

    rows = db.recent_interactions(limit=args.limit, session_id=args.session)
    if not rows:
        print("[warn] ยังไม่มีประวัติในฐานข้อมูล — ลองใช้งานแชทบอทสักสองสามคำถามก่อน")
        return

    args.out.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig = UTF-8 + BOM ให้ Excel อ่านภาษาไทยถูก
    with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    n = len(rows)
    n_abstain = sum(1 for r in rows if r["abstained"])
    lat = [r["latency_ms"] for r in rows if r["latency_ms"]]
    lat.sort()

    print(f"\nบันทึกแล้ว {n} รอบ -> {args.out}")
    print(f"  ปฏิเสธตอบ      : {n_abstain} ({n_abstain / n * 100:.1f}%)")
    if lat:
        print(f"  เวลาตอบ (มัธยฐาน): {lat[len(lat) // 2] / 1000:.1f} วินาที")
        print(f"  ช้าสุด          : {lat[-1] / 1000:.1f} วินาที")
    print(f"  จำนวน session   : {len({r['session_id'] for r in rows})}")

    top = Counter(r["system_used"] for r in rows if r["system_used"]).most_common(5)
    if top:
        print("\n  ระบบงานที่ถูกถามบ่อยสุด:")
        for name, c in top:
            print(f"    {c:4d}  {name}")


if __name__ == "__main__":
    main()

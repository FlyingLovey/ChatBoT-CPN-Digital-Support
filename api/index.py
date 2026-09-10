"""จุดเข้า (entrypoint) ที่ Vercel เรียกใช้จริง

Vercel's Python runtime มองหาไฟล์ในโฟลเดอร์ api/ แล้วรันแต่ละไฟล์เป็น serverless
function หนึ่งตัว ไฟล์นี้แค่ import FastAPI app ตัวจริงจาก main.py (ที่อยู่นอก
โฟลเดอร์ api/) มาเปิดใช้งาน — โค้ดแอปจริงทั้งหมดยังอยู่ที่ main.py/routers/services
เหมือนตอนรันในเครื่องทุกประการ ไม่ต้องเขียนซ้ำ
"""

import sys
from pathlib import Path

# เพิ่ม root ของโปรเจกต์ (โฟลเดอร์ที่อยู่เหนือ api/) เข้าไปใน sys.path ก่อน
# ไม่งั้น "from main import app" ด้านล่างจะหา main.py ไม่เจอ
sys.path.append(str(Path(__file__).resolve().parent.parent))

from main import app  # noqa: E402

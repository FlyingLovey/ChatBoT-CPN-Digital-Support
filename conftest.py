"""ทำให้ pytest มองเห็นแพ็กเกจของโปรเจกต์ (services, routers, config) ตอนรันจากรากโปรเจกต์

ไฟล์นี้ว่างโดยตั้งใจ — การมี conftest.py อยู่ที่รากทำให้ pytest ใส่โฟลเดอร์นี้เข้า sys.path
ให้เอง ไฟล์ทดสอบใน tests/ จึง import ได้ตรง ๆ ว่า `from services import guard`
"""

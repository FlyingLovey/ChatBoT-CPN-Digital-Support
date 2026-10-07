# -*- coding: utf-8 -*-
"""init_postgres.py — ตรวจและเตรียม PostgreSQL ให้พร้อมสำหรับแชทบอท

ทำอะไร (ทำซ้ำได้ ไม่พังถ้ามีอยู่แล้ว)
  1) ต่อเข้าฐานข้อมูล postgres ด้วยบัญชีผู้ดูแล (ค่าเริ่มต้นคือ postgres)
  2) สร้าง role ของแอป ถ้ายังไม่มี  — ชื่อและรหัสผ่านอ่านจาก POSTGRES_USER/PASSWORD ใน .env
  3) สร้างฐานข้อมูลของแอป ถ้ายังไม่มี — ชื่ออ่านจาก POSTGRES_DB
  4) ต่อด้วยบัญชีของแอปจริง แล้วให้ SQLAlchemy สร้างตารางให้ครบ
  5) สรุปว่าตารางมีอะไรบ้าง มีกี่แถว

วิธีใช้
    python scripts/init_postgres.py
    python scripts/init_postgres.py --admin-user postgres --admin-password "รหัสผ่านตอนติดตั้ง"

ถ้าไม่ใส่ --admin-password จะถามตอนรัน (ไม่แสดงตัวอักษร) และไม่เก็บลงไฟล์ใดทั้งสิ้น
"""
from __future__ import annotations

import argparse
import getpass
import io
import sys
from pathlib import Path

# ให้พิมพ์ภาษาไทยบนคอนโซล Windows ได้โดยไม่ระเบิดด้วย UnicodeEncodeError
for _name in ("stdout", "stderr"):
    _stream = getattr(sys, _name, None)
    if _stream is None:
        continue
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        try:
            setattr(sys, _name, io.TextIOWrapper(
                _stream.buffer, encoding="utf-8", errors="replace", line_buffering=True))
        except Exception:
            pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings  # noqa: E402
from services import db as appdb  # noqa: E402


def _connect_admin(host: str, port: int, user: str, password: str, dbname: str = "postgres"):
    import psycopg
    return psycopg.connect(host=host, port=port, user=user,
                           password=password, dbname=dbname, connect_timeout=5)


def main() -> int:
    ap = argparse.ArgumentParser(description="เตรียม PostgreSQL ให้พร้อมสำหรับแชทบอท")
    ap.add_argument("--admin-user", default="postgres",
                    help="บัญชีผู้ดูแลของ PostgreSQL (ค่าเริ่มต้น postgres)")
    ap.add_argument("--admin-password", default=None,
                    help="รหัสผ่านของบัญชีผู้ดูแล ถ้าไม่ใส่จะถามตอนรัน")
    ap.add_argument("--admin-db", default="postgres",
                    help="ฐานข้อมูลที่ใช้ต่อตอนสร้าง (ค่าเริ่มต้น postgres)")
    args = ap.parse_args()

    host = settings.POSTGRES_HOST
    port = settings.POSTGRES_PORT
    dbname = settings.POSTGRES_DB
    appuser = settings.POSTGRES_USER
    apppass = settings.POSTGRES_PASSWORD

    print("ค่าที่อ่านได้จาก .env")
    print(f"  เซิร์ฟเวอร์   : {host}:{port}")
    print(f"  ฐานข้อมูล    : {dbname}")
    print(f"  ผู้ใช้ของแอป  : {appuser}")
    print(f"  รหัสผ่าน     : {'(ตั้งไว้แล้ว)' if apppass else '(ยังไม่ได้ตั้ง)'}")
    print()

    if settings.DATABASE_URL and not settings.DATABASE_URL.startswith("postgresql"):
        print("[!] DATABASE_URL ใน .env ไม่ได้ชี้ไปที่ PostgreSQL — สคริปต์นี้ใช้ไม่ได้")
        print(f"    ค่าปัจจุบัน: {settings.DATABASE_URL}")
        return 2

    if not apppass:
        print("[!] ยังไม่ได้ตั้ง POSTGRES_PASSWORD ใน .env")
        print("    ตั้งรหัสผ่านของผู้ใช้แอปก่อน แล้วรันสคริปต์นี้ใหม่")
        return 2

    try:
        import psycopg  # noqa: F401
    except ImportError:
        print("[!] ยังไม่ได้ติดตั้งไดรเวอร์ PostgreSQL")
        print("    ติดตั้งด้วย:  pip install \"psycopg[binary]>=3.2\"")
        print("    หรือ:        pip install -r requirements.txt")
        return 2

    admin_password = args.admin_password
    if admin_password is None:
        admin_password = getpass.getpass(
            f"รหัสผ่านของ {args.admin_user} (ที่ตั้งไว้ตอนติดตั้ง PostgreSQL): ")

    # ---------------- 1) ต่อด้วยบัญชีผู้ดูแล ----------------
    try:
        conn = _connect_admin(host, port, args.admin_user, admin_password, args.admin_db)
    except Exception as exc:  # noqa: BLE001
        print(f"\n[FAIL] ต่อเข้า PostgreSQL ไม่ได้: {type(exc).__name__}: {exc}")
        print("  ตรวจสอบ:")
        print("   - ติดตั้ง PostgreSQL แล้วหรือยัง และ service ทำงานอยู่ไหม")
        print("     (Windows: เปิด services.msc หา postgresql-x64-… ต้องขึ้น Running)")
        print(f"   - พอร์ต {port} ถูกต้องไหม")
        print("   - รหัสผ่านของบัญชีผู้ดูแลถูกไหม")
        return 1
    conn.autocommit = True   # CREATE DATABASE รันใน transaction ไม่ได้

    # CREATE ROLE / CREATE DATABASE ไม่รับ bind parameter (%s) ทั้งชื่อและรหัสผ่าน
    # ต้องประกอบเป็นข้อความ SQL ตรงๆ จึงใช้ psycopg.sql ช่วย quote ให้ถูกต้องแทนการ
    # ต่อสตริงเอง — Identifier ใส่ " ให้ชื่อ, Literal ใส่ ' และ escape ให้ค่า
    from psycopg import sql

    ident_user = sql.Identifier(appuser)
    ident_db = sql.Identifier(dbname)

    with conn.cursor() as cur:
        # ---------------- 2) role ของแอป ----------------
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (appuser,))
        if cur.fetchone():
            print(f"[ok]  มีผู้ใช้ {appuser!r} อยู่แล้ว — ตั้งรหัสผ่านให้ตรงกับ .env")
            cur.execute(sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                ident_user, sql.Literal(apppass)))
        else:
            cur.execute(sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD {}").format(
                ident_user, sql.Literal(apppass)))
            print(f"[new] สร้างผู้ใช้ {appuser!r} แล้ว")

        # ---------------- 3) ฐานข้อมูลของแอป ----------------
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
        if cur.fetchone():
            print(f"[ok]  มีฐานข้อมูล {dbname!r} อยู่แล้ว")
        else:
            # ระบุ encoding ให้ชัดเป็น UTF8 เพราะคำถามของผู้ใช้เป็นภาษาไทย
            # TEMPLATE template0 จำเป็นเมื่อกำหนด encoding เองบนเครื่องที่ template1
            # ถูกสร้างด้วย encoding อื่น (เครื่อง Windows ภาษาไทยเจอบ่อย)
            cur.execute(sql.SQL(
                "CREATE DATABASE {} OWNER {} ENCODING 'UTF8' TEMPLATE template0").format(
                ident_db, ident_user))
            print(f"[new] สร้างฐานข้อมูล {dbname!r} แล้ว (UTF8)")
    conn.close()

    # ให้สิทธิ์บน schema public — ตั้งแต่ PostgreSQL 15 ผู้ใช้ทั่วไปสร้างตารางใน
    # public ไม่ได้อีกต่อไปถ้าไม่ได้ให้สิทธิ์ไว้ ซึ่งเป็นสาเหตุของ "permission denied
    # for schema public" ที่เจอกันบ่อยเวลาอัปเกรดจากเวอร์ชันเก่า
    try:
        conn = _connect_admin(host, port, args.admin_user, admin_password, dbname)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql.SQL("GRANT ALL ON SCHEMA public TO {}").format(ident_user))
            cur.execute(sql.SQL("ALTER SCHEMA public OWNER TO {}").format(ident_user))
        conn.close()
        print(f"[ok]  ให้สิทธิ์ schema public กับ {appuser!r} แล้ว")
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] ตั้งสิทธิ์ schema public ไม่สำเร็จ: {type(exc).__name__}: {exc}")

    # ---------------- 4) สร้างตารางด้วยโค้ดของแอปเอง ----------------
    print("\nสร้างตารางด้วย services/db.py (ชุดเดียวกับที่แอปใช้จริง) ...")
    ok = appdb.init_db(
        settings.database_url,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_recycle=settings.DB_POOL_RECYCLE,
        connect_timeout=settings.DB_CONNECT_TIMEOUT,
        sslmode=settings.POSTGRES_SSLMODE or None,
    )
    if not ok:
        print("[FAIL] แอปต่อฐานข้อมูลไม่สำเร็จ ดูข้อความ error ด้านบน")
        return 1

    st = appdb.status()
    print("\n[DONE] ฐานข้อมูลพร้อมใช้งาน")
    print(f"  dialect        : {st.get('dialect')}")
    print(f"  server_version : {st.get('server_version')}")
    print(f"  url            : {st.get('url')}")
    print(f"  chat_sessions  : {st.get('n_sessions')} แถว")
    print(f"  interactions   : {st.get('n_interactions')} แถว")
    print("\nเปิดแอปได้เลย:  .\\run.ps1   (หรือ  python -m uvicorn main:app --reload)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

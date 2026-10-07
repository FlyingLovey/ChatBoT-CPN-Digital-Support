"""เก็บประวัติคำถาม-คำตอบลงฐานข้อมูล

ทำไมต้องมี: เดิมประวัติบทสนทนาอยู่ใน dict ในหน่วยความจำ (`conversation_store`)
ซึ่งหายทุกครั้งที่รีสตาร์ท ใช้วิเคราะห์ย้อนหลังไม่ได้เลย โมดูลนี้เก็บทุกรอบถาม-ตอบ
ลงฐานข้อมูลจริง เพื่อสองเรื่อง

  1. บทสนทนาไม่หายเมื่อรีสตาร์ทแอป (โหลดกลับจากฐานข้อมูลด้วย session_id เดิม)
  2. มีข้อมูลไว้วิเคราะห์ว่าผู้ใช้ถามอะไรบ้าง ระบบไหนถูกถามมากสุด ปฏิเสธตอบกี่ครั้ง
     ตอบช้าแค่ไหน ซึ่งเป็นข้อมูลที่งานวิจัยต่อยอดต้องใช้ และเก็บได้เฉพาะจากการใช้งานจริง

ฐานข้อมูลหลักคือ PostgreSQL: เดิมใช้ SQLite ซึ่งเป็นไฟล์เดียวในเครื่อง พอต้องให้ผู้ใช้
หลายคนเข้าพร้อมกันและให้ทีมอื่นดึงข้อมูลไปวิเคราะห์ ไฟล์เดียวเริ่มเป็นคอขวด (เขียนพร้อมกัน
ได้ทีละรายการ และแชร์ข้ามเครื่องไม่ได้) จึงย้ายมาเป็น PostgreSQL

เขียนด้วย SQLAlchemy ทั้งหมด ตัวโค้ดที่ query จึงไม่ผูกกับฐานข้อมูลใดเป็นพิเศษ ส่วนที่
ต่างกันจริงๆ มีแค่ตอนสร้าง engine (ดู init_db) — ยังเปิดทาง sqlite:// ไว้สำหรับทดสอบ
เร็วๆ ในเครื่องที่ยังไม่ได้ติดตั้ง PostgreSQL

หลักสำคัญ: การบันทึกต้อง "ล้มแล้วไม่พาคำตอบล้มไปด้วย" ทุกฟังก์ชันในนี้จับ exception
เองทั้งหมด ถ้าเขียนฐานข้อมูลไม่ได้ ผู้ใช้ต้องยังได้รับคำตอบตามปกติ
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_engine = None
_Session = None
_ready = False
_last_error: str | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ============================================================================
# ตาราง
# ============================================================================

def _build_models():
    """นิยามตารางแบบ lazy (เรียกตอน init_db) เพื่อให้แอปยังรันได้แม้ไม่ได้ติดตั้ง SQLAlchemy

    ใช้รูปแบบ Column() แทน Mapped[...] ของ SQLAlchemy 2 โดยตั้งใจ เพราะไฟล์นี้เปิด
    `from __future__ import annotations` ทำให้ annotation กลายเป็นสตริง ซึ่ง SQLAlchemy
    ต้องแปลงกลับโดยมองหาชื่อชนิดข้อมูลใน globals ของโมดูล — แต่เรา import ไว้ในฟังก์ชัน
    มันจึงหาไม่เจอและพังด้วย MappedAnnotationError ส่วน Column() ไม่พึ่ง annotation เลย
    """
    from sqlalchemy import (Boolean, Column, DateTime, Index, Integer, String,
                            Text)
    from sqlalchemy.orm import declarative_base

    Base = declarative_base()

    class ChatSession(Base):
        """หนึ่งแถวต่อหนึ่ง session (หนึ่งเบราว์เซอร์ที่เข้ามาคุย)"""
        __tablename__ = "chat_sessions"

        session_id = Column(String(64), primary_key=True)
        # timezone=True สำคัญกับ PostgreSQL: _utcnow() คืนค่าที่ติด tzinfo มาด้วย
        # ถ้าประกาศเป็น DateTime เฉยๆ คอลัมน์จะเป็น timestamp without time zone แล้ว
        # PostgreSQL จะตัด tz ทิ้ง ทำให้เวลาที่อ่านกลับมาเพี้ยนตาม timezone ของเครื่อง
        created_at = Column(DateTime(timezone=True), default=_utcnow)
        last_active_at = Column(DateTime(timezone=True), default=_utcnow)
        n_interactions = Column(Integer, default=0)

    class Interaction(Base):
        """หนึ่งแถวต่อหนึ่งรอบถาม-ตอบ เก็บทั้งคำถาม คำตอบ และบริบทที่ใช้ตัดสินใจ

        เก็บ 'ทั้งรอบ' ไว้ในแถวเดียวแทนที่จะแยกเป็นตาราง messages เพราะคำถามที่อยากรู้
        ตอนวิเคราะห์เป็นคำถามระดับรอบทั้งหมด เช่น 'คำถามเรื่อง SAP ถูกปฏิเสธตอบกี่ %'
        ซึ่งถ้าแยกตารางต้อง join กันทุกครั้ง
        """
        __tablename__ = "interactions"

        id = Column(Integer, primary_key=True, autoincrement=True)
        session_id = Column(String(64), index=True, nullable=False)
        created_at = Column(DateTime(timezone=True), default=_utcnow, index=True)

        question = Column(Text, nullable=False)
        answer = Column(Text, default="")

        # ผู้ใช้เลือกโมเดลไหน ผ่านช่องทางไหน
        mode = Column(String(16))
        model = Column(String(128))

        # ระบบงานที่ผู้ใช้ "เลือกเอง" จาก dropdown (ว่าง = ให้ระบบเดา)
        system_selected = Column(String(128), index=True)
        # ระบบงานที่ถูกใช้กรองจริง (มาจากที่เลือก หรือจากการตรวจจับในข้อความ)
        system_used = Column(String(128), index=True)

        rag_used = Column(Boolean, default=False)
        n_sources = Column(Integer, default=0)
        # เก็บเป็น JSON เพราะจำนวนแหล่งอ้างอิงไม่คงที่ และใช้ดูย้อนหลังเป็นหลัก
        # ไม่ได้เอามา query ทีละฟิลด์
        sources_json = Column(Text)

        # ตอบว่า "ไม่พบคำตอบในบริบท" หรือไม่ — ตัวชี้วัดหลักตัวหนึ่งในสารนิพนธ์
        abstained = Column(Boolean, default=False, index=True)
        latency_ms = Column(Integer)
        error = Column(Text)

    Index("ix_interactions_session_created",
          Interaction.session_id, Interaction.created_at)
    return Base, ChatSession, Interaction


Base = ChatSession = Interaction = None  # เติมค่าจริงตอน init_db()


# ============================================================================
# เริ่มต้นระบบ
# ============================================================================

def init_db(
    database_url: str,
    *,
    pool_size: int = 5,
    max_overflow: int = 5,
    pool_recycle: int = 1800,
    connect_timeout: int = 5,
    sslmode: str | None = None,
) -> bool:
    """เปิดการเชื่อมต่อและสร้างตารางถ้ายังไม่มี คืน True เมื่อพร้อมใช้งาน

    เรียกซ้ำได้ ถ้าเคยสำเร็จแล้วจะไม่ทำอะไรอีก พารามิเตอร์พูลใช้เฉพาะกับ PostgreSQL
    (SQLite เป็นไฟล์เดียว ไม่มีพูลให้ตั้ง)
    """
    global _engine, _Session, _ready, _last_error, Base, ChatSession, Interaction
    if _ready:
        return True
    try:
        from sqlalchemy import create_engine, event
        from sqlalchemy.orm import sessionmaker

        is_sqlite = database_url.startswith("sqlite")
        is_postgres = database_url.startswith("postgresql")

        if is_sqlite:
            # สร้างโฟลเดอร์ปลายทางให้ก่อน ไม่งั้น SQLite จะ error ว่าเปิดไฟล์ไม่ได้
            path = database_url.split("///", 1)[-1]
            if path and path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)

        if is_postgres:
            # PostgreSQL เปิด connection จริงต่อหนึ่งตัวในพูล ต่างจาก SQLite ที่เปิดไฟล์
            # จึงต้องกำหนดขนาดพูลและ timeout เอง ไม่ปล่อยค่าเริ่มต้น
            #   connect_timeout  กันไม่ให้คำตอบของผู้ใช้ค้างรอ ถ้าเซิร์ฟเวอร์ไม่ตอบ
            #   application_name ทำให้เห็นใน pg_stat_activity ว่า connection มาจากแอปนี้
            engine_kwargs = dict(
                pool_size=pool_size,
                max_overflow=max_overflow,
                pool_recycle=pool_recycle,
                connect_args={
                    "connect_timeout": connect_timeout,
                    "application_name": "cpn-it-support-chatbot",
                    **({"sslmode": sslmode} if sslmode else {}),
                },
            )
        elif is_sqlite:
            # SQLite ปิดกั้นการใช้ connection ข้ามเธรดโดยค่าเริ่มต้น แต่เราเขียนจาก
            # เธรดพื้นหลังของ FastAPI จึงต้องปลดล็อกข้อนี้ (SQLAlchemy จัดคิวให้อยู่แล้ว)
            engine_kwargs = dict(connect_args={"check_same_thread": False, "timeout": 15})
        else:
            engine_kwargs = {}

        _engine = create_engine(
            database_url,
            # ทดสอบ connection ก่อนหยิบจากพูลทุกครั้ง สำคัญกับ PostgreSQL เพราะ
            # connection ที่ถูกเซิร์ฟเวอร์หรือไฟร์วอลล์ตัดทิ้งไปแล้วยังค้างอยู่ในพูล
            # ถ้าไม่ ping ก่อน จะเจอ "server closed the connection unexpectedly"
            pool_pre_ping=True,
            future=True,
            **engine_kwargs,
        )

        if is_sqlite:
            @event.listens_for(_engine, "connect")
            def _set_sqlite_pragma(dbapi_conn, _):
                cur = dbapi_conn.cursor()
                # WAL ให้อ่านและเขียนพร้อมกันได้ ไม่ล็อกทั้งไฟล์เหมือนโหมดปกติ
                cur.execute("PRAGMA journal_mode=WAL")
                # รอได้ถึง 10 วินาทีถ้าอีกฝั่งกำลังเขียนอยู่ แทนที่จะ error ทันที
                cur.execute("PRAGMA busy_timeout=10000")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.close()

        Base, ChatSession, Interaction = _build_models()
        Base.metadata.create_all(_engine)
        _Session = sessionmaker(bind=_engine, expire_on_commit=False)
        _ready = True
        _last_error = None
        logger.info("ฐานข้อมูลพร้อมใช้งาน: %s", _safe_url(database_url))
    except Exception as exc:  # noqa: BLE001 - ฐานข้อมูลล่มต้องไม่ทำให้แชทบอทใช้ไม่ได้
        _ready = False
        _last_error = f"{type(exc).__name__}: {exc}"
        logger.warning("เชื่อมต่อฐานข้อมูลไม่สำเร็จ (%s) — แชทบอทยังตอบได้ปกติ "
                       "แต่จะไม่บันทึกประวัติลงฐานข้อมูล", _last_error)
        if database_url.startswith("postgresql"):
            logger.warning("  ตรวจสอบ: PostgreSQL เปิดอยู่หรือไม่ / ค่า POSTGRES_* ใน .env "
                           "ถูกต้องหรือไม่ / สร้างฐานข้อมูลกับผู้ใช้แล้วหรือยัง "
                           "(รัน  python scripts/init_postgres.py  เพื่อตรวจและสร้างให้)")
    return _ready


def _safe_url(url: str) -> str:
    """ซ่อนรหัสผ่านใน connection string ก่อนเขียนลง log"""
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        if "@" in rest:
            return f"{scheme}://***@{rest.split('@', 1)[1]}"
    return url


def is_ready() -> bool:
    return _ready


def status() -> dict[str, Any]:
    info: dict[str, Any] = {"ready": _ready, "error": _last_error}
    if _ready and _engine is not None:
        info["dialect"] = _engine.dialect.name
        info["url"] = _safe_url(str(_engine.url))
        try:
            # เวอร์ชันของเซิร์ฟเวอร์ช่วยตอนดีบักว่าต่อไปโดนเครื่องไหน
            info["server_version"] = ".".join(
                str(v) for v in (_engine.dialect.server_version_info or ()))
        except Exception:  # noqa: BLE001
            pass
        try:
            with _Session() as s:
                info["n_interactions"] = s.query(Interaction).count()
                info["n_sessions"] = s.query(ChatSession).count()
        except Exception as exc:  # noqa: BLE001
            info["error"] = f"{type(exc).__name__}: {exc}"
    return info


# ============================================================================
# เขียน
# ============================================================================

def log_interaction(
    *,
    session_id: str,
    question: str,
    answer: str = "",
    mode: str | None = None,
    model: str | None = None,
    system_selected: str | None = None,
    system_used: str | None = None,
    rag_used: bool = False,
    sources: list[dict[str, Any]] | None = None,
    abstained: bool = False,
    latency_ms: int | None = None,
    error: str | None = None,
) -> None:
    """บันทึกหนึ่งรอบถาม-ตอบ ไม่โยน exception ออกไปไม่ว่ากรณีใด

    ฟังก์ชันนี้เป็น sync ตั้งใจให้ฝั่งเรียกใช้ asyncio.to_thread ครอบอีกที
    จะได้ไม่บล็อก event loop ระหว่างเขียนดิสก์
    """
    if not _ready:
        return
    try:
        with _Session() as s:
            row = Interaction(
                session_id=session_id,
                question=question,
                answer=answer,
                mode=mode,
                model=model,
                system_selected=system_selected or None,
                system_used=system_used or None,
                rag_used=rag_used,
                n_sources=len(sources or []),
                sources_json=json.dumps(sources, ensure_ascii=False) if sources else None,
                abstained=abstained,
                latency_ms=latency_ms,
                error=error,
            )
            s.add(row)

            sess = s.get(ChatSession, session_id)
            if sess is None:
                s.add(ChatSession(session_id=session_id, n_interactions=1))
            else:
                sess.n_interactions += 1
                sess.last_active_at = _utcnow()
            s.commit()
    except Exception:  # noqa: BLE001
        logger.exception("บันทึกประวัติลงฐานข้อมูลไม่สำเร็จ (คำตอบยังส่งให้ผู้ใช้ตามปกติ)")


# ============================================================================
# อ่าน
# ============================================================================

def load_history(session_id: str, max_turns: int = 10) -> list[dict[str, str]]:
    """ดึงบทสนทนาย้อนหลังของ session กลับมาในรูปแบบที่ llm_client ใช้ได้เลย

    ใช้ตอนผู้ใช้ถือ cookie เดิมกลับมาหลังแอปรีสตาร์ท — ในหน่วยความจำไม่มีแล้ว
    แต่ในฐานข้อมูลยังอยู่ จำกัดจำนวนรอบเพื่อไม่ให้ prompt ยาวเกินจำเป็น
    """
    if not _ready:
        return []
    try:
        with _Session() as s:
            rows = (
                s.query(Interaction)
                .filter(Interaction.session_id == session_id)
                .order_by(Interaction.created_at.desc())
                .limit(max_turns)
                .all()
            )
        history: list[dict[str, str]] = []
        for r in reversed(rows):          # เรียงกลับเป็นเก่า -> ใหม่
            if not r.answer:              # ข้ามรอบที่ตอบไม่สำเร็จ
                continue
            history.append({"role": "user", "content": r.question})
            history.append({"role": "assistant", "content": r.answer})
        return history
    except Exception:  # noqa: BLE001
        logger.exception("โหลดประวัติจากฐานข้อมูลไม่สำเร็จ")
        return []


def recent_interactions(limit: int = 50, session_id: str | None = None) -> list[dict[str, Any]]:
    """รายการถาม-ตอบล่าสุด สำหรับหน้า/สคริปต์ที่เอาไปดูย้อนหลัง"""
    if not _ready:
        return []
    try:
        with _Session() as s:
            q = s.query(Interaction)
            if session_id:
                q = q.filter(Interaction.session_id == session_id)
            rows = q.order_by(Interaction.created_at.desc()).limit(limit).all()
        return [
            {
                "id": r.id,
                "session_id": r.session_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "question": r.question,
                "answer": r.answer,
                "mode": r.mode,
                "model": r.model,
                "system_selected": r.system_selected,
                "system_used": r.system_used,
                "rag_used": r.rag_used,
                "n_sources": r.n_sources,
                "abstained": r.abstained,
                "latency_ms": r.latency_ms,
                "error": r.error,
            }
            for r in rows
        ]
    except Exception:  # noqa: BLE001
        logger.exception("อ่านประวัติจากฐานข้อมูลไม่สำเร็จ")
        return []

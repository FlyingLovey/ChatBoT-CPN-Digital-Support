from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent

# ชื่อโหมดที่ใช้เป็นคีย์ตลอดทั้งระบบ (frontend ส่งค่านี้กลับมาตอนถาม)
MODE_OFFLINE = "offline"
MODE_ONLINE = "online"


@dataclass(frozen=True)
class Provider:
    """แหล่งให้บริการโมเดลหนึ่งราย — ทั้งสองโหมดคุยด้วยฟอร์แมต OpenAI-compatible
    Chat Completions เหมือนกัน ต่างกันแค่ base URL/คีย์/โมเดล โค้ดฝั่ง llm_client
    จึงใช้ทางเดียวกันได้ทั้งคู่ ไม่ต้องแยก branch ตามผู้ให้บริการ"""

    mode: str
    label: str
    description: str
    base_url: str
    api_key: str
    default_model: str

    @property
    def configured(self) -> bool:
        # ไม่มี base URL = ยังไม่ได้ตั้งค่าโหมดนี้ ฝั่งหน้าเว็บจะขึ้นปุ่มแบบกดไม่ได้
        return bool(self.base_url)


class Settings(BaseSettings):
    """ค่าตั้งค่าทั้งหมดของแอปอ่านจากไฟล์ .env (ผ่าน pydantic-settings)

    รองรับผู้ให้บริการโมเดล 2 โหมดพร้อมกัน แล้วให้ผู้ใช้สลับได้จากหน้าเว็บ:
      - offline: LM Studio ที่รันในเครื่อง — ชุดเดียวกับที่ใช้ทดลองในสารนิพนธ์
      - online : ผู้ให้บริการออนไลน์ เช่น OpenRouter — ใช้ตอนไม่มีเครื่องแรงพอ

    ทั้งสองโหมดใช้ endpoint แบบ OpenAI-compatible เหมือนกัน จึงตั้งค่าเป็นชุดตัวแปร
    หน้าตาเดียวกัน แค่คนละ prefix
    """

    # ---------------------------------------------------------- offline ---
    # LM Studio ตั้ง base URL นี้ไว้เป็นค่าเริ่มต้นอยู่แล้ว และไม่ตรวจ API key จริง
    # (ใส่อะไรก็ผ่าน) จึงใส่ค่า placeholder ไว้ให้เลย ไม่ต้องตั้งเองใน .env
    OFFLINE_BASE_URL: str = "http://127.0.0.1:1234/v1"
    OFFLINE_API_KEY: str = "lm-studio"
    # เว้นว่างได้ = ให้หน้าเว็บเลือกจากรายชื่อโมเดลที่ LM Studio โหลดไว้จริงตอนนั้น
    OFFLINE_MODEL: str = ""

    # ----------------------------------------------------------- online ---
    ONLINE_BASE_URL: str = ""
    ONLINE_API_KEY: str = ""
    ONLINE_MODEL: str = ""

    # ค่าชุดเดิมก่อนจะแยกเป็นสองโหมด เก็บไว้เพื่อความเข้ากันได้ย้อนหลัง: ถ้า .env
    # ยังตั้งแค่ LLM_* อยู่ ระบบจะยกค่าชุดนี้ไปเป็นโหมด online ให้อัตโนมัติ
    LLM_BASE_URL: str = ""
    LLM_API_KEY: str = ""
    LLM_MODEL: str = ""

    # โหมดที่เลือกไว้ตอนเปิดหน้าเว็บครั้งแรก (ผู้ใช้สลับเองทีหลังได้)
    DEFAULT_LLM_MODE: str = MODE_OFFLINE

    # ข้อความระบบที่ส่งเป็น role "system" ตอนที่ "ไม่ได้" ใช้ RAG (เช่น ปิด RAG ไว้
    # หรือโหลดดัชนีไม่สำเร็จ) ถ้าใช้ RAG ระบบจะสร้าง system prompt จากเทมเพลตใน
    # services/rag_service.py แทน เพราะต้องแทรกบริบทที่ค้นคืนมาเข้าไปด้วย
    SYSTEM_PROMPT: str = "คุณคือผู้ช่วย AI ที่เป็นมิตรและตอบเป็นภาษาไทย"

    # ---------------------------------------------------------- ฐานข้อมูล ---
    # เก็บประวัติคำถาม-คำตอบไว้วิเคราะห์ย้อนหลัง และให้บทสนทนาไม่หายเมื่อรีสตาร์ท
    DB_ENABLED: bool = True

    # เว้นว่าง = ประกอบ URL ของ PostgreSQL จากค่า POSTGRES_* ด้านล่างให้อัตโนมัติ
    # ตั้งค่านี้โดยตรงก็ได้ถ้าต้องการระบุทั้งเส้น (เช่นต้องใส่พารามิเตอร์พิเศษ) เช่น
    #   postgresql+psycopg://user:pass@host:5432/dbname?sslmode=require
    #   sqlite:///D:/path/to/chatbot.db      ← ใช้ตอนทดสอบเร็วๆ โดยไม่ต้องมี PostgreSQL
    # ค่านี้มีลำดับความสำคัญสูงกว่า POSTGRES_* เสมอ
    DATABASE_URL: str = ""

    # ค่าเชื่อมต่อ PostgreSQL แบบแยกฟิลด์ — แยกไว้เพื่อไม่ต้องเอารหัสผ่านไปต่อเป็น
    # สตริงเดียวเอง (อักขระอย่าง @ : / ในรหัสผ่านต้อง encode ถ้าเขียนรวมใน URL)
    # โค้ดจะ quote ให้เองตอนประกอบ URL
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "chatbot"
    POSTGRES_USER: str = "chatbot"
    POSTGRES_PASSWORD: str = ""
    # disable = ต่อในเครื่องเดียวกัน, require = ผ่านเครือข่ายองค์กร
    POSTGRES_SSLMODE: str = "disable"

    # ---- connection pool ----
    # แอปเขียนประวัติจากเธรดพื้นหลังของ FastAPI พร้อมกันได้หลายเธรด ต่างจาก SQLite
    # ที่เปิดไฟล์เดียว PostgreSQL เปิดเป็น connection จริงต่อหนึ่งตัว จึงต้องจำกัดจำนวน
    # ไม่ให้กินโควตา max_connections ของเซิร์ฟเวอร์ (ค่าเริ่มต้นของ PostgreSQL คือ 100)
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 5
    # รีไซเคิล connection ก่อนที่ไฟร์วอลล์หรือ PostgreSQL จะตัดทิ้งเอง (วินาที)
    DB_POOL_RECYCLE: int = 1800
    # ถ้าเซิร์ฟเวอร์ไม่ตอบภายในกี่วินาทีให้เลิกรอ — กันไม่ให้คำตอบของผู้ใช้ค้าง
    DB_CONNECT_TIMEOUT: int = 5

    # จำนวนรอบถาม-ตอบย้อนหลังที่ดึงกลับมาเป็นบริบท เมื่อผู้ใช้ถือ cookie เดิมกลับมา
    # หลังแอปรีสตาร์ท มากไปจะทำให้ prompt ยาวและช้าโดยไม่จำเป็น
    DB_HISTORY_TURNS: int = 10

    # ---------------------------------------------------------------- RAG ---
    # เปิด/ปิดการค้นคืนเอกสารก่อนตอบ ปิดไว้แล้วแอปจะกลายเป็นแชทบอททั่วไปที่คุยกับ
    # โมเดลตรงๆ ใช้เทียบผลระหว่าง "มี RAG" กับ "ไม่มี RAG" ตอนสาธิตได้
    RAG_ENABLED: bool = True

    # โฟลเดอร์ดัชนีที่ scripts/build_index.py สร้างไว้ (chunks.jsonl, embeddings.npy, meta.json)
    RAG_INDEX_DIR: Path = BASE_DIR / "rag_index"

    # จำนวน chunk ที่ส่งเข้าไปเป็นบริบท — ค่า 5 ตรงกับที่ใช้ในการทดลองที่ 1–4
    RAG_TOP_K: int = 5

    # สามสวิตช์นี้ตรงกับองค์ประกอบ 3 ขั้นของ pipeline ในสารนิพนธ์ ปิดทีละตัวเพื่อ
    # สาธิตให้เห็นว่าแต่ละขั้นมีผลต่อคำตอบอย่างไร
    RAG_USE_HYBRID: bool = True
    RAG_USE_RERANKER: bool = True
    RAG_USE_METADATA_FILTER: bool = True

    # "graded" = เทมเพลตจากการทดลองที่ 4 (ตอบเท่าที่บริบทรองรับ) เป็นค่าเริ่มต้น
    # "strict" = เทมเพลตเดิมจากการทดลองที่ 1–3 (ไม่ครบก็ปฏิเสธตอบ)
    RAG_PROMPT_VARIANT: str = "graded"

    # ------------------------------------------- ชั้นตรวจก่อน-หลังตอบ ---
    # ตรวจคำถามและคำตอบด้วยกฎที่กำหนดแน่นอน ทำงานในเครื่องทั้งหมด ไม่เรียกโมเดลและ
    # ไม่ต่อเน็ต จึงไม่เพิ่มเวลาตอบและไม่ขัดกับข้อกำหนดที่ว่าเอกสารภายในต้องไม่ออกนอกองค์กร
    GUARD_ENABLED: bool = True

    # ไฟล์คำต้องห้ามของชั้นที่ 1 แก้ไฟล์แล้วมีผลทันทีโดยไม่ต้องรีสตาร์ท
    GUARD_BLOCKLIST_PATH: Path = BASE_DIR / "guard_blocklist.txt"

    # ตรวจฝั่งคำตอบด้วยหรือไม่ (เทียบอีเมล/เบอร์โทรในคำตอบกับบริบทที่ค้นคืนมา)
    # มีผลเฉพาะโหมดตอบแบบไม่สตรีม เพราะโหมดสตรีมส่งข้อความออกไปทีละส่วนแล้ว
    GUARD_CHECK_OUTPUT: bool = True

    # โหลดดัชนีและโมเดลตั้งแต่ตอนเปิดแอป (ทำในเธรดพื้นหลัง ไม่บล็อกการเปิดหน้าเว็บ)
    # ปิดไว้ก็ได้ ระบบจะไปโหลดเอาตอนมีคำถามแรกเข้ามาแทน แต่คำถามแรกจะช้า
    RAG_PRELOAD: bool = True

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    @model_validator(mode="after")
    def _backfill_online_from_legacy(self) -> "Settings":
        if not self.ONLINE_BASE_URL and self.LLM_BASE_URL:
            object.__setattr__(self, "ONLINE_BASE_URL", self.LLM_BASE_URL)
            object.__setattr__(self, "ONLINE_API_KEY", self.LLM_API_KEY)
            object.__setattr__(self, "ONLINE_MODEL", self.LLM_MODEL)
        return self

    @property
    def database_url(self) -> str:
        """URL ที่ใช้จริง — ถ้าไม่ได้ตั้ง DATABASE_URL จะประกอบจากค่า POSTGRES_* ให้

        แยกรหัสผ่านออกมาเป็นฟิลด์ของตัวเองแล้ว quote ตรงนี้ เพราะรหัสผ่านที่มี @ : / #
        ปนอยู่จะทำให้ URL ถูกแยกส่วนผิดและ error เป็น "could not translate host name"
        ซึ่งอ่านแล้วนึกไม่ถึงว่าสาเหตุอยู่ที่รหัสผ่าน
        """
        if self.DATABASE_URL:
            return self.DATABASE_URL
        user = quote_plus(self.POSTGRES_USER)
        pwd = f":{quote_plus(self.POSTGRES_PASSWORD)}" if self.POSTGRES_PASSWORD else ""
        return (
            f"postgresql+psycopg://{user}{pwd}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def providers(self) -> dict[str, Provider]:
        return {
            MODE_OFFLINE: Provider(
                mode=MODE_OFFLINE,
                label="ออฟไลน์",
                description="LM Studio ในเครื่อง",
                base_url=self.OFFLINE_BASE_URL,
                api_key=self.OFFLINE_API_KEY,
                default_model=self.OFFLINE_MODEL,
            ),
            MODE_ONLINE: Provider(
                mode=MODE_ONLINE,
                label="ออนไลน์",
                description="ผู้ให้บริการออนไลน์ (เช่น OpenRouter)",
                base_url=self.ONLINE_BASE_URL,
                api_key=self.ONLINE_API_KEY,
                default_model=self.ONLINE_MODEL,
            ),
        }

    @property
    def default_mode(self) -> str:
        """โหมดที่จะใช้เมื่อผู้ใช้ยังไม่ได้เลือก — ถ้าโหมดที่ตั้งไว้ยังไม่ได้ตั้งค่า
        ให้ถอยไปใช้โหมดอื่นที่ตั้งค่าไว้แล้วแทน จะได้ไม่เปิดมาเจอ error ตั้งแต่แรก"""
        providers = self.providers
        wanted = self.DEFAULT_LLM_MODE if self.DEFAULT_LLM_MODE in providers else MODE_OFFLINE
        if providers[wanted].configured:
            return wanted
        for mode, provider in providers.items():
            if provider.configured:
                return mode
        return wanted


settings = Settings()

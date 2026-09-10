import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import settings
from routers.chat import router as chat_router
from services import rag_service

# หาโฟลเดอร์ static/templates จาก path ของไฟล์นี้เอง แทนที่จะพึ่ง current working
# directory ตอนรัน เพราะบน serverless (เช่น Vercel) ไม่การันตีว่า process จะเริ่ม
# ทำงานจาก root ของโปรเจกต์เสมอไป
BASE_DIR = Path(__file__).resolve().parent

# ตั้ง logging กลางไว้ตั้งแต่จุดเริ่มโปรแกรม เพื่อให้ log จากทุกโมดูล (routers, services)
# ออกมาในฟอร์แมตเดียวกันหมด แทนที่จะต้องตั้งซ้ำในแต่ละไฟล์
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """อุ่นเครื่องดัชนี RAG ตั้งแต่ตอนเปิดแอป โหลดในเธรดพื้นหลังแบบ daemon เพื่อไม่ให้
    หน้าเว็บเปิดไม่ได้ระหว่างรอ (โหลด embedding model + reranker ใช้เวลาหลายสิบวินาที
    ในครั้งแรกที่ยังไม่มีไฟล์โมเดลในเครื่อง) ถ้าโหลดไม่สำเร็จ RagService จะเก็บสาเหตุไว้
    ใน last_error เอง แล้วแชทบอทจะทำงานต่อโดยไม่ใช้ฐานความรู้"""
    if settings.RAG_ENABLED and settings.RAG_PRELOAD:
        threading.Thread(
            target=rag_service.get_service().load, name="rag-preload", daemon=True
        ).start()
    yield


app = FastAPI(
    title="IT Support Chatbot (RAG)",
    description="แชทบอทสนับสนุนงานไอทีด้วย LLM และ Retrieval-Augmented Generation",
    version="0.2.0",
    lifespan=lifespan,
)

# เปิด CORS แบบกว้าง ("*") เพราะเป็นโปรเจกต์ทดลอง/รันในเครื่องเท่านั้น ไม่มี
# frontend แยกโดเมนจริงจังที่ต้องจำกัดสิทธิ์
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# เสิร์ฟไฟล์หน้าบ้าน (css/js) และ template ของหน้าแชทแบบ server-rendered เพียงหน้าเดียว
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

app.include_router(chat_router)


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/health")
async def health():
    # ใช้เช็คว่าเซิร์ฟเวอร์ยังรันอยู่เฉยๆ (ไม่ได้เช็คว่าคุยกับผู้ให้บริการ AI ได้ไหม)
    # rag บอกแค่ว่าดัชนีโหลดเสร็จหรือยัง รายละเอียดเต็มดูที่ /api/v1/rag/status
    return {
        "status": "ok",
        "rag": settings.RAG_ENABLED and rag_service.get_service().is_ready(),
    }

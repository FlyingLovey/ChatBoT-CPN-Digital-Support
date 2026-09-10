import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from config import settings
from services import llm_client, rag_service
from services.llm_client import LLMConnectionError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1")

# เก็บแค่ในหน่วยความจำ: รีสตาร์ทเซิร์ฟเวอร์แล้วหายหมด และไม่ scale ข้ามหลาย process
# ถ้าจะทำ production จริงควรย้ายไป Redis หรือฐานข้อมูลแทน
#
# ค่าที่เก็บคือ "ประวัติข้อความทั้งหมด" ต่อ session (list ของ {role, content})
# ไม่ใช่ previous_response_id แบบเดิมอีกแล้ว เพราะ Chat Completions API เป็นแบบ
# stateless ผู้ให้บริการไม่จำบทสนทนาให้ ต้องส่ง history ทั้งหมดไปเองทุกครั้ง
# (ดูเหตุผลเพิ่มเติมที่ services/llm_client.py::_build_messages)
conversation_store: dict[str, list[dict[str, str]]] = {}

SESSION_COOKIE = "session_id"
SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 วัน

# คำถามที่สั้นกว่านี้มักเป็นคำถามต่อเนื่อง เช่น "แล้วรีเซ็ตยังไง" ซึ่งไม่มีชื่อระบบอยู่ในตัวเอง
# จึงเอาข้อความก่อนหน้าของผู้ใช้มาต่อ "เฉพาะตอนค้นคืน" ให้ตัวตรวจจับชื่อระบบและ BM25
# มีคำสำคัญพอที่จะหาเอกสารถูกฉบับ (ข้อความที่ส่งให้โมเดลยังเป็นคำถามเดิมไม่เปลี่ยน)
FOLLOWUP_QUERY_MAX_CHARS = 40


def _ensure_session(session_id: str | None) -> tuple[str, bool]:
    """คืนค่า (session_id, is_new) ถ้าเป็น session ใหม่จะสร้างประวัติบทสนทนาว่างให้ด้วย"""
    if session_id and session_id in conversation_store:
        return session_id, False
    new_id = str(uuid.uuid4())
    conversation_store[new_id] = []
    return new_id, True


def _set_session_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=session_id,
        httponly=True,
        samesite="lax",
        max_age=SESSION_MAX_AGE,
    )


def _retrieval_query(history: list[dict[str, str]], message: str) -> str:
    if len(message) >= FOLLOWUP_QUERY_MAX_CHARS:
        return message
    for turn in reversed(history):
        if turn["role"] == "user":
            return f"{turn['content']} {message}"
    return message


async def _retrieve(history: list[dict[str, str]], message: str):
    """ค้นคืนบริบทแล้วประกอบ system prompt คืน (system_prompt, sources)

    ถ้าปิด RAG ไว้ หรือดัชนียังไม่พร้อม หรือค้นแล้วไม่เจออะไรเลย จะคืน (None, []) ซึ่งฝั่ง
    llm_client จะถอยไปใช้ SYSTEM_PROMPT ปกติจาก .env แทน แชทบอทจึงยังตอบได้เสมอ
    """
    if not settings.RAG_ENABLED:
        return None, []
    service = rag_service.get_service()
    try:
        # search() เป็นงานหนักฝั่ง CPU (encode + rerank) และเป็นโค้ด sync ถ้าเรียกตรงๆ
        # จะบล็อก event loop ทำให้คำขออื่นค้างทั้งเซิร์ฟเวอร์ จึงโยนไปรันในเธรดแยก
        chunks = await asyncio.to_thread(
            service.search, _retrieval_query(history, message), settings.RAG_TOP_K
        )
    except Exception:  # noqa: BLE001 - RAG ล้มไม่ควรทำให้ตอบคำถามไม่ได้
        logger.exception("ค้นคืนเอกสารล้มเหลว — จะตอบโดยไม่ใช้ฐานความรู้")
        return None, []
    if not chunks:
        return None, []
    system_prompt = rag_service.build_system_prompt(
        message, chunks, settings.RAG_PROMPT_VARIANT
    )
    return system_prompt, rag_service.sources_payload(chunks)


class ChatRequest(BaseModel):
    message: str
    # โหมดและโมเดลที่ผู้ใช้เลือกจากหน้าเว็บ ส่งมาพร้อมทุกคำถาม (ไม่เก็บฝั่งเซิร์ฟเวอร์)
    # ทำแบบนี้เพื่อให้เปลี่ยนโมเดลกลางบทสนทนาได้ทันที และเปิดหลายแท็บโดยเลือกคนละโมเดล
    # เปรียบเทียบกันได้ — ซึ่งเป็นสิ่งที่ต้องใช้ตอนสาธิตเทียบ 4 โมเดลในสารนิพนธ์
    mode: str | None = None
    model: str | None = None


class ChatResponse(BaseModel):
    reply: str
    sources: list[dict] = []


@router.get("/providers")
async def get_providers():
    """รายการโหมด (ออฟไลน์/ออนไลน์) ให้หน้าเว็บวาดปุ่มสลับ พร้อมบอกว่าโหมดไหนตั้งค่าไว้แล้ว"""
    return {"providers": llm_client.list_providers()}


@router.get("/models")
async def get_models(mode: str | None = None):
    # เลียนแบบ proxy pattern: ไม่ส่ง API key ออกไปให้ frontend เห็นเด็ดขาด และตอน
    # error ก็ตอบ 200 พร้อม {"error": ...} แทนการโยน HTTPException ตรงๆ
    # (โหมดออฟไลน์จะ error เป็นปกติถ้ายังไม่ได้เปิด LM Studio — เป็นสถานะที่หน้าเว็บ
    # ต้องแสดงให้ผู้ใช้เห็นและแก้เอง ไม่ใช่ความผิดพลาดของเซิร์ฟเวอร์)
    try:
        models = await llm_client.list_models(mode)
        return {"models": models}
    except LLMConnectionError as exc:
        logger.warning("ไม่สามารถดึงรายชื่อโมเดลได้ (mode=%s): %s", mode, exc.message)
        return JSONResponse(status_code=200, content={"error": True, "message": exc.message})


@router.get("/rag/status")
async def get_rag_status():
    """สถานะฐานความรู้ ใช้ให้หน้าเว็บบอกผู้ใช้ได้ว่าตอนนี้ตอบจากเอกสารหรือตอบจากโมเดลล้วนๆ"""
    if not settings.RAG_ENABLED:
        return {"enabled": False, "ready": False}
    service = rag_service.get_service()
    # ถ้ายังไม่มีใครสั่งโหลด (เช่นปิด RAG_PRELOAD ไว้) ให้เริ่มโหลดตรงนี้เลย ไม่งั้นป้าย
    # สถานะบนหน้าเว็บจะค้างที่ "กำลังโหลด" ตลอดไปจนกว่าจะมีคำถามแรกเข้ามา
    service.ensure_loading()
    status = service.status()
    status["enabled"] = True
    status["top_k"] = settings.RAG_TOP_K
    status["prompt_variant"] = settings.RAG_PROMPT_VARIANT
    return status


@router.post("/chat/reset")
async def post_chat_reset(request: Request, response: Response):
    """ล้างประวัติบทสนทนาของ session นี้ โดยไม่ออก session_id ใหม่ — เบื้องหลังปุ่ม
    "เริ่มใหม่" (Usability Heuristic: User Control & Freedom) ผู้ใช้ได้เริ่มคุยใหม่
    ทั้งหมด แต่ cookie เดิมยังใช้ต่อได้ ไม่ต้องออก cookie ใหม่"""
    session_id, is_new = _ensure_session(request.cookies.get(SESSION_COOKIE))
    if is_new:
        _set_session_cookie(response, session_id)
    conversation_store[session_id] = []
    return {"ok": True}


@router.post("/chat", response_model=ChatResponse)
async def post_chat(chat_request: ChatRequest, request: Request, response: Response):
    session_id, is_new = _ensure_session(request.cookies.get(SESSION_COOKIE))
    if is_new:
        _set_session_cookie(response, session_id)
    history = conversation_store.get(session_id, [])

    system_prompt, sources = await _retrieve(history, chat_request.message)

    try:
        reply_text = await llm_client.chat(
            history, chat_request.message, system_prompt,
            mode=chat_request.mode, model=chat_request.model,
        )
    except LLMConnectionError as exc:
        logger.error("chat ล้มเหลว session=%s: %s", session_id, exc.message)
        return JSONResponse(status_code=502, content={"error": True, "message": exc.message})

    # ต่อประวัติด้วยข้อความรอบนี้ (ทั้งฝั่งผู้ใช้และ AI) เก็บไว้ใช้เป็น context รอบถัดไป
    # เก็บเฉพาะบทสนทนา ไม่เก็บบริบทที่ค้นคืนมา เพราะรอบถัดไปจะค้นใหม่ตามคำถามใหม่อยู่แล้ว
    # ถ้าสะสมบริบทเก่าไว้ด้วย prompt จะยาวขึ้นเรื่อยๆ และเอกสารที่ไม่เกี่ยวจะกวนคำตอบ
    conversation_store[session_id] = history + [
        {"role": "user", "content": chat_request.message},
        {"role": "assistant", "content": reply_text},
    ]
    return ChatResponse(reply=reply_text, sources=sources)


@router.post("/chat/stream")
async def post_chat_stream(chat_request: ChatRequest, request: Request):
    session_id, is_new = _ensure_session(request.cookies.get(SESSION_COOKIE))
    history = conversation_store.get(session_id, [])

    async def event_generator():
        received_done = False
        accumulated_text = ""
        try:
            system_prompt, sources = await _retrieve(history, chat_request.message)
            # ส่งรายการแหล่งอ้างอิงออกไปก่อนตัวคำตอบ หน้าเว็บจะได้ขึ้นให้เห็นทันที
            # ว่ากำลังตอบจากเอกสารฉบับไหน ไม่ต้องรอจนสตรีมจบ
            yield f"data: {json.dumps({'sources': sources}, ensure_ascii=False)}\n\n"

            async for event in llm_client.chat_stream(
                history, chat_request.message, system_prompt,
                mode=chat_request.mode, model=chat_request.model,
            ):
                if event["type"] == "delta":
                    accumulated_text += event["content"]
                    payload = {"delta": event["content"]}
                elif event["type"] == "error":
                    logger.error("stream error session=%s: %s", session_id, event["message"])
                    payload = {"error": True, "message": event["message"]}
                elif event["type"] == "done":
                    received_done = True
                    payload = {"done": True}
                else:
                    continue
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except Exception as exc:
            logger.exception("stream ล้มเหลวโดยไม่คาดคิด session=%s", session_id)
            payload = {"error": True, "message": f"เกิดข้อผิดพลาดที่ไม่คาดคิด: {exc}"}
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        finally:
            # บันทึกประวัติเฉพาะตอนสตรีมจบแบบสมบูรณ์ (received_done) เท่านั้น ถ้าโดน
            # ตัดกลางทางหรือผู้ใช้กด "หยุด" เอง จะไม่เก็บคำตอบครึ่งๆ กลางๆ ไว้เป็น
            # context ต่อ กันบทสนทนารอบถัดไปสับสนจากคำตอบที่ไม่สมบูรณ์
            if accumulated_text and received_done:
                conversation_store[session_id] = history + [
                    {"role": "user", "content": chat_request.message},
                    {"role": "assistant", "content": accumulated_text},
                ]
            if not received_done:
                yield f"data: {json.dumps({'done': True}, ensure_ascii=False)}\n\n"

    resp = StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
    if is_new:
        _set_session_cookie(resp, session_id)
    return resp

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from config import Provider, settings

logger = logging.getLogger(__name__)

# connect: เวลาผูก TCP handshake, write: เวลาส่ง request, read: เวลารอโทเค็นตอบกลับ
# (ตั้งไว้นานเพราะโมเดลบางตัว โดยเฉพาะที่รันในเครื่องเอง ตอบช้า), pool: เวลารอ
# connection ว่างจาก connection pool
REQUEST_TIMEOUT = httpx.Timeout(connect=5.0, write=10.0, read=120.0, pool=5.0)


class LLMConnectionError(Exception):
    """โยนทุกครั้งที่คุยกับผู้ให้บริการ LLM ไม่สำเร็จ พร้อมข้อความภาษาไทยที่ปลอดภัย
    พอจะส่งกลับไปแสดงให้ผู้ใช้เห็นตรงๆ ได้เลย (ไม่หลุด stack trace หรือรายละเอียดภายใน)"""

    def __init__(self, message: str, status_code: int = 502):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def resolve_provider(mode: str | None) -> Provider:
    """แปลงชื่อโหมดที่หน้าเว็บส่งมาเป็นค่าตั้งค่าของผู้ให้บริการจริง

    ถ้าโหมดไม่รู้จักหรือไม่ได้ส่งมา ให้ใช้โหมดเริ่มต้น และถ้าโหมดที่ขอมายังไม่ได้ตั้งค่า
    ใน .env จะโยน LLMConnectionError ทันทีพร้อมบอกว่าต้องตั้งตัวแปรตัวไหน แทนที่จะปล่อย
    ให้ไปพังตอนยิง request ด้วย URL ว่าง ซึ่งอ่าน error ไม่รู้เรื่อง
    """
    providers = settings.providers
    resolved = mode if mode in providers else settings.default_mode
    provider = providers[resolved]
    if not provider.configured:
        raise LLMConnectionError(
            f"ยังไม่ได้ตั้งค่าโหมด{provider.label} ({provider.description}) "
            f"กรุณาตั้งค่า {resolved.upper()}_BASE_URL ในไฟล์ .env"
        )
    return provider


def list_providers() -> list[dict[str, Any]]:
    """รายการโหมดทั้งหมดสำหรับให้หน้าเว็บวาดตัวเลือก — ไม่ส่ง API key ออกไปเด็ดขาด"""
    default_mode = settings.default_mode
    return [
        {
            "mode": p.mode,
            "label": p.label,
            "description": p.description,
            "configured": p.configured,
            "default_model": p.default_model,
            "is_default": p.mode == default_mode,
        }
        for p in settings.providers.values()
    ]


def _auth_headers(provider: Provider) -> dict[str, str]:
    return {"Authorization": f"Bearer {provider.api_key}"}


def _build_messages(
    history: list[dict[str, str]], message: str, system_prompt: str | None = None
) -> list[dict[str, str]]:
    """ประกอบ payload แบบ Chat Completions มาตรฐาน (messages: [{role, content}])
    เพราะ endpoint นี้เป็น endpoint เดียวที่ทั้ง LM Studio และ OpenRouter รองรับ
    เหมือนกัน ต่างจาก Native API v1 เดิมของ LM Studio (input + previous_response_id)
    ที่ผูกติดกับ LM Studio เท่านั้นและใช้กับ OpenRouter ไม่ได้

    API แบบนี้เป็น stateless คือผู้ให้บริการไม่จำบทสนทนาให้ ต้องส่ง history
    ทั้งหมดที่คุยกันมาไปพร้อมกับข้อความใหม่ทุกครั้ง (history มาจาก conversation_store
    ฝั่ง router ซึ่งเก็บแยกตาม session)
    """
    # ถ้าผู้เรียกส่ง system_prompt มา (กรณีใช้ RAG จะเป็นเทมเพลตที่แทรกบริบทไว้แล้ว)
    # ให้ใช้ค่านั้นแทนค่าคงที่ใน .env
    effective_system = system_prompt if system_prompt is not None else settings.SYSTEM_PROMPT
    messages: list[dict[str, str]] = []
    if effective_system:
        messages.append({"role": "system", "content": effective_system})
    messages.extend(history)
    messages.append({"role": "user", "content": message})
    return messages


def _connect_error(provider: Provider) -> LLMConnectionError:
    """ข้อความตอนต่อผู้ให้บริการไม่ติด — บอกให้ตรงกับโหมดที่เลือกจริง เพราะวิธีแก้ต่างกัน
    คนละเรื่อง: ออฟไลน์คือลืมเปิด Local Server ใน LM Studio ส่วนออนไลน์คือเน็ต/URL ผิด"""
    if provider.mode == "offline":
        return LLMConnectionError(
            "เชื่อมต่อ LM Studio ไม่ได้ กรุณาเปิดโปรแกรม LM Studio แล้วกด Start Server "
            f"ที่แท็บ Developer หรือตรวจสอบค่า OFFLINE_BASE_URL ({provider.base_url}) ในไฟล์ .env"
        )
    return LLMConnectionError(
        "เชื่อมต่อผู้ให้บริการ AI ออนไลน์ไม่ได้ กรุณาตรวจสอบการเชื่อมต่ออินเทอร์เน็ต "
        "และค่า ONLINE_BASE_URL ในไฟล์ .env"
    )


def _resolve_model(provider: Provider, model: str | None) -> str:
    """เลือกชื่อโมเดลที่จะยิงไป: ใช้ตัวที่ผู้ใช้เลือกจากหน้าเว็บก่อน ถ้าไม่ได้เลือกจึงใช้
    ค่าเริ่มต้นของโหมดนั้นจาก .env

    โหมดออฟไลน์ตั้ง OFFLINE_MODEL เป็นค่าว่างได้ (แล้วแต่ว่าตอนนั้นโหลดโมเดลไหนไว้ใน
    LM Studio) กรณีนั้นถ้าผู้ใช้ยังไม่ได้เลือกด้วย จะยังไม่รู้ว่าต้องยิงโมเดลไหน
    จึงบอกให้ไปเลือกจากรายการบนหน้าเว็บ ดีกว่าส่ง model ว่างไปให้ผู้ให้บริการปฏิเสธ
    """
    chosen = (model or provider.default_model or "").strip()
    if not chosen:
        raise LLMConnectionError(
            f"ยังไม่ได้เลือกโมเดลสำหรับโหมด{provider.label} "
            f"กรุณาเลือกจากรายการด้านบน หรือตั้งค่า {provider.mode.upper()}_MODEL ในไฟล์ .env"
        )
    return chosen


def _friendly_error_from_response(exc: httpx.HTTPStatusError) -> LLMConnectionError:
    # แปลง error code มาตรฐานจาก HTTP ให้เป็นข้อความไทยที่เข้าใจง่าย แทนที่จะโชว์
    # JSON error ดิบๆ จากผู้ให้บริการให้ผู้ใช้เห็น
    status = exc.response.status_code
    try:
        body = exc.response.json()
        detail = body.get("error", {}).get("message", "")
    except (ValueError, AttributeError):
        detail = exc.response.text

    if status == 401:
        message = "เชื่อมต่อไม่สำเร็จ: API key ไม่ถูกต้องหรือหมดอายุ กรุณาตรวจสอบค่า LLM_API_KEY"
    elif status == 400:
        message = f"คำขอไม่ถูกต้อง: {detail}" if detail else "คำขอไม่ถูกต้อง"
    elif status == 404:
        message = f"ไม่พบโมเดลนี้: {detail}" if detail else "ไม่พบโมเดลที่ระบุ กรุณาตรวจสอบค่า LLM_MODEL"
    elif status == 429:
        message = "ถูกจำกัดอัตราการเรียกใช้งาน (rate limit) กรุณาลองใหม่อีกครั้งภายหลัง"
    elif status == 503:
        message = "ผู้ให้บริการ AI ไม่พร้อมให้บริการตอนนี้"
    else:
        message = f"ผู้ให้บริการ AI ตอบกลับด้วยข้อผิดพลาด ({status})"
    return LLMConnectionError(message, status_code=502)


async def list_models(mode: str | None = None) -> list[dict[str, Any]]:
    provider = resolve_provider(mode)
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        try:
            response = await client.get(
                f"{provider.base_url}/models",
                headers=_auth_headers(provider),
            )
            response.raise_for_status()
        except httpx.ConnectError as exc:
            raise _connect_error(provider) from exc
        except httpx.TimeoutException as exc:
            raise LLMConnectionError("ผู้ให้บริการ AI ตอบสนองช้าเกินไป (timeout)") from exc
        except httpx.HTTPStatusError as exc:
            raise _friendly_error_from_response(exc) from exc

        # เอนด์พอยต์ /models แบบ OpenAI-compatible คืนผลเป็น {"data": [...]}
        return response.json().get("data", [])


async def chat(
    history: list[dict[str, str]],
    message: str,
    system_prompt: str | None = None,
    mode: str | None = None,
    model: str | None = None,
) -> str:
    """ส่งข้อความคุยหนึ่งรอบแบบไม่สตรีม คืนค่าข้อความตอบกลับ (reply_text) เพียงอย่างเดียว
    (ไม่มี response_id ให้คืนแล้ว เพราะ API นี้ไม่ได้เก็บ state ให้)"""
    provider = resolve_provider(mode)
    payload: dict[str, Any] = {
        "model": _resolve_model(provider, model),
        "messages": _build_messages(history, message, system_prompt),
    }

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        try:
            response = await client.post(
                f"{provider.base_url}/chat/completions",
                headers=_auth_headers(provider),
                json=payload,
            )
            response.raise_for_status()
        except httpx.ConnectError as exc:
            raise _connect_error(provider) from exc
        except httpx.TimeoutException as exc:
            raise LLMConnectionError("ผู้ให้บริการ AI ตอบสนองช้าเกินไป (timeout)") from exc
        except httpx.HTTPStatusError as exc:
            raise _friendly_error_from_response(exc) from exc

        data = response.json()
        choices = data.get("choices", [])
        if not choices:
            return ""
        return choices[0].get("message", {}).get("content", "")


async def chat_stream(
    history: list[dict[str, str]],
    message: str,
    system_prompt: str | None = None,
    mode: str | None = None,
    model: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """สตรีมคำตอบทีละชิ้นตามฟอร์แมต SSE มาตรฐานของ Chat Completions
    ส่งออกเป็น dict รูปแบบ:
    {"type": "delta", "content": str} | {"type": "error", "message": str} | {"type": "done"}
    (ไม่มี response_id เหมือนเดิมอีกแล้ว — ฝั่ง router เป็นคนรวบข้อความที่สตรีมมา
    แล้วเก็บเป็นประวัติเองแทน)
    """
    try:
        provider = resolve_provider(mode)
    except LLMConnectionError as exc:
        # โหมดที่เลือกยังไม่ได้ตั้งค่า — ส่งเป็น event error ให้หน้าเว็บ แทนที่จะโยน
        # exception ออกจาก async generator ซึ่งฝั่ง router จับได้ยากกว่า
        yield {"type": "error", "message": exc.message}
        return

    payload: dict[str, Any] = {
        "model": _resolve_model(provider, model),
        "messages": _build_messages(history, message, system_prompt),
        "stream": True,
    }

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            async with client.stream(
                "POST",
                f"{provider.base_url}/chat/completions",
                headers=_auth_headers(provider),
                json=payload,
            ) as response_llm:
                if response_llm.status_code >= 400:
                    body = await response_llm.aread()
                    try:
                        detail = json.loads(body).get("error", {}).get("message", "")
                    except (ValueError, AttributeError):
                        detail = body.decode("utf-8", errors="ignore")
                    yield {
                        "type": "error",
                        "message": f"ผู้ให้บริการ AI ตอบกลับด้วยข้อผิดพลาด: {detail or response_llm.status_code}",
                    }
                    return

                async for line in response_llm.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[len("data:"):].strip()
                    if not raw:
                        continue
                    # สตรีมแบบ OpenAI-compatible จบด้วยบรรทัด "data: [DONE]" ที่ไม่ใช่ JSON
                    if raw == "[DONE]":
                        yield {"type": "done"}
                        return
                    try:
                        event = json.loads(raw)
                    except ValueError:
                        logger.warning("ข้าม SSE data ที่ parse ไม่ได้: %r", raw)
                        continue

                    choices = event.get("choices", [])
                    if not choices:
                        continue
                    content = choices[0].get("delta", {}).get("content")
                    if content:
                        yield {"type": "delta", "content": content}
    except httpx.ConnectError:
        yield {"type": "error", "message": _connect_error(provider).message}
    except httpx.TimeoutException:
        yield {"type": "error", "message": "ผู้ให้บริการ AI ตอบสนองช้าเกินไป (timeout)"}
    except httpx.HTTPError as exc:
        logger.exception("ข้อผิดพลาด httpx ที่ไม่คาดคิดระหว่างสตรีม")
        yield {"type": "error", "message": f"เกิดข้อผิดพลาดในการเชื่อมต่อ: {exc}"}
    except Exception as exc:
        logger.exception("ข้อผิดพลาดที่ไม่คาดคิดระหว่างสตรีม")
        yield {"type": "error", "message": f"เกิดข้อผิดพลาดที่ไม่คาดคิด: {exc}"}

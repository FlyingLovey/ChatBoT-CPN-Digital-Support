"""ชั้นตรวจก่อน-หลังตอบ (Guard Layer) — ทำงานในเครื่องทั้งหมด ไม่เรียกบริการภายนอก

ทำไมต้องมี: ระบบนี้ตอบคำถามจากคู่มือการใช้งานระบบสารสนเทศภายในองค์กร ซึ่งเอกสาร
บางฉบับมีชื่อผู้ติดต่อ อีเมล และเบอร์โทรของพนักงานปนอยู่ ถ้าโมเดลถูกชักจูงด้วยคำสั่ง
แฝง (Prompt Injection) หรือเผลอแต่งเบอร์ติดต่อขึ้นมาเอง ความเสียหายจะตกกับผู้ใช้จริง
การตรวจด้วยกฎที่กำหนดแน่นอน (deterministic) จึงทำงานคู่กับการตรวจด้วยโมเดลภาษา

ออกแบบเป็น 2 ชั้น ทั้งคู่ไม่เรียกโมเดลและไม่ต่อเน็ต จึงไม่กระทบเวลาตอบและไม่ขัดกับ
ข้อกำหนดที่ว่าเอกสารภายในต้องไม่ออกนอกองค์กร
    ชั้นที่ 1  รายการคำต้องห้าม (blocklist)  — ไฟล์ข้อความแก้ได้โดยไม่ต้องแก้โค้ด
    ชั้นที่ 2  กฎขององค์กร (business rules) — regex ตรวจทั้งฝั่งคำถามและฝั่งคำตอบ

ฝั่งคำถามของผู้ใช้ (check_input)
    GUARD_INJECTION   พยายามสั่งให้ลืมคำสั่งเดิม ขอดู System Prompt หรือสวมบทบาทผู้ดูแลระบบ
    GUARD_CREDENTIAL  ขอรหัสผ่าน โทเค็น หรือสิทธิ์ผู้ดูแลระบบ
    GUARD_PRIVACY     ขอข้อมูลส่วนบุคคลของพนักงานหรือผู้ใช้รายอื่น

ฝั่งคำตอบของโมเดล (check_output) — ตรวจเทียบกับบริบทที่ค้นคืนมาได้จริงเท่านั้น
    GUARD_PII_LEAK    คำตอบมีอีเมล เบอร์โทร หรือเลขประจำตัว 13 หลัก ที่ไม่มีอยู่ในบริบท
    GUARD_SECRET_LEAK คำตอบมีรหัสผ่านหรือคีย์ที่ดูเหมือนค่าจริง

หลักการ: ชั้นนี้ "ตรวจ" อย่างเดียว ไม่สร้างคำตอบเอง เมื่อพบว่าผิดกฎจะคืนข้อความปฏิเสธ
ที่เขียนไว้ล่วงหน้าแทนคำตอบของโมเดล และบันทึกรหัสกฎไว้ในประวัติเพื่อตรวจสอบย้อนหลัง
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- ผลการตรวจ ---


@dataclass(frozen=True)
class Verdict:
    """ผลการตรวจหนึ่งครั้ง — blocked=False คือผ่าน"""
    blocked: bool
    code: str = ""
    message: str = ""

    @property
    def ok(self) -> bool:
        return not self.blocked


PASS = Verdict(blocked=False)

# ข้อความปฏิเสธรายกฎ เขียนให้ผู้ใช้รู้ว่าควรทำอะไรต่อ ไม่บอกว่าติดกฎข้อไหนหรือคำใด
# เพราะการบอกรายละเอียดเท่ากับสอนวิธีหลบกฎ
MESSAGES: dict[str, str] = {
    "BLOCKLIST":
        "ขออภัยครับ ระบบไม่สามารถตอบข้อความนี้ได้ "
        "หากต้องการสอบถามการใช้งานระบบสารสนเทศภายใน กรุณาถามใหม่อีกครั้งครับ",
    "GUARD_INJECTION":
        "ขออภัยครับ ระบบไม่สามารถเปลี่ยนบทบาทหรือเปิดเผยคำสั่งการทำงานภายในได้ "
        "ยินดีช่วยตอบคำถามเกี่ยวกับการใช้งานระบบสารสนเทศภายในองค์กรครับ",
    "GUARD_CREDENTIAL":
        "ขออภัยครับ ระบบไม่สามารถให้รหัสผ่าน โทเค็น หรือสิทธิ์การเข้าถึงระบบได้ "
        "กรุณาติดต่อทีมสนับสนุนไอทีผ่านช่องทางแจ้งปัญหาตามขั้นตอนขององค์กรครับ",
    "GUARD_PRIVACY":
        "ขออภัยครับ ระบบไม่เปิดเผยข้อมูลส่วนบุคคลของพนักงานหรือผู้ใช้รายอื่น "
        "หากต้องการติดต่อผู้ดูแลระบบงานใด สอบถามชื่อหน่วยงานที่รับผิดชอบได้ครับ",
    "GUARD_PII_LEAK":
        "ขออภัยครับ ระบบไม่สามารถยืนยันข้อมูลติดต่อในคำตอบนี้กับเอกสารในฐานความรู้ได้ "
        "จึงไม่แสดงคำตอบดังกล่าว กรุณาสอบถามใหม่หรือติดต่อทีมสนับสนุนไอทีโดยตรงครับ",
    "GUARD_SECRET_LEAK":
        "ขออภัยครับ คำตอบนี้มีข้อมูลที่ไม่สามารถเปิดเผยผ่านแชทบอทได้ "
        "กรุณาติดต่อทีมสนับสนุนไอทีโดยตรงครับ",
}

# ------------------------------------------------------- ชั้นที่ 1 blocklist ---


class _Blocklist:
    """อ่านคำต้องห้ามจากไฟล์ข้อความ บรรทัดละคำ ขึ้นต้นด้วย # = คำอธิบาย

    โหลดใหม่เองเมื่อไฟล์ถูกแก้ (ดูจาก mtime) จึงเพิ่มคำได้โดยไม่ต้องรีสตาร์ทเซิร์ฟเวอร์
    ซึ่งสำคัญตอนใช้งานจริง เพราะคำที่ต้องบล็อกมักถูกเพิ่มหลังเจอปัญหาหน้างาน
    """

    def __init__(self) -> None:
        self._path: Path | None = None
        self._mtime: float = -1.0
        self._words: tuple[str, ...] = ()

    def load(self, path: str | Path) -> None:
        p = Path(path)
        self._path = p
        self._refresh(force=True)

    def _refresh(self, force: bool = False) -> None:
        if self._path is None:
            return
        try:
            mtime = self._path.stat().st_mtime
        except OSError:
            if force:
                logger.warning("ไม่พบไฟล์คำต้องห้าม %s — ข้ามชั้นที่ 1", self._path)
            self._words = ()
            self._mtime = -1.0
            return
        if not force and mtime == self._mtime:
            return
        words = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                words.append(line.lower())
        self._words = tuple(words)
        self._mtime = mtime
        logger.info("โหลดคำต้องห้าม %d คำจาก %s", len(self._words), self._path)

    def hit(self, text: str) -> bool:
        self._refresh()
        if not self._words:
            return False
        lowered = text.lower()
        # เทียบกับข้อความที่ตัดช่องว่างทิ้งด้วย กันการเว้นวรรคหลบ เช่น "ร หั ส ผ่ า น"
        compact = re.sub(r"\s+", "", lowered)
        return any(w in lowered or re.sub(r"\s+", "", w) in compact for w in self._words)


_blocklist = _Blocklist()


def load_blocklist(path: str | Path) -> None:
    """ตั้งไฟล์คำต้องห้าม เรียกครั้งเดียวตอนแอปเริ่มทำงาน"""
    _blocklist.load(path)


# ------------------------------------------------- ชั้นที่ 2 กฎขององค์กร ---

# คำที่บ่งว่าเป็น "คำขอ" ไม่ใช่คำถามเชิงข้อมูล ใช้แยก "รีเซ็ตรหัสผ่านอย่างไร" (ถามวิธี
# ซึ่งเป็นงานปกติของการสนับสนุนไอที ต้องตอบได้) ออกจาก "ขอรหัสผ่านของบัญชีนี้หน่อย"
# คำที่บ่งว่าผู้ใช้ถาม "วิธีทำ" ซึ่งเป็นงานปกติของการสนับสนุนไอที ต้องตอบได้เสมอ
# เช่น "รีเซ็ตรหัสผ่าน Wi-Fi ยังไง" ต่างจาก "ขอรหัสผ่าน Wi-Fi หน่อย"
_HOWTO_MARKER = re.compile(
    r"ยังไง|อย่างไร|ทำไง|วิธี|ขั้นตอน|แก้ไขอย่างไร|how\s*(to|do|can)",
    re.IGNORECASE,
)

_REQUEST_MARKER = re.compile(
    r"ขอ|บอก|แจ้ง|ให้หน่อย|ได้(?:ไหม|มั้ย|ป่ะ|ปะ|ไม|บ่|หรือเปล่า|รึเปล่า)|หน่อย|"
    r"give me|tell me|send me|what is the",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _Rule:
    code: str
    patterns: tuple[re.Pattern[str], ...]
    needs_request: bool = False   # ต้องมีคำแสดงการขอร่วมด้วยถึงจะนับว่าผิดกฎ
    allow_howto: bool = False     # ถ้าเป็นคำถามว่า "ทำอย่างไร" ให้ปล่อยผ่าน

    def matches(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", text)
        if not any(p.search(text) or p.search(compact) for p in self.patterns):
            return False
        if self.allow_howto and _HOWTO_MARKER.search(text):
            return False
        return not self.needs_request or bool(_REQUEST_MARKER.search(text))


def _p(*patterns: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


_INPUT_RULES: tuple[_Rule, ...] = (
    _Rule("GUARD_INJECTION", _p(
        r"ignore\s+(all\s+|the\s+)?(previous|above|prior)",
        r"disregard\s+(all\s+|the\s+)?(previous|above|prior|instruction)",
        r"system\s*prompt|developer\s*mode|jailbreak|\bDAN\b",
        r"(ลืม|เพิกเฉย|ไม่ต้องสนใจ|ละเว้น|ยกเลิก|ข้าม)\S{0,10}(คำสั่ง|กฎ|ข้อกำหนด|prompt|พรอมป์|บทบาท)",
        r"(แสดง|บอก|เปิดเผย|พิมพ์|ขอดู)\S{0,10}(คำสั่งระบบ|พรอมป์|prompt|คำสั่งที่ได้รับ|คำสั่งตั้งต้น)",
        r"(สมมติว่า|ทำตัว|สวมบทบาท|แกล้งทำ)\S{0,8}เป็น(ผู้ดูแลระบบ|แอดมิน|admin|ผู้ดูแล|เจ้าหน้าที่ไอที)",
        r"(ผม|ฉัน|เรา|หนู|กู)(คือ|เป็น)(ผู้ดูแลระบบ|แอดมิน|admin|administrator|ผู้พัฒนา|developer)",
    )),
    _Rule("GUARD_CREDENTIAL", _p(
        r"(ขอ|บอก|แจ้ง|ส่ง)\S{0,8}(รหัสผ่าน|password|passwd|token|api\s*key|secret|คีย์)",
        r"(รหัสผ่าน|password|passwd|รหัสเข้าระบบ)\S{0,12}ของ\S{0,10}"
        r"(บัญชี|ผู้ใช้|user|admin|คนอื่น|ผู้อื่น|พนักงาน|หัวหน้า|เขา|ท่าน)",
        r"(สิทธิ์|สิทธิ|permission|access)\S{0,10}(ผู้ดูแลระบบ|แอดมิน|admin|สูงสุด|root)",
        r"(เข้าระบบ|ล็อกอิน|login)\S{0,10}(แทน|ด้วยบัญชี)\S{0,10}(คนอื่น|ผู้อื่น|เขา)",
    ), allow_howto=True),
    _Rule("GUARD_PRIVACY", _p(
        r"(เบอร์|โทรศัพท์|อีเมล|email|ที่อยู่|เงินเดือน|ข้อมูลส่วนตัว|เลขบัตรประชาชน)\S{0,10}"
        r"(ของ)?\S{0,6}(พนักงาน|ผู้ใช้|ลูกค้า|คนอื่น|ผู้อื่น|คนที่|หัวหน้า)",
        r"(พนักงาน|ผู้ใช้|ลูกค้า)\S{0,8}(คนอื่น|รายอื่น|ท่านอื่น|คนก่อน|คนที่แจ้ง)",
        r"ใคร\S{0,10}(แจ้งปัญหา|เปิดใบงาน|ใช้ระบบนี้|ทำรายการ)",
    ), needs_request=True, allow_howto=True),
)

# ---- ฝั่งคำตอบ ----

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# เบอร์ไทย: 0 ตามด้วยตัวเลข 8-9 ตัว อนุญาตขีดหรือช่องว่างคั่น
_PHONE_RE = re.compile(r"\b0\d{1,2}[-\s]?\d{3}[-\s]?\d{3,4}\b")
# เลขประจำตัวประชาชน 13 หลัก (อนุญาตขีดคั่นตามรูปแบบที่พบบ่อย)
_NATID_RE = re.compile(r"\b\d[-\s]?\d{4}[-\s]?\d{5}[-\s]?\d{2}[-\s]?\d\b")
_SECRET_RE = _p(
    r"(รหัสผ่าน|password|passwd|pwd)\s*(คือ|:|=)\s*\S{4,}",
    r"(api[_\s-]?key|secret|token)\s*(คือ|:|=)\s*[\w-]{8,}",
)


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def _in_context(value: str, context: str, *, numeric: bool = False) -> bool:
    """ค่านี้ปรากฏอยู่ในบริบทที่ค้นคืนมาหรือไม่

    กรณีตัวเลขจะเทียบเฉพาะตัวเลขล้วน เพราะเอกสารเขียนเบอร์โทรไม่เหมือนกัน
    เช่น 02-123-4567 กับ 021234567 ต้องถือว่าเป็นค่าเดียวกัน
    """
    if not context:
        return False
    if numeric:
        return _digits(value) in _digits(context)
    return value.lower() in context.lower()


def check_input(text: str) -> Verdict:
    """ตรวจคำถามของผู้ใช้ก่อนส่งเข้ากระบวนการค้นคืนและสร้างคำตอบ"""
    if not text or not text.strip():
        return PASS
    if _blocklist.hit(text):
        return Verdict(True, "BLOCKLIST", MESSAGES["BLOCKLIST"])
    for rule in _INPUT_RULES:
        if rule.matches(text):
            return Verdict(True, rule.code, MESSAGES[rule.code])
    return PASS


def check_output(answer: str, context: str = "") -> Verdict:
    """ตรวจคำตอบของโมเดลเทียบกับบริบทที่ค้นคืนมา

    context คือข้อความที่ประกอบเป็น System Prompt ของรอบนั้น (มีเนื้อหาส่วนข้อความที่
    ค้นคืนมาอยู่ครบ) ถ้าคำตอบมีอีเมลหรือเบอร์โทรที่ไม่ปรากฏในบริบท แปลว่าโมเดลแต่งขึ้น
    เองหรือดึงมาจากความจำในพารามิเตอร์ ซึ่งผู้ใช้ไม่ควรนำไปติดต่อจริง
    """
    if not answer or not answer.strip():
        return PASS

    for pattern in _SECRET_RE:
        if pattern.search(answer):
            return Verdict(True, "GUARD_SECRET_LEAK", MESSAGES["GUARD_SECRET_LEAK"])

    for value in _EMAIL_RE.findall(answer):
        if not _in_context(value, context):
            return Verdict(True, "GUARD_PII_LEAK", MESSAGES["GUARD_PII_LEAK"])
    for value in _PHONE_RE.findall(answer):
        if not _in_context(value, context, numeric=True):
            return Verdict(True, "GUARD_PII_LEAK", MESSAGES["GUARD_PII_LEAK"])
    for value in _NATID_RE.findall(answer):
        if not _in_context(value, context, numeric=True):
            return Verdict(True, "GUARD_PII_LEAK", MESSAGES["GUARD_PII_LEAK"])
    return PASS

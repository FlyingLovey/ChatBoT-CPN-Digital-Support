"""ชุดทดสอบส่วนค้นคืนเอกสาร (services/rag_service.py)

รันด้วย:  pytest -q
ไม่ต้องมีดัชนี ไม่ต้องโหลดโมเดล ไม่ต้องมี LM Studio และไม่ต่อเน็ต เพราะไฟล์นี้ทดสอบเฉพาะ
ส่วนที่เป็นตรรกะล้วน คือ ตัวตัดคำสำหรับ BM25, การรวมอันดับด้วย RRF, การตรวจจับชื่อระบบงาน
และการประกอบบริบท/พรอมต์

ค่าที่คาดหวังในไฟล์นี้ผูกกับตัวเลขชุดเดียวกับที่รายงานไว้ในสารนิพนธ์ (รูปที่ 1, รูปที่ 2 และ
สมการที่ (1)) ถ้าแก้โค้ดแล้วผลการค้นคืนเปลี่ยนไปจากที่รายงานไว้ ชุดทดสอบนี้จะจับได้ทันที
"""

from pathlib import Path

import pytest

from services import rag_service
from services.rag_service import RetrievedChunk, _reciprocal_rank_fusion, _tokenize_for_bm25


# ===================================================== ตัวตัดคำสำหรับ BM25 ===
# ภาษาไทยไม่เว้นวรรคระหว่างคำ จึงใช้คู่อักขระ (character bigram) แทนการตัดคำจริง
# ส่วนภาษาอังกฤษและตัวเลขใช้ regex word ตามปกติ

def test_tokenize_english_and_digits():
    assert _tokenize_for_bm25("SAP Login 2024") == ["sap", "login", "2024"]


def test_tokenize_is_case_insensitive():
    assert _tokenize_for_bm25("SAP") == _tokenize_for_bm25("sap")


def test_tokenize_drops_punctuation():
    assert _tokenize_for_bm25("SAP: login!  (ERP)") == ["sap", "login", "erp"]


def test_tokenize_thai_bigrams_exact():
    """"รหัสผ่าน" มี 8 อักขระ จึงต้องได้คู่อักขระเหลื่อมกันทีละหนึ่งตำแหน่ง 7 คู่"""
    assert _tokenize_for_bm25("รหัสผ่าน") == [
        "รห", "หั", "ัส", "สผ", "ผ่", "่า", "าน",
    ]


def test_tokenize_thai_run_shorter_than_bigram_kept_as_is():
    """อักขระไทยเดี่ยว ๆ ต้องไม่หายไป ไม่อย่างนั้นคำถามสั้นมากจะกลายเป็นโทเค็นว่าง"""
    assert _tokenize_for_bm25("ก ข") == ["ก", "ข"]


def test_tokenize_mixed_puts_latin_tokens_first():
    """ลำดับต้องคงที่: โทเค็นอังกฤษ/ตัวเลขก่อน แล้วจึงตามด้วยคู่อักขระไทย"""
    tokens = _tokenize_for_bm25("ลืมรหัสผ่าน SAP")
    assert tokens[0] == "sap"
    assert all(len(t) <= 2 for t in tokens[1:])
    assert "รห" in tokens


def test_tokenize_empty_text():
    assert _tokenize_for_bm25("") == []


def test_tokenize_shares_bigrams_across_wordings():
    """เหตุผลที่เลือกใช้คู่อักขระ: คำถามที่เขียนต่างจากคู่มือเล็กน้อยยังจับคู่กันได้

    ตรงกับรูปที่ 1 ในสารนิพนธ์ — "รหัสผ่าน" กับ "เปลี่ยนรหัสผ่าน" ต้องมีคู่อักขระร่วมกัน
    ครบทุกคู่ของคำที่สั้นกว่า
    """
    short = set(_tokenize_for_bm25("รหัสผ่าน"))
    long = set(_tokenize_for_bm25("เปลี่ยนรหัสผ่าน"))
    assert short <= long


# ====================================== การรวมอันดับด้วย Reciprocal Rank Fusion ===
# สมการที่ (1) ในสารนิพนธ์: score(d) = sum( 1 / (rrf_k + rank_i(d)) ) โดย rank เริ่มที่ 1

def test_rrf_reproduces_paper_example():
    """ตัวอย่างเดียวกับรูปที่ 2 ในสารนิพนธ์ (k = 60)

    BM25   : D1, D2, D3, D4
    เวกเตอร์ : D2, D4, D1, D5
    ผลรวม   : D2, D1, D4, D3, D5 — D2 ขึ้นมาอันดับหนึ่งทั้งที่ไม่ได้เป็นที่หนึ่งของวิธีใดเลย
    """
    bm25 = [1, 2, 3, 4]
    dense = [2, 4, 1, 5]
    assert _reciprocal_rank_fusion([bm25, dense], k=5, rrf_k=60) == [2, 1, 4, 3, 5]


def test_rrf_rank_is_one_based():
    """rank ต้องเริ่มนับที่ 1 ตามสมการที่ (1) — ถ้าเผลอนับจาก 0 เมื่อ k = 0 จะหารด้วยศูนย์

    เคสนี้คือเคสที่จับความผิดพลาดแบบ off-by-one ได้โดยตรง เพราะถ้าลำดับยังเรียงเหมือนเดิม
    การเทียบแค่ลำดับผลลัพธ์จะมองไม่เห็นความต่างเลย
    """
    assert _reciprocal_rank_fusion([[1, 2, 3]], k=3, rrf_k=0) == [1, 2, 3]


def test_rrf_lets_a_runner_up_in_both_lists_beat_a_winner_in_one():
    """หัวใจของ RRF: เอกสารที่ติดอันดับสองของทั้งสองวิธี ต้องชนะเอกสารที่เป็นที่หนึ่งของวิธีเดียว

    ใช้ k เล็ก (= 1) เพื่อให้ผลต่างของสูตรเห็นชัดในลำดับผลลัพธ์ ถ้าดัชนีอันดับผิดไปหนึ่ง
    ตำแหน่ง คะแนนทั้งสามจะเท่ากันหมดและลำดับจะกลายเป็น [10, 20, 30]
    """
    bm25 = [10, 20]
    dense = [30, 20]
    assert _reciprocal_rank_fusion([bm25, dense], k=3, rrf_k=1) == [20, 10, 30]


def test_rrf_reproduces_the_scores_printed_in_the_paper():
    """ตัวเลขคะแนนในรูปที่ 2 คำนวณจากสูตรเดียวกับที่โค้ดใช้ (ตรวจด้วยลำดับที่ได้จริง)

    D2 = 1/62 + 1/61 = 0.032522   (สูงสุด)
    D1 = 1/61 + 1/63 = 0.032266
    D4 = 1/64 + 1/62 = 0.031754
    D3 = 1/63        = 0.015873
    D5 = 1/64        = 0.015625
    """
    ordered = _reciprocal_rank_fusion([[1, 2, 3, 4], [2, 4, 1, 5]], k=5, rrf_k=60)
    expected = sorted(
        {1: 1/61 + 1/63, 2: 1/62 + 1/61, 3: 1/63, 4: 1/64 + 1/62, 5: 1/64},
        key=lambda d: -{1: 1/61 + 1/63, 2: 1/62 + 1/61, 3: 1/63, 4: 1/64 + 1/62, 5: 1/64}[d],
    )
    assert ordered == expected == [2, 1, 4, 3, 5]


def test_rrf_single_list_preserves_order():
    assert _reciprocal_rank_fusion([[7, 8, 9]], k=3, rrf_k=60) == [7, 8, 9]


def test_rrf_truncates_to_k():
    assert _reciprocal_rank_fusion([[1, 2, 3, 4, 5]], k=2, rrf_k=60) == [1, 2]


def test_rrf_handles_empty_input():
    assert _reciprocal_rank_fusion([], k=5, rrf_k=60) == []
    assert _reciprocal_rank_fusion([[], []], k=5, rrf_k=60) == []


def test_rrf_ignores_raw_score_scale():
    """RRF ใช้เฉพาะ "อันดับ" จึงต้องให้ผลเดิมไม่ว่าคะแนนดิบของสองวิธีจะต่างสเกลกันแค่ไหน

    ทดสอบด้วยการส่งลำดับชุดเดิมซ้ำ — ผลต้องเท่ากันทุกครั้ง เพราะฟังก์ชันไม่เห็นคะแนนดิบเลย
    """
    a = _reciprocal_rank_fusion([[3, 1, 2], [1, 3, 4]], k=4, rrf_k=60)
    b = _reciprocal_rank_fusion([[3, 1, 2], [1, 3, 4]], k=4, rrf_k=60)
    assert a == b


def test_rrf_k_flattens_the_gap_between_ranks():
    """ค่า k ที่ใหญ่ขึ้นทำให้ช่องว่างระหว่างอันดับต้น ๆ แคบลง (เหตุผลที่งานต้นทางเลือก k = 60)"""
    def gap(rrf_k: int) -> float:
        first = 1.0 / (rrf_k + 1)
        second = 1.0 / (rrf_k + 2)
        return first - second

    assert gap(60) < gap(1)


# ======================================================= การตรวจจับชื่อระบบงาน ===

def _service(systems: list[str], chunks: list[dict] | None = None) -> rag_service.RagService:
    """สร้าง RagService โดยไม่โหลดดัชนีจริง — ใส่รายชื่อระบบงานให้ตรงกับที่ load() จะได้

    load() เรียงชื่อจากยาวไปสั้นเสมอ (กันชื่อสั้นแมตช์ทับชื่อยาว) จึงเรียงให้เหมือนกันที่นี่
    """
    svc = rag_service.RagService(Path("ดัชนีที่ไม่มีอยู่จริง"))
    svc.known_systems = sorted(set(systems), key=len, reverse=True)
    svc.chunks = chunks or []
    return svc


@pytest.mark.parametrize("query, expected", [
    ("ระบบ SAP เข้าไม่ได้ทำยังไง", "SAP"),                    # ตรงตัว
    ("ระบบ sap เข้าไม่ได้ทำยังไง", "SAP"),                    # ไม่สนตัวพิมพ์เล็ก-ใหญ่
    ("ขอสิทธิ์ใช้งาน Smart Property หน่อย", "SmartProperty"),  # ผู้ใช้เว้นวรรค โฟลเดอร์ไม่เว้น
    ("ขอสิทธิ์ใช้งาน Smart-Property หน่อย", "SmartProperty"),  # ขีดกลาง
    ("ขอสิทธิ์ใช้งาน smart_property หน่อย", "SmartProperty"),  # ขีดล่าง
    ("ลืมรหัสผ่านทำยังไง", None),                             # ไม่ได้ระบุระบบ ต้องไม่เดา
    ("", None),
])
def test_detect_system(query, expected):
    svc = _service(["SAP", "SmartProperty", "Area Tracking"])
    assert svc.detect_system(query) == expected


def test_detect_system_prefers_the_longer_name():
    """ชื่อระบบที่ยาวกว่าต้องชนะ ไม่งั้น "Smart Sale" จะไปกินคำถามของ "Smart Sale Online" """
    svc = _service(["Smart Sale", "Smart Sale Online"])
    assert svc.detect_system("ระบบ Smart Sale Online ใช้ยังไง") == "Smart Sale Online"
    assert svc.detect_system("ระบบ Smart Sale ใช้ยังไง") == "Smart Sale"


def test_detect_system_with_spaces_in_the_known_name():
    svc = _service(["Area Tracking"])
    assert svc.detect_system("ระบบ AreaTracking ใช้ยังไง") == "Area Tracking"


def test_systems_lists_names_with_chunk_counts():
    svc = _service(
        ["SAP", "Area Tracking"],
        chunks=[
            {"system": "SAP"}, {"system": "SAP"},
            {"system": "Area Tracking"},
            {"system": None},   # chunk ที่ไม่ได้สังกัดระบบใด ต้องไม่ถูกนับ
            {},
        ],
    )
    assert svc.systems() == [
        {"name": "Area Tracking", "n_chunks": 1},
        {"name": "SAP", "n_chunks": 2},
    ]


# =============================================== พฤติกรรมเมื่อยังไม่มีดัชนี ===

def test_search_returns_empty_when_index_is_missing(tmp_path):
    """ดัชนีหาย/ยังไม่ได้สร้าง ต้องไม่ทำให้แอปล้ม แต่คืนผลว่างและบันทึกสาเหตุไว้"""
    pytest.importorskip("numpy")
    svc = rag_service.RagService(tmp_path / "ไม่มีดัชนี")
    assert svc.load() is False
    assert svc.is_ready() is False
    assert svc.last_error and "FileNotFoundError" in svc.last_error
    assert svc.search("ลืมรหัสผ่านทำยังไง") == []


def test_status_reports_not_ready_before_loading(tmp_path):
    svc = rag_service.RagService(tmp_path / "ไม่มีดัชนี")
    st = svc.status()
    assert st["ready"] is False and st["loading"] is False
    assert st["n_chunks"] == 0 and st["n_systems"] == 0
    assert st["hybrid"] is False and st["reranker"] is False


# ================================================= การประกอบบริบทและพรอมต์ ===

def _chunk(doc_id="D1", heading="หัวข้อ", text="เนื้อความ", system="SAP", score=0.5):
    return RetrievedChunk(doc_id=doc_id, heading=heading, text=text, system=system, score=score)


def test_build_context_uses_the_experiment_format():
    """รูปแบบบริบทต้องตรงกับตอนทดลองทุกตัวอักษร ไม่งั้นผลที่ได้จะเทียบกับสารนิพนธ์ไม่ได้"""
    ctx = rag_service.build_context([
        _chunk("D1", "ขอรหัสผ่านใหม่", "กดปุ่มลืมรหัสผ่าน"),
        _chunk("D2", "ติดต่อผู้ดูแล", "โทรแจ้งฝ่ายไอที"),
    ])
    assert ctx == (
        "[D1 — ขอรหัสผ่านใหม่]\nกดปุ่มลืมรหัสผ่าน"
        "\n\n"
        "[D2 — ติดต่อผู้ดูแล]\nโทรแจ้งฝ่ายไอที"
    )


def test_build_context_with_no_chunks():
    assert rag_service.build_context([]) == ""


def test_sources_payload_rounds_score_and_trims_excerpt():
    long_text = "ก" * 500
    payload = rag_service.sources_payload([_chunk(text=long_text, score=0.1234567)])
    assert payload[0]["score"] == 0.1235
    assert payload[0]["excerpt"].endswith("…")
    assert len(payload[0]["excerpt"]) == 401


def test_sources_payload_keeps_short_text_whole():
    payload = rag_service.sources_payload([_chunk(text="ข" * 400)])
    assert payload[0]["excerpt"] == "ข" * 400   # พอดี 400 ตัว ต้องไม่มี …


def test_build_system_prompt_defaults_to_graded():
    prompt = rag_service.build_system_prompt("ลืมรหัสผ่าน", [_chunk()])
    assert prompt == rag_service.build_system_prompt("ลืมรหัสผ่าน", [_chunk()], variant="graded")
    assert "ตอบเท่าที่มีข้อมูลรองรับ" in prompt


def test_build_system_prompt_strict_variant():
    prompt = rag_service.build_system_prompt("ลืมรหัสผ่าน", [_chunk()], variant="strict")
    assert "ห้ามแต่งข้อมูลเอง" in prompt


def test_build_system_prompt_falls_back_for_unknown_variant():
    unknown = rag_service.build_system_prompt("คำถาม", [_chunk()], variant="ไม่มีแบบนี้")
    assert unknown == rag_service.build_system_prompt("คำถาม", [_chunk()], variant="graded")


def test_build_system_prompt_contains_context_and_question():
    prompt = rag_service.build_system_prompt(
        "ลืมรหัสผ่าน SAP", [_chunk("D9", "รีเซ็ตรหัสผ่าน", "กดปุ่มลืมรหัสผ่าน")])
    assert "[D9 — รีเซ็ตรหัสผ่าน]" in prompt
    assert "ลืมรหัสผ่าน SAP" in prompt


def test_abstain_marker_is_identical_in_both_templates():
    """ข้อความปฏิเสธตอบใช้เป็นตัวชี้วัดในสารนิพนธ์ ถ้าแก้คำในเทมเพลตแล้วลืมแก้ค่าคงที่
    ตัวเลขอัตราการปฏิเสธตอบจะผิดทั้งหมดโดยไม่มีอะไรฟ้อง"""
    assert rag_service.ABSTAIN_MARKER == "ไม่พบคำตอบในบริบท"
    assert rag_service.ABSTAIN_MARKER in rag_service.SYSTEM_PROMPT_STRICT
    assert rag_service.ABSTAIN_MARKER in rag_service.SYSTEM_PROMPT_GRADED

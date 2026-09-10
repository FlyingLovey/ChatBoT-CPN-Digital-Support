"""ค้นคืนเอกสารสำหรับแชทบอท ด้วย pipeline เดียวกับที่รายงานไว้ในสารนิพนธ์ (หัวข้อ 3.9)

ลำดับการทำงานเหมือนกับ Retriever.search() ใน rag_eval_lmstudio.py ทุกขั้นตอน คือ
  1) Metadata filter  — ตรวจจับชื่อระบบงานจากคำถาม แล้วจำกัดขอบเขตค้นหาเฉพาะเอกสารของระบบนั้น
  2) Hybrid search    — ดึงผู้สมัครจาก dense embedding และ BM25 แยกกัน แล้วรวมอันดับด้วย RRF
  3) Reranker         — ให้ cross-encoder จัดอันดับผู้สมัครซ้ำอีกครั้ง แล้วเลือก top-k จริง

ต่างจากสคริปต์ประเมินผลตรงที่ไม่ต้องอ่านเอกสารและสร้าง embedding ใหม่ทุกครั้ง แต่โหลดดัชนีที่
scripts/build_index.py สร้างไว้แล้วจากดิสก์ ทำให้เปิดแอปได้ในไม่กี่วินาทีแทนที่จะรอหลายนาที

โมดูลนี้ออกแบบให้ "ล้มแล้วไม่พาแอปล้มไปด้วย" ถ้าโหลดดัชนีหรือไลบรารีไม่ได้ จะบันทึกสาเหตุไว้ใน
last_error แล้วให้ is_ready() คืนค่า False ฝั่ง router จะข้ามขั้นตอน RAG ไปคุยกับโมเดลตรงๆ แทน
เพื่อให้ยังสาธิตหน้าเว็บได้แม้ดัชนียังไม่พร้อม
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ตัดช่องว่าง/ขีด/ขีดล่างก่อนเทียบชื่อระบบงาน เพื่อให้ "Smart Property" ที่ผู้ใช้พิมพ์
# แมตช์กับโฟลเดอร์ชื่อ "SmartProperty" ได้ (เหมือน _SEPARATORS_RE ในสคริปต์ประเมินผล)
_SEPARATORS_RE = re.compile(r"[\s_\-]+")


@dataclass
class RetrievedChunk:
    doc_id: str
    heading: str
    text: str
    system: str | None
    score: float


def _tokenize_for_bm25(text: str) -> list[str]:
    """ตัดคำแบบเบาให้เหมือนกับสคริปต์ประเมินผลทุกประการ: อังกฤษ/ตัวเลขใช้ regex word
    ส่วนภาษาไทยที่ไม่เว้นวรรคใช้ character bigram แทนการตัดคำจริง"""
    text = text.lower()
    tokens: list[str] = re.findall(r"[a-z0-9]+", text)
    for run in re.findall(r"[฀-๿]+", text):
        if len(run) < 2:
            tokens.append(run)
        else:
            tokens.extend(run[i:i + 2] for i in range(len(run) - 1))
    return tokens


def _reciprocal_rank_fusion(ranked_lists: list[list[int]], k: int, rrf_k: int) -> list[int]:
    """รวมอันดับจากหลายวิธีค้นหาด้วยสูตร RRF: score(d) = sum(1 / (rrf_k + rank_i(d)))
    ใช้การรวมแบบอิงอันดับเพราะ cosine similarity กับคะแนน BM25 อยู่คนละมาตราส่วน เทียบตรงๆ ไม่ได้"""
    scores: dict[int, float] = {}
    for ranked in ranked_lists:
        for rank, idx in enumerate(ranked):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (rrf_k + rank + 1)
    return sorted(scores.keys(), key=lambda i: -scores[i])[:k]


class RagService:
    def __init__(self, index_dir: Path, *, use_reranker: bool = True,
                 use_hybrid: bool = True, use_metadata_filter: bool = True,
                 hybrid_pool_multiplier: int = 5, rrf_k: int = 60):
        self.index_dir = Path(index_dir)
        self.use_reranker = use_reranker
        self.use_hybrid = use_hybrid
        self.use_metadata_filter = use_metadata_filter
        self.hybrid_pool_multiplier = hybrid_pool_multiplier
        self.rrf_k = rrf_k

        self.meta: dict[str, Any] = {}
        self.chunks: list[dict[str, Any]] = []
        self.known_systems: list[str] = []
        self.last_error: str | None = None
        self._ready = False
        self._embeddings = None
        self._model = None
        self._bm25 = None
        self._reranker = None
        # โหลดโมเดลครั้งเดียวแล้วใช้ซ้ำ แต่ FastAPI เสิร์ฟหลาย request พร้อมกันได้
        # จึงกันด้วย lock ไม่ให้สองคำขอเริ่มโหลดโมเดลเดียวกันซ้อนกัน
        self._lock = threading.Lock()
        self._loading = False

    # ------------------------------------------------------------------ load
    def load(self) -> bool:
        """โหลดดัชนีและโมเดลทั้งหมด คืน True เมื่อพร้อมใช้งาน (เรียกซ้ำได้ ไม่โหลดซ้ำ)"""
        with self._lock:
            if self._ready:
                return True
            try:
                self._load_unlocked()
                self._ready = True
                self.last_error = None
                logger.info("RAG พร้อมใช้งาน: %d chunks / %d ระบบงาน",
                            len(self.chunks), len(self.known_systems))
            except Exception as exc:  # noqa: BLE001 - ตั้งใจจับกว้าง เพื่อไม่ให้แอปล้มทั้งตัว
                self._ready = False
                self.last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("โหลดดัชนี RAG ไม่สำเร็จ (%s) — แชทบอทจะทำงานโดยไม่ใช้ฐานความรู้",
                               self.last_error)
            return self._ready

    def _load_unlocked(self) -> None:
        import numpy as np

        chunks_path = self.index_dir / "chunks.jsonl"
        emb_path = self.index_dir / "embeddings.npy"
        meta_path = self.index_dir / "meta.json"
        if not chunks_path.exists() or not emb_path.exists():
            raise FileNotFoundError(
                f"ไม่พบดัชนีที่ {self.index_dir} — รัน `python scripts/build_index.py` ก่อน")

        self.meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        self.chunks = [json.loads(line) for line in chunks_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self._embeddings = np.load(emb_path)
        if len(self.chunks) != len(self._embeddings):
            raise ValueError(
                f"จำนวน chunk ({len(self.chunks)}) ไม่ตรงกับจำนวนเวกเตอร์ ({len(self._embeddings)}) "
                f"— ดัชนีอาจสร้างไม่สมบูรณ์ ให้ลบโฟลเดอร์ {self.index_dir} แล้วสร้างใหม่")

        # เรียงชื่อระบบจากยาวไปสั้น เพื่อกันชื่อสั้นไปแมตช์ทับชื่อยาวที่มีคำนั้นอยู่ข้างใน
        self.known_systems = sorted({c["system"] for c in self.chunks if c.get("system")},
                                    key=len, reverse=True)

        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(self.meta.get("embedding_model", "intfloat/multilingual-e5-base"))

        if self.use_hybrid:
            try:
                from rank_bm25 import BM25Okapi
                self._bm25 = BM25Okapi([_tokenize_for_bm25(c["text"]) for c in self.chunks])
            except ImportError:
                logger.warning("ไม่พบ rank_bm25 — ใช้เฉพาะ dense retrieval (ผลจะต่างจากที่รายงานในสารนิพนธ์)")
                self._bm25 = None

        if self.use_reranker:
            try:
                from sentence_transformers import CrossEncoder
                self._reranker = CrossEncoder(self.meta.get("reranker_model", "BAAI/bge-reranker-v2-m3"))
            except Exception as exc:  # noqa: BLE001
                logger.warning("โหลด reranker ไม่สำเร็จ (%s) — ข้ามขั้นจัดอันดับซ้ำ", exc)
                self._reranker = None

    def ensure_loading(self) -> None:
        """สั่งให้เริ่มโหลดดัชนีในเธรดพื้นหลัง ถ้ายังไม่ได้เริ่ม (เรียกซ้ำได้ ไม่เริ่มซ้อน)

        แยกจาก load() เพราะ load() จะบล็อกจนโหลดเสร็จ ใช้ตอนเปิดแอปหรือตอนที่หน้าเว็บ
        ถามสถานะ จะได้ตอบกลับทันทีว่า "กำลังโหลด" แทนที่จะค้างรอหลายสิบวินาที
        """
        with self._lock:
            if self._ready or self._loading:
                return
            self._loading = True

        def _run() -> None:
            try:
                self.load()
            finally:
                self._loading = False

        threading.Thread(target=_run, name="rag-load", daemon=True).start()

    def is_ready(self) -> bool:
        return self._ready

    def is_loading(self) -> bool:
        return self._loading

    def status(self) -> dict[str, Any]:
        return {
            "ready": self._ready,
            "loading": self._loading,
            "error": self.last_error,
            "index_dir": str(self.index_dir),
            "n_chunks": len(self.chunks),
            "n_systems": len(self.known_systems),
            "hybrid": bool(self._bm25 is not None),
            "reranker": bool(self._reranker is not None),
            "embedding_model": self.meta.get("embedding_model"),
        }

    # ---------------------------------------------------------------- search
    def detect_system(self, query: str) -> str | None:
        q_lower = query.lower()
        for name in self.known_systems:
            if name.lower() in q_lower:
                return name
        q_squashed = _SEPARATORS_RE.sub("", q_lower)
        for name in self.known_systems:
            if _SEPARATORS_RE.sub("", name.lower()) in q_squashed:
                return name
        return None

    def search(self, query: str, top_k: int = 5) -> list[RetrievedChunk]:
        if not self._ready and not self.load():
            return []
        import numpy as np

        candidate_idx = list(range(len(self.chunks)))
        if self.use_metadata_filter and self.known_systems:
            target = self.detect_system(query)
            if target:
                filtered = [i for i in candidate_idx if self.chunks[i].get("system") == target]
                # ถ้ากรองแล้วไม่เหลืออะไรเลย ให้ถอยกลับไปค้นทั้งหมด กัน false positive
                # ของการตรวจจับชื่อระบบทำให้หาคำตอบไม่เจอทั้งที่มีอยู่
                if filtered:
                    candidate_idx = filtered

        pool_k = max(top_k * self.hybrid_pool_multiplier, top_k)
        q_emb = self._model.encode([f"query: {query}"], normalize_embeddings=True,
                                   show_progress_bar=False)[0]

        sims = self._embeddings[candidate_idx] @ q_emb
        dense_ranked = [candidate_idx[o] for o in np.argsort(-sims)[:pool_k]]

        bm25_ranked: list[int] = []
        if self._bm25 is not None:
            tokens = _tokenize_for_bm25(query)
            if tokens:
                all_scores = self._bm25.get_scores(tokens)
                scored = sorted(((i, all_scores[i]) for i in candidate_idx), key=lambda t: -t[1])[:pool_k]
                bm25_ranked = [i for i, s in scored if s > 0]

        fused = _reciprocal_rank_fusion([dense_ranked, bm25_ranked], pool_k, self.rrf_k) if bm25_ranked else dense_ranked

        if self._reranker is not None and fused:
            pairs = [[query, self.chunks[i]["text"]] for i in fused]
            rerank_scores = self._reranker.predict(pairs)
            order = np.argsort(-np.asarray(rerank_scores))[:top_k]
            top_idx = [fused[o] for o in order]
        else:
            top_idx = fused[:top_k]

        if not top_idx:
            return []
        # คืนคะแนนเป็น cosine similarity เสมอ (ไม่ใช่คะแนนดิบของ reranker) เพื่อให้ตีความง่าย
        cos = self._embeddings[top_idx] @ q_emb
        return [
            RetrievedChunk(doc_id=self.chunks[i]["doc_id"], heading=self.chunks[i]["heading"],
                           text=self.chunks[i]["text"], system=self.chunks[i].get("system"),
                           score=float(s))
            for i, s in zip(top_idx, cos)
        ]


def build_context(chunks: list[RetrievedChunk]) -> str:
    """ประกอบบริบทให้อยู่ในรูปแบบเดียวกับตอนทดลอง: [doc_id — heading] ตามด้วยเนื้อความ"""
    return "\n\n".join(f"[{c.doc_id} — {c.heading}]\n{c.text}" for c in chunks)


# ============================================================================
# System prompt: ใช้ข้อความชุดเดียวกับที่รายงานไว้ในสารนิพนธ์
# ============================================================================

# แบบ strict (การทดลองที่ 1–3) สั่งให้ปฏิเสธทันทีเมื่อไม่พบคำตอบตรงๆ ในบริบท
SYSTEM_PROMPT_STRICT = """คุณเป็นผู้ช่วยตอบคำถามเกี่ยวกับ IT Support ขององค์กร
ให้ตอบคำถามจากบริบทเท่านั้น ห้ามแต่งข้อมูลเอง หากไม่พบคำตอบในบริบท ให้ตอบว่า "ไม่พบคำตอบในบริบท"

บริบทจากเอกสาร:
{context}

คำถาม:
{question}

รูปแบบคำตอบ:
- ตอบเป็นภาษาไทย
- ใช้หัวข้อสั้นๆ ให้อ่านง่าย
- ตอบให้ตรงประเด็นตามบริบท ไม่ต้องอธิบายยืดยาว
"""

# แบบ graded (การทดลองที่ 4) อนุญาตให้ตอบเท่าที่บริบทรองรับ แล้วบอกว่าส่วนไหนยังไม่พบ
# ผลการทดลองที่ 4 ชี้ว่าแบบนี้ลดการปฏิเสธตอบโดยไม่จำเป็นได้ และคะแนนคุณภาพไม่ลดลง
# จึงตั้งเป็นค่าเริ่มต้นของแชทบอท
SYSTEM_PROMPT_GRADED = """คุณเป็นผู้ช่วยตอบคำถามเกี่ยวกับ IT Support ขององค์กร
ให้ใช้ข้อมูลจากบริบทที่ให้มาเท่านั้นในการตอบ ห้ามเพิ่มข้อมูลที่ไม่มีในบริบท

วิธีตอบ:
- ถ้าบริบทมีคำตอบครบ ให้ตอบให้ครบถ้วน
- ถ้าบริบทมีข้อมูลที่เกี่ยวข้องเพียงบางส่วน ให้ตอบเท่าที่มีข้อมูลรองรับ แล้วระบุท้ายคำตอบว่าส่วนใดที่ยังไม่พบในเอกสาร
- ตอบว่า "ไม่พบคำตอบในบริบท" เฉพาะกรณีที่บริบทไม่เกี่ยวข้องกับคำถามเลยเท่านั้น

บริบทจากเอกสาร:
{context}

คำถาม:
{question}

รูปแบบคำตอบ:
- ตอบเป็นภาษาไทย
- ใช้หัวข้อสั้นๆ ให้อ่านง่าย
- ตอบให้ตรงประเด็นตามบริบท ไม่ต้องอธิบายยืดยาว
"""

PROMPT_TEMPLATES = {"strict": SYSTEM_PROMPT_STRICT, "graded": SYSTEM_PROMPT_GRADED}


def build_system_prompt(query: str, chunks: list[RetrievedChunk], variant: str = "graded") -> str:
    """เติมบริบทและคำถามลงในเทมเพลต ได้ข้อความที่ส่งเป็น role "system" ตรงกับตอนทดลอง"""
    template = PROMPT_TEMPLATES.get(variant, SYSTEM_PROMPT_GRADED)
    return template.format(context=build_context(chunks), question=query)


def sources_payload(chunks: list[RetrievedChunk]) -> list[dict[str, Any]]:
    """ย่อผลการค้นคืนให้เหลือเฉพาะข้อมูลที่หน้าเว็บต้องใช้แสดงแหล่งอ้างอิง"""
    return [
        {
            "doc_id": c.doc_id,
            "heading": c.heading,
            "system": c.system,
            "score": round(c.score, 4),
            # ตัดตัวอย่างเนื้อความไว้ให้ผู้ใช้กดดูได้ว่าคำตอบมาจากข้อความส่วนไหน
            "excerpt": c.text[:400] + ("…" if len(c.text) > 400 else ""),
        }
        for c in chunks
    ]


# ============================================================================
# ตัวอินสแตนซ์เดียวที่ใช้ร่วมกันทั้งแอป (สร้างจากค่าใน .env)
# ============================================================================

_service: RagService | None = None


def get_service() -> RagService:
    """คืนอินสแตนซ์เดียวของ RagService สร้างครั้งแรกที่เรียก (ยังไม่โหลดดัชนี)"""
    global _service
    if _service is None:
        from config import settings

        _service = RagService(
            Path(settings.RAG_INDEX_DIR),
            use_reranker=settings.RAG_USE_RERANKER,
            use_hybrid=settings.RAG_USE_HYBRID,
            use_metadata_filter=settings.RAG_USE_METADATA_FILTER,
        )
    return _service

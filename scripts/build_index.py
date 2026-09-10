"""สร้างดัชนีค้นคืน (RAG index) สำหรับแชทบอท แล้วบันทึกลงดิสก์ไว้ให้แอปโหลดตอนเปิด

ทำไมต้องแยกเป็นสคริปต์ต่างหาก: การอ่านเอกสารจริงทั้งหมด (PDF/DOCX/XLSX/อีเมล) แล้วสร้าง
เวกเตอร์ตัวแทนความหมายใช้เวลาหลายนาที ถ้าให้แอปทำตอนเปิดทุกครั้ง เว็บจะเปิดช้ามากและ
เสี่ยง timeout ตอนสาธิต จึงสร้างครั้งเดียวเก็บเป็นไฟล์ แล้วแอปแค่โหลดไฟล์ที่สร้างไว้

สคริปต์นี้เรียกใช้ฟังก์ชันโหลดและตัด chunk จาก rag_eval_lmstudio.py โดยตรง (ไม่คัดลอกโค้ดมา
เขียนใหม่) เพื่อรับประกันว่าฐานความรู้ที่แชทบอทใช้ เป็นชุดเดียวกับที่รายงานผลไว้ในสารนิพนธ์
ทุกประการ ทั้งการตัด chunk การกำกับชื่อระบบงาน และการแก้อักขระภาษาไทยที่เพี้ยนจากการสกัด PDF

วิธีรัน (จากโฟลเดอร์โปรเจกต์):
    python scripts/build_index.py
หรือระบุ path เองเมื่อโครงสร้างโฟลเดอร์ต่างจากค่าเริ่มต้น:
    python scripts/build_index.py --eval-script "D:/Project IS/Model AI/lm_studio_eval/rag_eval_lmstudio.py"
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import types
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EVAL_SCRIPT = PROJECT_ROOT.parent.parent / "Model AI" / "lm_studio_eval" / "rag_eval_lmstudio.py"


def load_eval_module(path: Path):
    """โหลด rag_eval_lmstudio.py เข้ามาเป็นโมดูล

    สคริปต์นั้น import httpx ไว้ตั้งแต่ต้นไฟล์เพื่อใช้คุยกับ LM Studio ซึ่งขั้นตอนสร้างดัชนี
    ไม่ได้ใช้เลย จึงใส่โมดูลหลอกแทนไว้ก่อน เพื่อไม่บังคับให้เครื่องที่สร้างดัชนีต้องติดตั้ง httpx
    """
    if "httpx" not in sys.modules:
        stub = types.ModuleType("httpx")

        class _Timeout:  # noqa: D401 - แทนที่ httpx.Timeout ที่ถูกเรียกตอน import
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        stub.Timeout = _Timeout
        stub.Client = object
        sys.modules["httpx"] = stub

    if not path.exists():
        raise SystemExit(
            f"[fail] หาไฟล์ rag_eval_lmstudio.py ไม่เจอที่ {path}\n"
            f"       ระบุ path เองด้วย --eval-script"
        )
    spec = importlib.util.spec_from_file_location("rag_eval_lmstudio", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["rag_eval_lmstudio"] = module
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser(description="สร้างดัชนีค้นคืนสำหรับแชทบอท")
    parser.add_argument("--eval-script", type=Path, default=DEFAULT_EVAL_SCRIPT,
                        help="path ของ rag_eval_lmstudio.py ที่ใช้เป็นต้นทางของ pipeline")
    parser.add_argument("--out", type=Path, default=PROJECT_ROOT / "rag_index",
                        help="โฟลเดอร์ปลายทางที่จะเก็บดัชนี")
    parser.add_argument("--batch-size", type=int, default=32,
                        help="ขนาด batch ตอนสร้าง embedding (ลดลงถ้าหน่วยความจำไม่พอ)")
    args = parser.parse_args()

    print(f"[1/4] โหลด pipeline จาก {args.eval_script}")
    rag = load_eval_module(args.eval_script)

    print(f"[2/4] อ่านเอกสารและตัด chunk จาก {rag.APPLICATIONS_DIR}")
    chunks = rag.load_and_chunk_applications(rag.APPLICATIONS_DIR)
    if not chunks:
        raise SystemExit("[fail] ไม่ได้ chunk ใดเลย ตรวจสอบว่าโฟลเดอร์ knowledge_base/Applications มีเอกสารอยู่จริง")
    print(f"      ได้ {len(chunks)} chunks จาก {len({c.doc_id for c in chunks})} เอกสาร")

    print(f"[3/4] สร้าง embedding ด้วย {rag.EMBEDDING_MODEL_NAME} (ครั้งแรกจะดาวน์โหลดโมเดลก่อน)")
    from sentence_transformers import SentenceTransformer
    import numpy as np

    model = SentenceTransformer(rag.EMBEDDING_MODEL_NAME)
    # ใส่ prefix "passage: " ให้ตรงกับที่ Retriever ในสคริปต์ประเมินผลใช้ ไม่งั้นเวกเตอร์ของ
    # เอกสารกับของคำถามจะอยู่คนละปริภูมิย่อย ทำให้คะแนนความคล้ายต่ำผิดปกติ
    texts = [f"passage: {c.text}" for c in chunks]
    embeddings = model.encode(texts, batch_size=args.batch_size, normalize_embeddings=True,
                              show_progress_bar=True)
    embeddings = np.asarray(embeddings, dtype="float32")

    print(f"[4/4] บันทึกดัชนีลง {args.out}")
    args.out.mkdir(parents=True, exist_ok=True)
    np.save(args.out / "embeddings.npy", embeddings)
    with open(args.out / "chunks.jsonl", "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps({"doc_id": c.doc_id, "heading": c.heading,
                                "text": c.text, "system": c.system}, ensure_ascii=False) + "\n")
    meta = {
        "embedding_model": rag.EMBEDDING_MODEL_NAME,
        "reranker_model": rag.RERANKER_MODEL_NAME,
        "n_chunks": len(chunks),
        "n_docs": len({c.doc_id for c in chunks}),
        "systems": sorted({c.system for c in chunks if c.system}),
        "source_dir": str(rag.APPLICATIONS_DIR),
        "rrf_k": rag.RRF_K,
        "hybrid_pool_multiplier": rag.HYBRID_POOL_MULTIPLIER,
    }
    with open(args.out / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"\nเสร็จแล้ว: {len(chunks)} chunks / {meta['n_docs']} เอกสาร / {len(meta['systems'])} ระบบงาน")
    print(f"ดัชนีอยู่ที่ {args.out.resolve()}  —  ตั้ง RAG_INDEX_DIR ใน .env ให้ตรงกับ path นี้")


if __name__ == "__main__":
    main()

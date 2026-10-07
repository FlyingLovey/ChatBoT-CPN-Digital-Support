"""แยก chunks.jsonl ออกเป็นไฟล์รายระบบงานที่คนอ่านได้

ทำไมต้องมี: chunks.jsonl เป็น JSON บรรทัดละเรคคอร์ด เปิดอ่านด้วยตาไม่ไหว (882 บรรทัด
แต่ละบรรทัดยาวเป็นพันตัวอักษร) แต่มันคือ "สิ่งที่แชทบอทรู้จริงๆ" ทั้งหมด การได้อ่าน
ในรูปแบบปกติทำให้ตรวจได้ว่า

  - เอกสารของแต่ละระบบถูกสกัดข้อความออกมาครบไหม
  - ภาษาไทยเพี้ยนจากการอ่าน PDF หรือเปล่า
  - มี chunk สั้นๆ ที่ไม่มีประโยชน์ปนอยู่มากแค่ไหน

ผลลัพธ์:
    rag_index/by_system/_index.md          สรุปภาพรวมทุกระบบ
    rag_index/by_system/<ระบบ>.md          เนื้อหาทั้งหมดของระบบนั้น จัดกลุ่มตามเอกสาร
    rag_index/by_system/chunks.csv         ทุก chunk ในรูปตาราง เปิดใน Excel ได้

วิธีรัน (จากโฟลเดอร์โปรเจกต์):
    .\\.venv\\Scripts\\python.exe scripts\\export_chunks.py
    .\\.venv\\Scripts\\python.exe scripts\\export_chunks.py --system SAP

ใช้เฉพาะไลบรารีมาตรฐานของ Python ไม่ต้องติดตั้งอะไรเพิ่ม
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ความยาวที่ถือว่า chunk สั้นเกินจนแทบไม่มีประโยชน์ในการค้นคืน
SHORT_CHUNK_CHARS = 50

# อักขระที่ใช้ในชื่อไฟล์บน Windows ไม่ได้
_UNSAFE = re.compile(r'[<>:"/\\|?*]')

# อักขระควบคุมที่ติดมาจากการสกัดข้อความจาก PDF/DOCX — พบจริงในฐานความรู้ชุดนี้
# ตัวที่เป็นปัญหาที่สุดคือ NUL (\x00) ซึ่งโมดูล csv ของ Python ปฏิเสธที่จะเขียน
# และทำให้โปรแกรมดูไฟล์หลายตัวแสดงผลเพี้ยน เก็บ \n และ \t ไว้เพราะเป็นการจัดรูปแบบจริง
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(text: str) -> tuple[str, bool]:
    """ล้างอักขระควบคุมออก คืน (ข้อความที่สะอาด, เคยมีอักขระควบคุมไหม)

    ล้างเฉพาะตอนส่งออกเท่านั้น ไม่แตะดัชนีจริง เพราะดัชนีต้องคงเหมือนกับที่ใช้
    ในการทดลองของสารนิพนธ์ทุกตัวอักษร
    """
    cleaned = _CONTROL.sub("", text)
    return cleaned, cleaned != text


def safe_filename(name: str) -> str:
    """แปลงชื่อระบบงานให้ใช้เป็นชื่อไฟล์ได้ (เช่น 'CMS (Coupon)' มีวงเล็บซึ่งใช้ได้
    แต่บางระบบอาจมี / หรือ : ซึ่งใช้ไม่ได้บน Windows)"""
    cleaned = _UNSAFE.sub("-", name).strip().rstrip(".")
    return cleaned or "unknown"


def load_chunks(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(
            f"[fail] ไม่พบ {path}\n"
            f"       สร้างดัชนีก่อนด้วย: .\\.venv\\Scripts\\python.exe scripts\\build_index.py"
        )
    # ต้องแยกด้วย "\n" เท่านั้น ห้ามใช้ splitlines() เพราะเอกสารมีอักขระ U+2028
    # ปนอยู่ ซึ่ง splitlines() นับเป็นการขึ้นบรรทัดใหม่ แล้วจะผ่าเรคคอร์ดขาดกลางสตริง
    raw = path.read_text(encoding="utf-8")
    out = []
    for i, line in enumerate(raw.split("\n"), 1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as exc:
            print(f"  [warn] ข้ามบรรทัดที่ {i} ที่อ่านไม่ได้: {exc}")
    return out


def write_system_file(out_dir: Path, system: str, chunks: list[dict]) -> Path:
    """เขียนไฟล์ Markdown หนึ่งไฟล์ต่อหนึ่งระบบงาน จัดกลุ่มตามเอกสารต้นทาง"""
    by_doc: dict[str, list[dict]] = defaultdict(list)
    for c in chunks:
        by_doc[c.get("doc_id", "(ไม่ระบุเอกสาร)")].append(c)

    total_chars = sum(len(c.get("text", "")) for c in chunks)
    n_short = sum(1 for c in chunks if len(c.get("text", "")) < SHORT_CHUNK_CHARS)

    lines: list[str] = [
        f"# {system}",
        "",
        f"- เอกสาร: **{len(by_doc)}** ฉบับ",
        f"- chunk: **{len(chunks)}** ชิ้น",
        f"- ตัวอักษรรวม: **{total_chars:,}**",
    ]
    if n_short:
        lines.append(
            f"- chunk สั้นกว่า {SHORT_CHUNK_CHARS} ตัวอักษร: **{n_short}** ชิ้น "
            f"(มักเป็นเศษจากการตัด ไม่ค่อยมีประโยชน์ในการค้นคืน)"
        )
    lines += ["", "---", ""]

    for doc_id in sorted(by_doc):
        items = by_doc[doc_id]
        lines += [f"## {doc_id}", "", f"*{len(items)} chunk*", ""]
        for n, c in enumerate(items, 1):
            heading, _ = clean_text(c.get("heading", "") or "(ไม่มีหัวข้อ)")
            text, had_control = clean_text((c.get("text", "") or "").strip())
            flag = "  ⚠️ สั้นผิดปกติ" if len(text) < SHORT_CHUNK_CHARS else ""
            if had_control:
                flag += "  ⚠️ มีอักขระควบคุมปนมา (ล้างออกในไฟล์นี้แล้ว)"
            lines += [
                f"### {n}. {heading}",
                "",
                f"`{len(text)} ตัวอักษร`{flag}",
                "",
                "```",
                text if text else "(ว่าง)",
                "```",
                "",
            ]
        lines.append("---")
        lines.append("")

    path = out_dir / f"{safe_filename(system)}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_index(out_dir: Path, groups: dict[str, list[dict]]) -> Path:
    total_chunks = sum(len(v) for v in groups.values())
    total_docs = len({c.get("doc_id") for v in groups.values() for c in v})

    lines = [
        "# สารบัญฐานความรู้",
        "",
        f"แยกจาก `rag_index/chunks.jsonl` — **{total_chunks:,} chunk** จาก "
        f"**{total_docs}** เอกสาร ใน **{len(groups)}** ระบบงาน",
        "",
        "| ระบบงาน | เอกสาร | chunk | ตัวอักษร | chunk สั้น | ไฟล์ |",
        "|---|---:|---:|---:|---:|---|",
    ]
    # เรียงจาก chunk มากไปน้อย เห็นได้ทันทีว่าระบบไหนมีข้อมูลเยอะ/น้อย
    for system in sorted(groups, key=lambda s: -len(groups[s])):
        items = groups[system]
        docs = len({c.get("doc_id") for c in items})
        chars = sum(len(c.get("text", "")) for c in items)
        short = sum(1 for c in items if len(c.get("text", "")) < SHORT_CHUNK_CHARS)
        fname = f"{safe_filename(system)}.md"
        # ชื่อไฟล์มีช่องว่าง (เช่น "Travel Agent Incentive.md") ต้องครอบด้วย <> ตาม
        # CommonMark ไม่งั้นลิงก์จะขาดตรงช่องว่างแรกและกดไม่ติด
        lines.append(
            f"| {system} | {docs} | {len(items)} | {chars:,} | {short} | [{fname}](<./{fname}>) |"
        )

    lines += [
        "",
        "## อ่านตารางนี้ยังไง",
        "",
        "- **chunk น้อยผิดปกติ** เทียบกับจำนวนเอกสาร มักแปลว่าเอกสารของระบบนั้นเป็น PDF",
        "  ที่สแกนมาเป็นรูป ซึ่งสกัดข้อความไม่ได้ แชทบอทจึงตอบเรื่องนั้นไม่ได้",
        f"- **chunk สั้น** คือชิ้นที่สั้นกว่า {SHORT_CHUNK_CHARS} ตัวอักษร เป็นเศษจากการตัด",
        "  กินที่ในผลค้นคืนโดยไม่ให้ข้อมูลอะไร",
        "- เปิดไฟล์ของแต่ละระบบเพื่ออ่านเนื้อหาจริงที่แชทบอทใช้ตอบ",
        "",
    ]
    path = out_dir / "_index.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_csv(out_dir: Path, chunks: list[dict]) -> tuple[Path, int]:
    path = out_dir / "chunks.csv"
    n_dirty = 0
    # utf-8-sig = UTF-8 + BOM ให้ Excel บน Windows อ่านภาษาไทยถูก
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ลำดับ", "ระบบงาน", "เอกสาร", "หัวข้อ", "จำนวนตัวอักษร", "เนื้อหา"])
        for i, c in enumerate(chunks, 1):
            text, had_control = clean_text((c.get("text", "") or "").strip())
            heading, _ = clean_text(c.get("heading", "") or "")
            if had_control:
                n_dirty += 1
            w.writerow([i, c.get("system", ""), c.get("doc_id", ""),
                        heading, len(text), text])
    return path, n_dirty


def main() -> None:
    ap = argparse.ArgumentParser(description="แยก chunks.jsonl เป็นไฟล์รายระบบงานที่อ่านได้")
    ap.add_argument("--index", type=Path, default=PROJECT_ROOT / "rag_index" / "chunks.jsonl")
    ap.add_argument("--out", type=Path, default=PROJECT_ROOT / "rag_index" / "by_system")
    ap.add_argument("--system", type=str, default=None,
                    help="ทำเฉพาะระบบเดียว (ชื่อต้องตรงกับใน dropdown)")
    ap.add_argument("--no-csv", action="store_true", help="ไม่ต้องสร้างไฟล์ CSV")
    args = ap.parse_args()

    chunks = load_chunks(args.index)
    if not chunks:
        raise SystemExit("[fail] ไม่มี chunk ในไฟล์ดัชนี")

    groups: dict[str, list[dict]] = defaultdict(list)
    for c in chunks:
        groups[c.get("system") or "(ไม่ระบุระบบ)"].append(c)

    if args.system:
        if args.system not in groups:
            print(f"[fail] ไม่พบระบบงานชื่อ '{args.system}' — ที่มีอยู่คือ:")
            for s in sorted(groups):
                print(f"    {s}")
            sys.exit(1)
        groups = {args.system: groups[args.system]}

    args.out.mkdir(parents=True, exist_ok=True)
    print(f"อ่าน {len(chunks):,} chunk จาก {args.index}")
    print(f"เขียนลง {args.out}\n")

    for system in sorted(groups, key=lambda s: -len(groups[s])):
        p = write_system_file(args.out, system, groups[system])
        docs = len({c.get("doc_id") for c in groups[system]})
        print(f"  {len(groups[system]):4d} chunk / {docs:3d} เอกสาร  ->  {p.name}")

    if not args.system:
        idx = write_index(args.out, groups)
        print(f"\n  สารบัญ -> {idx.name}")

    if not args.no_csv:
        selected = [c for v in groups.values() for c in v]
        csv_path, n_dirty = write_csv(args.out, selected)
        print(f"  ตาราง  -> {csv_path.name}  ({len(selected):,} แถว)")
        if n_dirty:
            print(f"\n[!] {n_dirty} chunk มีอักขระควบคุมปนมาจากการสกัดเอกสาร (ล้างออกในไฟล์ส่งออกแล้ว)")
            print("    ในดัชนีจริงยังอยู่เหมือนเดิม เพื่อให้ตรงกับที่ใช้ทดลองในสารนิพนธ์")

    # เตือนเรื่องคุณภาพข้อมูล ซึ่งเป็นเหตุผลหลักที่ทำสคริปต์นี้
    thin = [s for s, v in groups.items() if len(v) <= 3]
    if thin:
        print(f"\n[!] ระบบที่มี chunk น้อยมาก (<= 3): {', '.join(sorted(thin))}")
        print("    มักแปลว่าเอกสารเป็น PDF สแกนที่สกัดข้อความไม่ได้ — แชทบอทจะตอบเรื่องนี้ไม่ได้")


if __name__ == "__main__":
    main()

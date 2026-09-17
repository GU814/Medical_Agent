"""分块：支持多种策略（按段落聚合 / 定长 / 按句），并给出分块质量统计。

策略说明：
- paragraph：先按段落聚合成不超过 size 的段，超长段再滑窗切分（默认，兼顾语义完整）。
- fixed：严格按字符定长切分并保留 overlap（最可控，但可能切断语义）。
- sentence：先按句切分，再聚合到 size，超长句滑窗（语义边界最好）。
"""
import re

from .. import config

_SENT_RE = re.compile(r"(?<=[。！？;；\n])")


def _split_sentences(text: str) -> list[str]:
    parts = _SENT_RE.split(text)
    out = []
    for p in parts:
        p = p.strip()
        if p:
            out.append(p)
    return out


def chunk_text(text: str, size: int = None, overlap: int = None,
               strategy: str = "paragraph") -> list[str]:
    size = size or config.CHUNK_SIZE
    overlap = overlap if overlap is not None else config.CHUNK_OVERLAP
    text = (text or "").strip()
    if not text:
        return []

    if strategy == "fixed":
        return _chunk_fixed(text, size, overlap)
    if strategy == "sentence":
        return _chunk_sentence(text, size, overlap)
    return _chunk_paragraph(text, size, overlap)


def _chunk_paragraph(text: str, size: int, overlap: int) -> list[str]:
    blocks: list[str] = []
    cur = ""
    for para in text.split("\n"):
        para = para.strip()
        if not para:
            continue
        if len(cur) + len(para) + 1 <= size:
            cur = f"{cur}\n{para}".strip()
        else:
            if cur:
                blocks.append(cur)
            cur = para
    if cur:
        blocks.append(cur)
    return _sliding(blocks, size, overlap)


def _chunk_sentence(text: str, size: int, overlap: int) -> list[str]:
    sents = _split_sentences(text)
    blocks: list[str] = []
    cur = ""
    for s in sents:
        if len(s) > size:  # 超长单句直接滑窗
            if cur:
                blocks.append(cur)
                cur = ""
            blocks.extend(_sliding([s], size, overlap))
            continue
        if len(cur) + len(s) + 1 <= size:
            cur = f"{cur} {s}".strip()
        else:
            if cur:
                blocks.append(cur)
            cur = s
    if cur:
        blocks.append(cur)
    return blocks


def _chunk_fixed(text: str, size: int, overlap: int) -> list[str]:
    step = max(size - overlap, 1)
    chunks: list[str] = []
    for i in range(0, len(text), step):
        chunk = text[i:i + size]
        if chunk:
            chunks.append(chunk)
        if i + size >= len(text):
            break
    return chunks


def _sliding(blocks: list[str], size: int, overlap: int) -> list[str]:
    chunks: list[str] = []
    for b in blocks:
        if len(b) <= size:
            chunks.append(b)
            continue
        step = max(size - overlap, 1)
        for i in range(0, len(b), step):
            chunks.append(b[i:i + size])
            if i + size >= len(b):
                break
    return chunks


def chunk_stats(chunks: list[str]) -> dict:
    """分块质量统计：数量、长度分布、过短/过长占比、相邻重叠。"""
    n = len(chunks)
    if n == 0:
        return {"count": 0, "avg_len": 0, "min_len": 0, "max_len": 0,
                "too_short": 0, "too_short_pct": 0.0, "too_long": 0, "overlap_avg": 0.0}
    lengths = [len(c) for c in chunks]
    avg = sum(lengths) / n
    too_short = sum(1 for l in lengths if l < 40)
    too_long = sum(1 for c in chunks if len(c) >= config.CHUNK_SIZE)
    overlap_sum = 0
    ov_count = 0
    for i in range(1, n):
        a, b = chunks[i - 1], chunks[i]
        m = min(len(a), len(b), 50)
        ov = 0
        while ov < m and a[len(a) - m + ov] == b[ov]:
            ov += 1
        if ov > 0:
            overlap_sum += ov
            ov_count += 1
    return {
        "count": n,
        "avg_len": round(avg, 1),
        "min_len": min(lengths),
        "max_len": max(lengths),
        "too_short": too_short,
        "too_short_pct": round(too_short / n * 100, 1),
        "too_long": too_long,
        "overlap_avg": round(overlap_sum / ov_count, 1) if ov_count else 0.0,
    }

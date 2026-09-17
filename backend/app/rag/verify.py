"""检索验证：检测已入库内容是否可被检索到，提供命中状态与覆盖率反馈。

两种用法：
- verify(user_id, queries)：在真实索引（含可选语义加权）上验证，适合「手动输入查询验证」。
- verify_with_index(index, queries)：在给定（临时）索引上验证，适合「分块策略效果对比」。
"""
import re

from .. import config
from . import indexer

_SENT_SPLIT = re.compile(r"(?<=[。！？;；\n])")


def sample_queries(index: dict, per_doc: int = 2, max_total: int = 40) -> list[dict]:
    """从索引中自动抽取代表性查询句（便于一键验证入库内容的可检索性）。

    优先选取长度适中（6~40 字）的句子作为查询，使验证更贴近真实检索场景。
    """
    by_doc: dict = {}
    for c in index.get("chunks", []):
        by_doc.setdefault(c["doc_id"], []).append(c)
    queries = []
    for doc_id, chs in by_doc.items():
        doc_name = chs[0].get("doc_name", "")
        cand = []
        for c in chs:
            for sent in _SENT_SPLIT.split(c["text"]):
                s = sent.strip()
                if 6 <= len(s) <= 40:
                    cand.append(s)
        if not cand:
            cand = [c["text"][:30].strip() for c in chs[:1] if c["text"].strip()]
        step = max(1, len(cand) // per_doc)
        for q in cand[::step][:per_doc]:
            queries.append({"query": q, "doc_id": doc_id, "doc_name": doc_name})
        if len(queries) >= max_total:
            break
    return queries[:max_total]


def _hit(top: list[dict], q: dict) -> bool:
    """命中判定：带源文档时要求 top_k 中出现该文档；否则要求最高分高于阈值。"""
    if q.get("doc_id"):
        return any(t["doc_id"] == q["doc_id"] for t in top)
    return bool(top) and top[0]["score"] > config.MIN_SCORE


def _build_result(top: list[dict], q: dict) -> dict:
    hit = _hit(top, q)
    best = top[0] if top else None
    return {
        "query": q["query"],
        "doc_name": q.get("doc_name", ""),
        "hit": hit,
        "best_doc": best["doc_name"] if best else None,
        "best_chunk_idx": (best["chunk_idx"] + 1) if best else None,
        "best_score": best["score"] if best else 0.0,
        "snippet": (best["text"] or "")[:160] if best else "",
    }


def verify_with_index(index: dict, queries: list[dict], top_k: int = 5) -> dict:
    """在给定索引上验证命中（纯 BM25 同步排序，用于离线效果评估）。"""
    results = []
    hits = 0
    for q in queries:
        top = [{"score": s, **c} for s, c in indexer.rank(q["query"], index, top_k)]
        r = _build_result(top, q)
        results.append(r)
        if r["hit"]:
            hits += 1
    total = len(queries)
    coverage = round(hits / total, 3) if total else 0.0
    return {"total": total, "hits": hits, "coverage": coverage, "results": results}


async def verify(user_id: str, queries: list[dict], top_k: int = 5) -> dict:
    """在真实索引上验证（含可选语义加权）。"""
    results = []
    hits = 0
    for q in queries:
        top = await indexer.top_chunks(user_id, q["query"], top_k)
        r = _build_result(top, q)
        results.append(r)
        if r["hit"]:
            hits += 1
    total = len(queries)
    coverage = round(hits / total, 3) if total else 0.0
    return {"total": total, "hits": hits, "coverage": coverage, "results": results}

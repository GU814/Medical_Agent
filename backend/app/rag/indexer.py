"""索引与检索：BM25（字符 bigram + 英文词元），零外部依赖、离线可用。

索引按用户隔离存储于 kb/index.json.enc，包含全部分块与统计量。
若配置了智谱 embedding，则在检索阶段做可选的语义加权（混合检索）。
"""
import math
import re
from collections import Counter
from typing import Optional

from .. import config, storage

_WORD_RE = re.compile(r"[a-zA-Z0-9]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

K1, B = 1.5, 0.75


def tokenize(text: str) -> list[str]:
    toks: list[str] = []
    toks += [w.lower() for w in _WORD_RE.findall(text or "")]
    # 中文：单字 + 2-gram（单字由 IDF 降权，可命中"发烧↔发热"这类换词场景）
    buf: list[str] = []
    for ch in text or "":
        if _CJK_RE.match(ch):
            buf.append(ch)
        else:
            if buf:
                toks += _ngram(buf)
                buf = []
    if buf:
        toks += _ngram(buf)
    return toks


def _ngram(chars: list[str]) -> list[str]:
    if len(chars) == 1:
        return chars
    out = list(chars)
    out += [chars[i] + chars[i + 1] for i in range(len(chars) - 1)]
    return out


# ---------------- 索引 ----------------

def build_index(user_id: str) -> dict:
    """汇总该用户全部文档分块，重建 BM25 统计。"""
    docs: dict = storage.load(user_id, "kb/docs.json.enc", {}) or {}
    chunks: list[dict] = []
    for doc_id, meta in docs.items():
        doc_chunks = storage.load(user_id, f"kb/chunks/{doc_id}.json.enc", []) or []
        for i, c in enumerate(doc_chunks):
            chunks.append({"doc_id": doc_id, "doc_name": meta.get("name", ""),
                           "chunk_idx": i, "text": c})
    df: Counter = Counter()
    total_len = 0
    for c in chunks:
        terms = set(tokenize(c["text"]))
        df.update(terms)
        total_len += len(c["text"])
    index = {
        "chunks": chunks,
        "df": dict(df),
        "N": len(chunks),
        "avgdl": (total_len / len(chunks)) if chunks else 0.0,
    }
    storage.save(user_id, "kb/index.json.enc", index)
    return index


def get_index(user_id: str) -> dict:
    idx = storage.load(user_id, "kb/index.json.enc")
    if idx is None:
        return build_index(user_id)
    return idx


# ---------------- 检索 ----------------

def _bm25_scores(query: str, index: dict) -> list[float]:
    q_terms = tokenize(query)
    if not q_terms or not index["chunks"]:
        return [0.0] * len(index["chunks"])
    N, avgdl, df = index["N"], max(index["avgdl"], 1.0), index["df"]
    scores = []
    for c in index["chunks"]:
        tf = Counter(tokenize(c["text"]))
        dl = len(c["text"])
        s = 0.0
        for t in q_terms:
            if t not in tf:
                continue
            idf = math.log(1 + (N - df.get(t, 0) + 0.5) / (df.get(t, 0) + 0.5))
            s += idf * tf[t] * (K1 + 1) / (tf[t] + K1 * (1 - B + B * dl / avgdl))
        scores.append(s)
    return scores


def explain_bm25(query: str, index: dict, top_k: int = 8) -> dict:
    """诊断检索：展示查询分词、各 token 的 df/idf，以及 top 分块的逐 token 得分明细。

    用于定位「查肺炎却返回头痛」这类误召回——可看出是单字 token（肺/炎）跨文档命中
    还是 bigram 命中、各贡献多少分，从而判断是否需要提高 min_score 或要求整词命中。
    """
    q_terms = tokenize(query)
    N, avgdl, df = index["N"], max(index["avgdl"], 1.0), index["df"]
    token_info = []
    for t in q_terms:
        d = df.get(t, 0)
        idf = math.log(1 + (N - d + 0.5) / (d + 0.5)) if N else 0.0
        token_info.append({"token": t, "df": d, "idf": round(idf, 4),
                            "is_bigram": len(t) >= 2 and _CJK_RE.match(t[0]) is not None})

    scores = _bm25_scores(query, index) if q_terms else [0.0] * len(index["chunks"])
    pairs = sorted(zip(scores, index.get("chunks", [])), key=lambda x: x[0], reverse=True)
    detail = []
    for sc, c in pairs[:top_k]:
        tf = Counter(tokenize(c["text"]))
        per = []
        for t in q_terms:
            if t not in tf:
                continue
            d = df.get(t, 0)
            idf = math.log(1 + (N - d + 0.5) / (d + 0.5))
            contrib = idf * tf[t] * (K1 + 1) / (tf[t] + K1 * (1 - B + B * len(c["text"]) / avgdl))
            per.append({"token": t, "tf": tf[t], "contrib": round(contrib, 4)})
        detail.append({"doc_name": c.get("doc_name", ""), "chunk_idx": c.get("chunk_idx", 0),
                       "score": round(sc, 4), "len": len(c["text"]),
                       "snippet": (c.get("text") or "")[:160],
                       "tokens": per})
    return {"query": query, "query_tokens": token_info,
            "N": N, "avgdl": round(avgdl, 1), "top": detail}


async def _semantic_scores(query: str, chunks: list[dict]) -> Optional[list[float]]:
    """可选语义检索：智谱 embedding 可用时启用，否则返回 None 走纯 BM25。"""
    try:
        from ..llm import get_provider
        prov = get_provider("zhipu")
        if not prov or not prov.has_embedding():
            return None
        q_vec = await prov.embed_query(query)
        if q_vec is None:
            return None
        # 逐块嵌入（知识库规模通常较小；如需性能可缓存向量）
        scores = []
        for c in chunks:
            d_vec = await prov.embed_query(c["text"][:800])
            if d_vec is None:
                return None
            scores.append(_cosine(q_vec, d_vec))
        return scores
    except Exception:
        return None


def _cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


def build_index_from_chunks(chunks: list[str], doc_id: str = None,
                             doc_name: str = "") -> dict:
    """从给定分块列表构建 BM25 索引（不落盘），用于效果评估/策略预览。"""
    norm: list[dict] = []
    df: Counter = Counter()
    total_len = 0
    for i, c in enumerate(chunks):
        norm.append({"doc_id": doc_id, "doc_name": doc_name, "chunk_idx": i, "text": c})
        df.update(set(tokenize(c)))
        total_len += len(c)
    return {"chunks": norm, "df": dict(df), "N": len(norm),
            "avgdl": (total_len / len(norm)) if norm else 0.0}


def rank(query: str, index: dict, top_k: int = None) -> list[tuple[float, dict]]:
    """对给定索引做纯 BM25 排序，返回 [(score, chunk)...]（降序，最多 top_k）。

    同步实现，供验证/效果评估在无事件循环的场景下使用（不含语义加权）。
    """
    top_k = top_k or config.TOP_K
    chunks = index.get("chunks", [])
    if not chunks or not (query or "").strip():
        return []
    bm = _bm25_scores(query, index)
    pairs = sorted(zip(bm, chunks), key=lambda x: x[0], reverse=True)
    return pairs[:top_k]


async def top_chunks(user_id: str, query: str, top_k: int = None) -> list[dict]:
    """检索 top_k 片段（不过滤阈值），返回带 score 的 chunk 列表。

    相比 search()，它保留全部 top_k 结果（含低于 MIN_SCORE 的），便于「验证命中状态」。
    """
    top_k = top_k or config.TOP_K
    index = get_index(user_id)
    chunks = index.get("chunks", [])
    if not chunks or not (query or "").strip():
        return []
    bm = _bm25_scores(query, index)
    sem = await _semantic_scores(query, chunks)

    if sem:
        mx, mn = max(bm) or 1.0, min(bm)
        rng = (mx - mn) or 1.0
        bm_n = [(x - mn) / rng for x in bm]
        smx, smn = max(sem), min(sem)
        srng = (smx - smn) or 1.0
        sem_n = [(x - smn) / srng for x in sem]
        combined = [0.5 * a + 0.5 * b for a, b in zip(bm_n, sem_n)]
    else:
        combined = bm

    pairs = sorted(zip(combined, chunks), key=lambda x: x[0], reverse=True)
    return [{**c, "score": round(float(score), 4)} for score, c in pairs[:top_k]]


async def search(user_id: str, query: str, top_k: int = None) -> list[dict]:
    """检索 top_k 相关片段（过滤低分）。返回 [{doc_id, doc_name, chunk_idx, text, score}]。"""
    top_k = top_k or config.TOP_K
    results = await top_chunks(user_id, query, top_k)
    return [r for r in results if r["score"] >= config.MIN_SCORE]

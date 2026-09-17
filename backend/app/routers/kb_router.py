"""知识库路由：上传文档（PDF/DOCX/MD/TXT）、自动分块索引、文档列表/删除、检索测试。"""
import time
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from .. import config, storage
from ..rag import chunker, indexer, loader, verify as verify_mod, graph as graph_mod
from .deps import get_current_user

router = APIRouter(prefix="/api/kb", tags=["knowledge-base"])

ALLOWED_EXT = (".pdf", ".docx", ".md", ".markdown", ".txt")
MAX_SIZE = 20 * 1024 * 1024  # 20MB
CHUNK_STRATEGIES = ("paragraph", "fixed", "sentence")


@router.get("/documents")
def list_documents(user: dict = Depends(get_current_user)):
    docs = storage.load(user["id"], "kb/docs.json.enc", {}) or {}
    items = []
    for doc_id, meta in docs.items():
        items.append({"id": doc_id, "name": meta.get("name"), "size": meta.get("size"),
                      "chunk_count": meta.get("chunk_count"),
                      "uploaded_at": meta.get("uploaded_at")})
    items.sort(key=lambda x: x.get("uploaded_at") or 0, reverse=True)
    return items


@router.post("/upload")
async def upload(file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    name = file.filename or "untitled"
    if not name.lower().endswith(ALLOWED_EXT):
        raise HTTPException(400, f"不支持的文件类型，请上传 {'/'.join(ALLOWED_EXT)}")

    content = await file.read()
    if len(content) > MAX_SIZE:
        raise HTTPException(400, "文件超过 20MB 限制")
    if not content:
        raise HTTPException(400, "文件为空")

    try:
        text = loader.extract_text(content, name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"文档解析失败：{str(e)[:150]}")

    if not text or not text.strip():
        raise HTTPException(400, "未能从文档中提取到文本内容")

    chunks = chunker.chunk_text(text)
    if not chunks:
        raise HTTPException(400, "文档分块结果为空")

    doc_id = uuid.uuid4().hex[:12]
    storage.save(user["id"], f"kb/chunks/{doc_id}.json.enc", chunks)
    storage.save(user["id"], f"kb/raw/{doc_id}.json.enc", text)  # 保留原文用于分块策略重算/预览
    docs = storage.load(user["id"], "kb/docs.json.enc", {}) or {}
    docs[doc_id] = {"name": name, "size": len(content), "chunk_count": len(chunks),
                    "uploaded_at": time.time(), "chars": len(text)}
    storage.save(user["id"], "kb/docs.json.enc", docs)
    indexer.build_index(user["id"])  # 重建该用户索引
    _rebuild_graph_cache(user["id"])  # 实时动态更新知识图谱
    return {"id": doc_id, "name": name, "chunk_count": len(chunks), "chars": len(text)}


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: str, user: dict = Depends(get_current_user)):
    docs = storage.load(user["id"], "kb/docs.json.enc", {}) or {}
    if doc_id not in docs:
        raise HTTPException(404, "文档不存在")
    del docs[doc_id]
    storage.save(user["id"], "kb/docs.json.enc", docs)
    storage.delete(user["id"], f"kb/chunks/{doc_id}.json.enc")
    storage.delete(user["id"], f"kb/raw/{doc_id}.json.enc")
    indexer.build_index(user["id"])
    _rebuild_graph_cache(user["id"])  # 删除文档后同步更新图谱
    return {"ok": True}


class SearchQuery(BaseModel):
    query: str
    top_k: int = 5


@router.post("/search")
async def search(body: SearchQuery, user: dict = Depends(get_current_user)):
    results = await indexer.search(user["id"], body.query, body.top_k)
    return [{"doc_name": r["doc_name"], "chunk_idx": r["chunk_idx"] + 1,
             "score": r["score"], "snippet": (r["text"] or "")[:300]} for r in results]


# ---------------- 分块与检索效果可视化 ----------------

@router.get("/config")
def kb_config(user: dict = Depends(get_current_user)):
    """返回当前分块/检索配置与可用策略，供前端「分块分析」页初始化。"""
    return {"chunk_size": config.CHUNK_SIZE, "chunk_overlap": config.CHUNK_OVERLAP,
            "top_k": config.TOP_K, "min_score": config.MIN_SCORE,
            "strategies": list(CHUNK_STRATEGIES)}


@router.get("/chunks")
def get_chunks(doc_id: str, user: dict = Depends(get_current_user)):
    """查看某文档的分块结果：分块内容、数量与质量统计。"""
    docs = storage.load(user["id"], "kb/docs.json.enc", {}) or {}
    if doc_id not in docs:
        raise HTTPException(404, "文档不存在")
    chunks = storage.load(user["id"], f"kb/chunks/{doc_id}.json.enc", [])
    if isinstance(chunks, dict):
        chunks = chunks.get("chunks", [])
    stats = chunker.chunk_stats(chunks)
    return {"id": doc_id, "name": docs[doc_id].get("name"),
            "chunk_count": len(chunks), "stats": stats,
            "chunks": [{"idx": i + 1, "len": len(c), "text": c} for i, c in enumerate(chunks)]}


class PreviewBody(BaseModel):
    doc_id: str = None
    text: str = None
    chunk_size: int = None
    chunk_overlap: int = None
    strategy: str = "paragraph"


@router.post("/preview-chunks")
def preview_chunks(body: PreviewBody, user: dict = Depends(get_current_user)):
    """分块策略预览：以指定参数对原文重新分块（不落盘），用于直观对比不同策略。"""
    text = body.text
    if text is None and body.doc_id:
        raw = storage.load(user["id"], f"kb/raw/{body.doc_id}.json.enc")
        if raw is None:
            raise HTTPException(400, "该文档缺少原文（可能为旧版本上传），请用文本直接预览")
        text = raw
    if not text or not text.strip():
        raise HTTPException(400, "请提供文本或有效的 doc_id")
    size = body.chunk_size or config.CHUNK_SIZE
    overlap = body.chunk_overlap if body.chunk_overlap is not None else config.CHUNK_OVERLAP
    strategy = body.strategy if body.strategy in CHUNK_STRATEGIES else "paragraph"
    chunks = chunker.chunk_text(text, size=size, overlap=overlap, strategy=strategy)
    return {"strategy": strategy, "chunk_size": size, "chunk_overlap": overlap,
            "chunk_count": len(chunks), "stats": chunker.chunk_stats(chunks),
            "chunks": [{"idx": i + 1, "len": len(c), "text": c} for i, c in enumerate(chunks)]}


class EvalBody(BaseModel):
    doc_id: str
    chunk_size: int = None
    chunk_overlap: int = None
    strategy: str = "paragraph"
    top_k: int = 5
    sample: int = 30


@router.post("/evaluate")
def evaluate(body: EvalBody, user: dict = Depends(get_current_user)):
    """检索效果对比：以候选分块参数重算分块并评估「自身内容可检索率」，与当前已存分块（基线）对照。"""
    docs = storage.load(user["id"], "kb/docs.json.enc", {}) or {}
    if body.doc_id not in docs:
        raise HTTPException(404, "文档不存在")
    raw = storage.load(user["id"], f"kb/raw/{body.doc_id}.json.enc")
    if raw is None:
        raise HTTPException(400, "该文档缺少原文（可能为旧版本上传），无法重算分块效果")
    size = body.chunk_size or config.CHUNK_SIZE
    overlap = body.chunk_overlap if body.chunk_overlap is not None else config.CHUNK_OVERLAP
    strategy = body.strategy if body.strategy in CHUNK_STRATEGIES else "paragraph"

    cand_chunks = chunker.chunk_text(raw, size=size, overlap=overlap, strategy=strategy)
    cand_index = indexer.build_index_from_chunks(cand_chunks, body.doc_id, docs[body.doc_id].get("name"))
    cand_queries = verify_mod.sample_queries(cand_index, per_doc=3, max_total=body.sample)
    cand = verify_mod.verify_with_index(cand_index, cand_queries, body.top_k)
    cand["chunk_count"] = len(cand_chunks)
    cand["stats"] = chunker.chunk_stats(cand_chunks)
    cand["strategy"] = strategy
    cand["chunk_size"] = size
    cand["chunk_overlap"] = overlap

    stored = storage.load(user["id"], f"kb/chunks/{body.doc_id}.json.enc", [])
    if isinstance(stored, dict):
        stored = stored.get("chunks", [])
    base_index = indexer.build_index_from_chunks(stored, body.doc_id, docs[body.doc_id].get("name"))
    base_queries = verify_mod.sample_queries(base_index, per_doc=3, max_total=body.sample)
    base = verify_mod.verify_with_index(base_index, base_queries, body.top_k)
    base["chunk_count"] = len(stored)
    base["stats"] = chunker.chunk_stats(stored)
    base["strategy"] = "stored"
    base["chunk_size"] = config.CHUNK_SIZE
    base["chunk_overlap"] = config.CHUNK_OVERLAP

    return {"baseline": base, "candidate": cand}


# ---------------- 检索验证 ----------------

@router.get("/diagnose")
def diagnose(query: str, top_k: int = 8, user: dict = Depends(get_current_user)):
    """检索命中率诊断：展示查询分词与 top 分块的逐 token BM25 得分贡献。

    用于定位「查肺炎却召回头痛内容」类误召回，看清单字 token（肺/炎）跨文档命中
    还是整词命中，从而决定是否提高 min_score 或要求整词命中。
    """
    if not (query or "").strip():
        raise HTTPException(400, "请提供 query")
    index = indexer.get_index(user["id"])
    return indexer.explain_bm25(query, index, top_k)


class VerifyBody(BaseModel):
    queries: list = None
    auto: bool = True
    sample: int = 30
    top_k: int = 5


@router.post("/verify")
async def verify_kb(body: VerifyBody, user: dict = Depends(get_current_user)):
    """检索验证：检测已加入知识库的内容是否能被检索到。

    - 传入 queries（字符串列表或 {query, doc_id}）做定向验证；
    - 不传且 auto=true 时，自动从索引抽样代表性句子做全库召回验证。
    返回整体覆盖率与逐条命中状态（hit / best_doc / best_score / snippet）作为反馈。
    """
    queries = []
    if body.queries:
        for q in body.queries:
            if isinstance(q, str):
                queries.append({"query": q})
            else:
                queries.append({"query": q.get("query"), "doc_id": q.get("doc_id"),
                                "doc_name": q.get("doc_name", "")})
    if body.auto and not queries:
        index = indexer.get_index(user["id"])
        queries = verify_mod.sample_queries(index, per_doc=2, max_total=body.sample)
    if not queries:
        raise HTTPException(400, "请提供待验证的查询语句，或开启自动抽样（auto=true）")
    return await verify_mod.verify(user["id"], queries, body.top_k)


# ---------------- 知识图谱 ----------------

def _rebuild_graph_cache(user_id: str) -> dict:
    """上传/删除文档后实时重建图谱缓存，保证图谱与知识库内容一致。"""
    g = graph_mod.build_graph(user_id)
    storage.save(user_id, "kb/graph.json.enc", g)
    return g


@router.get("/graph")
def get_graph(rebuild: bool = False, user: dict = Depends(get_current_user)):
    """返回知识图谱（病情节点 + 关联边）。rebuild=true 时基于当前知识库重建并缓存。"""
    if rebuild:
        return _rebuild_graph_cache(user["id"])
    g = storage.load(user["id"], "kb/graph.json.enc")
    if g is None:
        g = _rebuild_graph_cache(user["id"])
    return g


@router.get("/graph/tree")
def graph_tree(user: dict = Depends(get_current_user)):
    """返回图谱的分层树（分类→病情节点），供前端折叠树视图。"""
    g = storage.load(user["id"], "kb/graph.json.enc")
    if g is None:
        g = _rebuild_graph_cache(user["id"])
    return {"tree": g.get("tree", []), "categories": g.get("categories", []),
            "rel_label": g.get("rel_label", {})}


@router.post("/graph/rebuild")
def rebuild_graph(user: dict = Depends(get_current_user)):
    return _rebuild_graph_cache(user["id"])

"""本地向量化（embedding）模块：为混合检索提供语义向量。

- 单例懒加载：首次调用才加载模型，避免启动时阻塞；
- 容错降级：模型加载失败 / 编码异常时返回 None，检索层自动回退纯 BM25；
- 默认模型 BAAI/bge-small-zh-v1.5（中文语义，512 维，~100MB），
  可用 config 的 RAG_EMBED_MODEL / rag.embed_model 覆盖为其他模型或本地路径。
"""
import asyncio
from typing import Optional

from .. import config

_model = None
_model_error: Optional[str] = None


def get_embedder():
    """返回全局单例 SentenceTransformer；加载失败返回 None。"""
    global _model, _model_error
    if _model is not None:
        return _model
    if _model_error:
        return None
    try:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(config.RAG_EMBED_MODEL)
        return _model
    except Exception as e:  # 依赖缺失 / 首次下载失败 / 显存不足等
        _model_error = f"{type(e).__name__}: {str(e)[:200]}"
        return None


def embed(texts: list[str]) -> Optional[list[list[float]]]:
    """同步编码一批文本为归一化向量；失败返回 None。"""
    m = get_embedder()
    if m is None:
        return None
    try:
        return m.encode(texts, normalize_embeddings=True).tolist()
    except Exception:
        return None


async def embed_async(texts: list[str]) -> Optional[list[list[float]]]:
    """异步包装，避免阻塞事件循环（CPU 密集）。"""
    return await asyncio.to_thread(embed, texts)


def embedding_status() -> dict:
    """诊断信息：模型是否可用、错误原因。"""
    if _model is not None:
        return {"available": True, "model": config.RAG_EMBED_MODEL, "error": None}
    return {"available": False, "model": config.RAG_EMBED_MODEL, "error": _model_error}

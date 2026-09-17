"""对话标题自动摘要：用大模型生成 ≤20 字中文标题，失败则本地兜底。"""
from typing import List, Optional

from .llm import LLMProvider
from . import prompts

MAX_TITLE = 20  # 标题长度上限（汉字计），可少不可多

# 需要清理的结尾/包裹字符：标点、空白、引号
_TRIM = "。.，,！!？?、：:；;…—- \t\n\r\"'「」“”‘’()（）《》<>「"


def _clean(text: str) -> str:
    """清洗并强制截断到 MAX_TITLE 字以内。"""
    text = (text or "").strip()
    text = text.strip("\"'「」“”‘’ \t\n\r")  # 去掉包裹引号
    text = text.rstrip(_TRIM)                  # 去掉结尾多余标点/空白
    return text[:MAX_TITLE]


def local_title(messages: List[dict]) -> str:
    """本地兜底：取首个用户消息前若干字作为标题。"""
    for m in messages:
        if m.get("role") == "user" and m.get("content"):
            return _clean(m["content"][:18]) or "健康问诊"
    return "健康问诊"


async def summarize_title(messages: List[dict], provider: Optional[LLMProvider]) -> str:
    """生成对话标题（≤20 字）。

    - provider 可用时调用大模型做整体概括；
    - 否则（未配 Key / 接口不可用）回退到本地首条用户消息摘要。
    任何异常都安全降级到本地兜底，不会抛出。
    """
    if not messages:
        return "健康问诊"
    if provider is None or not provider.available():
        return local_title(messages)

    dialog = "\n".join(
        f"{'用户' if m['role'] == 'user' else '医生'}：{m['content']}"
        for m in messages[:8] if m.get("content")
    )
    if not dialog.strip():
        return local_title(messages)

    try:
        raw = await provider.chat(
            messages=[{"role": "user",
                       "content": prompts.TITLE_PROMPT.replace("{dialog}", dialog)}],
            temperature=0.1,
        )
        return _clean(raw) or local_title(messages)
    except Exception:
        return local_title(messages)

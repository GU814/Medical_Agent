"""模型注册表：统一获取 provider、列出可用模型，支持前端切换。"""
from typing import Optional

from .. import config
from .base import LLMProvider, OpenAICompatibleProvider, encode_image  # noqa: F401

_providers: dict[str, LLMProvider] = {}


def _init() -> None:
    if _providers:
        return
    for name, settings in (config.PROVIDERS or {}).items():
        _providers[name] = OpenAICompatibleProvider(name, settings)


def get_provider(name: str) -> Optional[LLMProvider]:
    _init()
    return _providers.get(name)


def list_providers() -> list[dict]:
    _init()
    out = []
    for name, p in _providers.items():
        out.append({
            "id": name, "label": p.label,
            "available": p.available(),
            "chat_model": p.chat_model(),
            "vision": bool(p.vision_model()) and p.available(),
            "embedding": p.has_embedding() and p.available(),
        })
    return out


def default_provider_id() -> str:
    return config.DEFAULT_PROVIDER if config.DEFAULT_PROVIDER in _providers else \
        (next(iter(_providers)) if _providers else "")


def get_any_provider(preferred: Optional[str] = None) -> Optional[LLMProvider]:
    """返回一个当前可用（已配置 Key）的 provider；优先 preferred，否则任取一个。"""
    _init()
    if preferred:
        p = _providers.get(preferred)
        if p and p.available():
            return p
    for p in _providers.values():
        if p.available():
            return p
    return None

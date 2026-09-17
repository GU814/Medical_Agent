"""LLM 适配层。

- LLMProvider：统一抽象接口，新增国外模型只需继承并注册。
- OpenAICompatibleProvider：覆盖智谱 GLM / DeepSeek / OpenAI 等
  OpenAI 兼容协议端点（chat/completions + embeddgins + 流式 SSE）。
"""
import base64
import json
from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional

import httpx

from .. import config


class LLMProvider(ABC):
    name: str = "base"
    label: str = "未命名"

    @abstractmethod
    def available(self) -> bool:
        """是否已配置可用（有 api_key 等）。"""

    @abstractmethod
    def chat_model(self) -> str: ...

    @abstractmethod
    async def stream_chat(self, messages: list[dict], model: Optional[str] = None,
                           temperature: float = 0.3) -> AsyncIterator[str]:
        """流式输出，逐段 yield 文本 delta。"""

    @abstractmethod
    async def chat(self, messages: list[dict], model: Optional[str] = None,
                   temperature: float = 0.3) -> str:
        """一次性返回完整文本。"""

    # ---- 可选能力 ----
    def vision_model(self) -> Optional[str]:
        return None

    async def vision(self, image_b64: str, prompt: str, model: Optional[str] = None) -> str:
        raise NotImplementedError(f"{self.label} 暂不支持视觉识别")

    def embedding_model(self) -> Optional[str]:
        return None

    def has_embedding(self) -> bool:
        return bool(self.embedding_model())

    async def embed_query(self, text: str) -> Optional[list[float]]:
        return None


class OpenAICompatibleProvider(LLMProvider):
    """OpenAI 兼容协议实现（智谱 / DeepSeek / OpenAI 等通用）。"""

    def __init__(self, name: str, settings: dict):
        self.name = name
        self.label = {"zhipu": "智谱 GLM", "deepseek": "DeepSeek",
                      "openai": "OpenAI（预留）"}.get(name, name)
        self.settings = settings or {}
        self.api_key = (self.settings.get("api_key") or "").strip()
        self.base_url = (self.settings.get("base_url") or "").rstrip("/")

    def available(self) -> bool:
        return bool(self.api_key and self.base_url)

    def chat_model(self) -> str:
        return self.settings.get("chat_model") or ""

    def vision_model(self) -> Optional[str]:
        return (self.settings.get("vision_model") or "").strip() or None

    def embedding_model(self) -> Optional[str]:
        return (self.settings.get("embedding_model") or "").strip() or None

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"}

    # ---------------- chat ----------------

    def _payload(self, messages, model, temperature, stream) -> dict:
        return {"model": model or self.chat_model(), "messages": messages,
                "temperature": temperature, "stream": stream}

    def _fail_if_not_json(self, resp: "httpx.Response") -> None:
        """上游返回 HTML / 非 JSON（通常是地址或模型名配错）时，给出清晰报错。"""
        ctype = (resp.headers.get("content-type") or "").lower()
        if "application/json" in ctype:
            return
        try:
            head = (resp.text or "")[:120].lstrip()
        except Exception:
            head = ""
        if head.startswith("<!doctype") or head.startswith("<html") or head.startswith("<"):
            raise RuntimeError(
                f"{self.label} 返回了网页而非 JSON（HTTP {resp.status_code}），"
                f"多半是 config.yaml 里的 base_url 或模型名填错，或中转站地址不可用。")

    def _extract_error(self, resp: "httpx.Response", model: str = "") -> str:
        """解析上游 JSON 错误体，把中转站账号组类问题翻译成可操作提示。"""
        raw = ""
        try:
            raw = (resp.json().get("error", {}) or {}).get("message", "") or resp.text
        except Exception:
            raw = resp.text or ""
        raw = str(raw)[:300]
        if "not supported by any configured account" in raw:
            return (f"{self.label} 模型「{model}」在当前中转站账号组未开通"
                    f"（上游返回：{raw}）。这是中转站套餐/账号权限问题，与输入内容无关："
                    f"请在该中转站更换为已开通此模型的账号/Key，或改用已开通视觉与对话能力的其它 provider。")
        if "accounts exhausted" in raw or "All available" in raw:
            return (f"{self.label} 模型「{model}」的中转站账号已耗尽/限流"
                    f"（上游返回：{raw}）。请稍后重试，或换用其它可用模型。")
        if "Unsupported image format" in raw or "image" in raw.lower():
            return (f"{self.label} 拒绝该图片格式（上游返回：{raw}）。"
                    f"请上传清晰的 JPG/PNG 药物照片。")
        return f"{self.label} 返回 {resp.status_code}：{raw}"

    async def chat(self, messages: list[dict], model: Optional[str] = None,
                   temperature: float = 0.3) -> str:
        if not self.available():
            raise RuntimeError(f"{self.label} 未配置 API Key，请在 config.yaml 中填写")
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(f"{self.base_url}/chat/completions",
                                  headers=self._headers(),
                                  json=self._payload(messages, model, temperature, False))
            self._fail_if_not_json(r)
            r.raise_for_status()
            data = r.json()
            return data["choices"][0]["message"]["content"] or ""

    async def stream_chat(self, messages: list[dict], model: Optional[str] = None,
                          temperature: float = 0.3) -> AsyncIterator[str]:
        if not self.available():
            raise RuntimeError(f"{self.label} 未配置 API Key，请在 config.yaml 中填写")
        # 设置超时，避免上游挂起时前端永远“思考”
        timeout = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", f"{self.base_url}/chat/completions",
                                     headers=self._headers(),
                                     json=self._payload(messages, model, temperature, True)) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", "ignore")
                    raise RuntimeError(f"{self.label} 返回 {resp.status_code}: {body[:200]}")
                async for raw in resp.aiter_lines():
                    line = raw.strip()
                    if not line or line.startswith(":"):
                        continue  # 空行 / SSE 注释行
                    if not line.startswith("data:"):
                        # 非 SSE 内容（HTML 错误页 / JSON 报错）→ 明确失败
                        raise RuntimeError(
                            f"{self.label} 返回了非 SSE 内容（疑似中转站地址/模型名错误）：{line[:200]}")
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                        delta = obj["choices"][0].get("delta", {})
                        content = delta.get("content")
                        if content:
                            yield content
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

    # ---------------- vision ----------------

    async def vision(self, image_b64: str, prompt: str, model: Optional[str] = None) -> str:
        m = model or self.vision_model()
        if not m:
            raise RuntimeError(f"{self.label} 未配置视觉模型，无法识药")
        messages = [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                {"type": "text", "text": prompt},
            ],
        }]
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(f"{self.base_url}/chat/completions", headers=self._headers(),
                                  json={"model": m, "messages": messages, "temperature": 0.1})
            self._fail_if_not_json(r)
            if r.status_code != 200:
                # 透传上游真实错误（含账号组未开通/限流等可操作信息），避免被吞成泛化 500
                raise RuntimeError(self._extract_error(r, m))
            try:
                data = r.json()
                return data["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, TypeError) as e:
                raise RuntimeError(
                    f"{self.label} 返回了非预期结构（{type(e).__name__}）：{r.text[:200]}")

    # ---------------- embedding ----------------

    async def embed_query(self, text: str) -> Optional[list[float]]:
        m = self.embedding_model()
        if not m or not self.available():
            return None
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                r = await client.post(f"{self.base_url}/embeddings", headers=self._headers(),
                                      json={"model": m, "input": text[:1800]})
                r.raise_for_status()
                return r.json()["data"][0]["embedding"]
        except Exception:
            return None


def encode_image(path_or_bytes) -> str:
    if isinstance(path_or_bytes, (bytes, bytearray)):
        return base64.b64encode(bytes(path_or_bytes)).decode()
    with open(path_or_bytes, "rb") as f:
        return base64.b64encode(f.read()).decode()

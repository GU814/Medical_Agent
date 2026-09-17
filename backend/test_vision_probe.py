"""探测中转站对 Claude vision 的支持方式。"""
import asyncio
import base64
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
with open(ROOT / "config.yaml", "r", encoding="utf-8") as f:
    cfg = yaml.safe_load(f)

z = cfg["llm"]["providers"]["zhipu"]
key = z["api_key"].strip()
base_url = z["base_url"].rstrip("/")
model = z["vision_model"]
img_path = ROOT / "test_medicine.png"
img_b64 = base64.b64encode(img_path.read_bytes()).decode()


async def test_openai_compatible():
    print("=== 1) OpenAI 兼容格式 POST /v1/chat/completions + image_url ===")
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                {"type": "text", "text": "这是什么药物？"},
            ]
        }],
        "temperature": 0.1,
    }
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{base_url}/chat/completions",
                         headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                         json=payload)
    print(f"status={r.status_code}")
    print(r.text[:300])
    print()


async def test_anthropic_native():
    print("=== 2) Anthropic 原生格式 POST /v1/messages ===")
    payload = {
        "model": model,
        "max_tokens": 1024,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}},
                {"type": "text", "text": "这是什么药物？"},
            ]
        }],
    }
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{base_url}/messages",
                         headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                  "Content-Type": "application/json"},
                         json=payload)
    print(f"status={r.status_code}")
    print(r.text[:300])
    print()


asyncio.run(test_openai_compatible())
asyncio.run(test_anthropic_native())

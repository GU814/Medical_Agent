"""用户数据空间：每位用户一个独立目录，所有业务数据加密落盘。

data/spaces/{user_id}/
    conversations/{conv_id}.json.enc   # 对话
    kb/docs.json.enc                   # 知识库文档元数据
    kb/chunks/{doc_id}.json.enc        # 分块
    kb/index.json.enc                  # 检索索引
    profile.json.enc                   # 健康档案
    reminders.json.enc                 # 提醒
"""
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from . import config
from .crypto import encrypt_json, decrypt_json, InvalidToken

_lock = threading.Lock()


def user_dir(user_id: str) -> Path:
    d = config.SPACES_DIR / user_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "conversations").mkdir(exist_ok=True)
    (d / "kb" / "chunks").mkdir(parents=True, exist_ok=True)
    return d


def _enc_path(user_id: str, rel: str) -> Path:
    return user_dir(user_id) / rel


def save(user_id: str, rel: str, obj: Any) -> None:
    with _lock:
        p = _enc_path(user_id, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(encrypt_json(user_id, obj))


def load(user_id: str, rel: str, default: Any = None) -> Any:
    p = _enc_path(user_id, rel)
    if not p.exists():
        return default
    try:
        return decrypt_json(user_id, p.read_bytes())
    except (InvalidToken, json.JSONDecodeError):
        return default


def delete(user_id: str, rel: str) -> None:
    p = _enc_path(user_id, rel)
    if p.exists():
        p.unlink()


def list_conversations(user_id: str) -> list[dict]:
    items = []
    for p in sorted((_enc_path(user_id, "conversations")).glob("*.json.enc"),
                    key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            items.append(decrypt_json(user_id, p.read_bytes()))
        except Exception:
            continue
    return items


def new_conversation(user_id: str, title: str = "新的问诊") -> dict:
    conv_id = uuid.uuid4().hex[:12]
    conv = {
        "id": conv_id, "title": title or "新的问诊",
        "created_at": time.time(), "updated_at": time.time(),
        "messages": [], "report": None, "provider": None, "model": None,
    }
    save(user_id, f"conversations/{conv_id}.json.enc", conv)
    return conv


def get_conversation(user_id: str, conv_id: str) -> dict | None:
    return load(user_id, f"conversations/{conv_id}.json.enc")


def save_conversation(user_id: str, conv: dict) -> None:
    conv["updated_at"] = time.time()
    save(user_id, f"conversations/{conv['id']}.json.enc", conv)

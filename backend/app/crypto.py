"""加密模块：基于用户 ID 派生密钥的 Fernet 加密存储。

主密钥（config 自动生成）+ HKDF(用户ID) 派生每用户独立密钥，
不同用户密文互不可解，实现数据空间硬隔离。
"""
import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from . import config


def _derive_fernet(user_id: str) -> Fernet:
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"medical-agent-user-salt",
                info=f"user:{user_id}".encode())
    key = base64.urlsafe_b64encode(hkdf.derive(config.FERNET_KEY))
    return Fernet(key)


def encrypt_json(user_id: str, obj: Any) -> bytes:
    data = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
    return _derive_fernet(user_id).encrypt(data)


def decrypt_json(user_id: str, token: bytes) -> Any:
    data = _derive_fernet(user_id).decrypt(token)
    return json.loads(data.decode("utf-8"))


def hash_password(password: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    """PBKDF2-SHA256, 120k 轮。返回 (salt, dk)。"""
    import os
    if salt is None:
        salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return salt, dk


def verify_password(password: str, salt: bytes, dk: bytes) -> bool:
    import hmac
    _, calc = hash_password(password, salt)
    return hmac.compare_digest(calc, dk)


__all__ = ["encrypt_json", "decrypt_json", "hash_password", "verify_password", "InvalidToken"]

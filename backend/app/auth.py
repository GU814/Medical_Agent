"""用户账户与 JWT 鉴权。

- SQLite 存账户（用户名唯一），密码 PBKDF2 哈希。
- JWT：HMAC-SHA256 自签（header.payload.signature），无第三方依赖。
- 依赖 FastAPI 时通过 get_current_user 注入请求用户。
"""
import base64
import hashlib
import hmac
import json
import re
import sqlite3
import time
import uuid
from typing import Optional

from . import config
from .crypto import hash_password, verify_password

TOKEN_TTL = 7 * 24 * 3600  # 7 天
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_\u4e00-\u9fa5]{2,32}$")


def _db() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.USERS_DB)
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        salt BLOB NOT NULL,
        pwd_dk BLOB NOT NULL,
        created_at REAL NOT NULL)""")
    return conn


def register(username: str, password: str) -> dict:
    if not _USERNAME_RE.match(username or ""):
        raise ValueError("用户名需为 2-32 位字母/数字/下划线/中文")
    if not password or len(password) < 6:
        raise ValueError("密码至少 6 位")
    salt, dk = hash_password(password)
    uid = uuid.uuid4().hex
    conn = _db()
    try:
        conn.execute("INSERT INTO users(id, username, salt, pwd_dk, created_at) VALUES(?,?,?,?,?)",
                     (uid, username, salt, dk, time.time()))
        conn.commit()
    except sqlite3.IntegrityError:
        raise ValueError("用户名已存在")
    finally:
        conn.close()
    return {"id": uid, "username": username}


def login(username: str, password: str) -> dict:
    conn = _db()
    row = conn.execute("SELECT id, salt, pwd_dk FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    if not row or not verify_password(password, row[1], row[2]):
        raise ValueError("用户名或密码错误")
    return {"id": row[0], "username": username}


# ---------------- JWT ----------------

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_token(user: dict) -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64(json.dumps({
        "uid": user["id"], "username": user["username"],
        "exp": int(time.time()) + TOKEN_TTL,
    }, ensure_ascii=False).encode())
    signing = f"{header}.{payload}".encode()
    sig = _b64(hmac.new(config.JWT_SECRET, signing, hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


def parse_token(token: str) -> Optional[dict]:
    try:
        header, payload, sig = token.split(".")
        signing = f"{header}.{payload}".encode()
        expect = _b64(hmac.new(config.JWT_SECRET, signing, hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expect):
            return None
        data = json.loads(_unb64(payload))
        if data.get("exp", 0) < time.time():
            return None
        return data
    except Exception:
        return None

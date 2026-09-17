"""共享依赖：从请求头解析当前用户。"""
from fastapi import Header, HTTPException

from .. import auth, storage


def get_current_user(authorization: str = Header(default="")) -> dict:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "未登录")
    payload = auth.parse_token(authorization[7:].strip())
    if not payload:
        raise HTTPException(401, "登录已过期，请重新登录")
    # 确保数据空间存在
    storage.user_dir(payload["uid"])
    return {"id": payload["uid"], "username": payload.get("username", "")}

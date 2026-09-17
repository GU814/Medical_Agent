"""认证路由：注册 / 登录 / 当前用户 / 可用模型列表。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import auth
from ..llm import list_providers, default_provider_id
from .deps import get_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


class Credentials(BaseModel):
    username: str
    password: str


@router.post("/register")
def register(body: Credentials):
    try:
        user = auth.register(body.username.strip(), body.password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"user": user, "token": auth.issue_token(user)}


@router.post("/login")
def login(body: Credentials):
    try:
        user = auth.login(body.username.strip(), body.password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"user": user, "token": auth.issue_token(user)}


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return {"user": user, "providers": list_providers(),
            "default_provider": default_provider_id()}

"""健康档案 + 行为建议 + 提醒闹钟路由。"""
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import prompts, storage
from ..llm import get_provider
from .deps import get_current_user

router = APIRouter(prefix="/api", tags=["profile"])

DEFAULT_PROFILE = {
    "nickname": "", "gender": "", "birthday": "", "height_cm": None, "weight_kg": None,
    "blood_type": "", "allergies": "", "chronic": "",          # 慢性病史
    "current_medications": "",                                  # 在服药物
    "records": [],                                              # 近期健康记录
}


@router.get("/profile")
def get_profile(user: dict = Depends(get_current_user)):
    p = storage.load(user["id"], "profile.json.enc")
    if not p:
        p = {**DEFAULT_PROFILE, "nickname": user["username"]}
        storage.save(user["id"], "profile.json.enc", p)
    return p


@router.put("/profile")
def update_profile(body: dict, user: dict = Depends(get_current_user)):
    p = storage.load(user["id"], "profile.json.enc", {**DEFAULT_PROFILE}) or dict(DEFAULT_PROFILE)
    for k in DEFAULT_PROFILE:
        if k in body:
            p[k] = body[k]
    p["nickname"] = p.get("nickname") or user["username"]
    storage.save(user["id"], "profile.json.enc", p)
    return p


class HealthRecord(BaseModel):
    type: str            # 记录类型：症状/用药/体检/其他
    content: str
    date: Optional[str] = None


@router.post("/profile/records")
def add_record(body: HealthRecord, user: dict = Depends(get_current_user)):
    p = storage.load(user["id"], "profile.json.enc") or dict(DEFAULT_PROFILE)
    p.setdefault("records", []).insert(0, {
        "id": uuid.uuid4().hex[:8], "type": body.type, "content": body.content,
        "date": body.date or time.strftime("%Y-%m-%d"), "created_at": time.time(),
    })
    p["records"] = p["records"][:200]
    storage.save(user["id"], "profile.json.enc", p)
    return p


@router.delete("/profile/records/{record_id}")
def delete_record(record_id: str, user: dict = Depends(get_current_user)):
    p = storage.load(user["id"], "profile.json.enc") or dict(DEFAULT_PROFILE)
    p["records"] = [r for r in p.get("records", []) if r.get("id") != record_id]
    storage.save(user["id"], "profile.json.enc", p)
    return p


@router.get("/profile/suggestions")
async def suggestions(user: dict = Depends(get_current_user)):
    p = storage.load(user["id"], "profile.json.enc") or dict(DEFAULT_PROFILE)
    brief = {k: p.get(k) for k in ("gender", "age", "chronic", "allergies",
                                   "current_medications")}
    brief["recent_records"] = p.get("records", [])[:10]
    # 无模型时给安全默认建议
    provider = None
    for pid in ("zhipu", "deepseek", "openai"):
        prov = get_provider(pid)
        if prov and prov.available():
            provider = prov
            break
    if provider is None:
        return {"suggestions": ["规律作息，避免熬夜", "每天饮水 1500-2000ml",
                                "适量运动，每周锻炼 3 次", "按时服药，遵医嘱复诊"],
                "source": "default"}
    try:
        raw = await provider.chat([{"role": "user", "content": prompts.SUGGESTION_PROMPT.replace(
            "{profile}", str(brief))}], temperature=0.4)
        import json as _json
        start, end = raw.find("["), raw.rfind("]")
        arr = _json.loads(raw[start:end + 1]) if start >= 0 else []
        arr = [str(a)[:30] for a in arr][:6] or ["保持规律作息与均衡饮食"]
        return {"suggestions": arr, "source": "model"}
    except Exception:
        return {"suggestions": ["规律作息，避免熬夜", "每天饮水 1500-2000ml",
                                "适量运动，每周锻炼 3 次", "按时服药，遵医嘱复诊"],
                "source": "default"}


# ---------------- 提醒 ----------------

class Reminder(BaseModel):
    title: str
    time: str                    # "HH:MM" 每日提醒，或 "YYYY-MM-DD HH:MM" 一次性
    repeat: str = "daily"        # daily / once
    note: str = ""
    enabled: bool = True


@router.get("/reminders")
def list_reminders(user: dict = Depends(get_current_user)):
    items = storage.load(user["id"], "reminders.json.enc", []) or []
    items.sort(key=lambda x: (not x.get("enabled", True), x.get("time", "")))
    return items


@router.post("/reminders")
def create_reminder(body: Reminder, user: dict = Depends(get_current_user)):
    if not body.title.strip():
        raise HTTPException(400, "提醒标题不能为空")
    items = storage.load(user["id"], "reminders.json.enc", []) or []
    item = {"id": uuid.uuid4().hex[:8], "title": body.title.strip(), "time": body.time,
            "repeat": body.repeat, "note": body.note, "enabled": body.enabled,
            "created_at": time.time()}
    items.append(item)
    storage.save(user["id"], "reminders.json.enc", items)
    return item


@router.patch("/reminders/{rid}")
def update_reminder(rid: str, body: dict, user: dict = Depends(get_current_user)):
    items = storage.load(user["id"], "reminders.json.enc", []) or []
    for it in items:
        if it["id"] == rid:
            for k in ("title", "time", "repeat", "note", "enabled"):
                if k in body:
                    it[k] = body[k]
            storage.save(user["id"], "reminders.json.enc", items)
            return it
    raise HTTPException(404, "提醒不存在")


@router.delete("/reminders/{rid}")
def delete_reminder(rid: str, user: dict = Depends(get_current_user)):
    items = storage.load(user["id"], "reminders.json.enc", []) or []
    left = [it for it in items if it["id"] != rid]
    if len(left) == len(items):
        raise HTTPException(404, "提醒不存在")
    storage.save(user["id"], "reminders.json.enc", left)
    return {"ok": True}

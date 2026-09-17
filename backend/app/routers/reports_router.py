"""医学报告库路由：用户专属历史报告的列表、查看、删除、编辑。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import reports
from .deps import get_current_user

router = APIRouter(prefix="/api/reports", tags=["reports"])


class ReportPatch(BaseModel):
    title: str | None = None
    category: str | None = None


@router.get("")
def list_reports(category: str | None = None, q: str | None = None,
                user: dict = Depends(get_current_user)):
    """列出报告，按时间倒序；支持 ?category= 与 ?q= 关键词过滤。"""
    return reports.list_reports(user["id"], category=category, keyword=q)


@router.get("/categories")
def list_categories(user: dict = Depends(get_current_user)):
    """返回各分类的报告计数，供前端分组侧栏。"""
    return reports.categories(user["id"])


@router.get("/{rid}")
def get_report(rid: str, user: dict = Depends(get_current_user)):
    rep = reports.get_report(user["id"], rid)
    if rep is None:
        raise HTTPException(404, "报告不存在")
    return rep


@router.patch("/{rid}")
def patch_report(rid: str, body: ReportPatch,
                 user: dict = Depends(get_current_user)):
    meta = reports.update_report(user["id"], rid,
                                 title=body.title, category=body.category)
    if meta is None:
        raise HTTPException(404, "报告不存在")
    return meta


@router.delete("/{rid}")
def delete_report(rid: str, user: dict = Depends(get_current_user)):
    if not reports.delete_report(user["id"], rid):
        raise HTTPException(404, "报告不存在")
    return {"ok": True}

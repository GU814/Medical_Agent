"""医学报告库：用户专属历史报告的存储、归类、检索与查看。

数据布局（用户空间内，加密落盘）：
    reports/{rid}.json.enc      # 单份完整报告
    reports/index.json.enc      # 报告索引：[{rid, conv_id, title, category,
                                #   chief_complaint, created_at, summary}]
报告在对话生成报告时自动入库，按主诉归入系统分类，支持按时间/分类过滤。
"""
from __future__ import annotations

import time
import uuid

from . import storage
from .med_cats import categorize


def _idx(user_id: str) -> list[dict]:
    return storage.load(user_id, "reports/index.json.enc", []) or []


def _save_idx(user_id: str, idx: list[dict]) -> None:
    storage.save(user_id, "reports/index.json.enc", idx)


def save_report(user_id: str, conv: dict, report: dict) -> dict:
    """将生成的报告入库。返回带 rid 的报告元数据。

    - 自动按 chief_complaint 归类；
    - 自动抽取一句话摘要（取主诉或报告第一段）；
    - 同一会话重复生成报告时更新同一 rid（便于查看历史），而不是无限堆叠。
    """
    rid = None
    idx = _idx(user_id)
    conv_id = conv.get("id")
    for item in idx:
        if item.get("conv_id") == conv_id:
            rid = item["rid"]
            break
    if rid is None:
        rid = uuid.uuid4().hex[:12]

    chief = (report.get("chief_complaint") or "").strip() or conv.get("title") or "未命名报告"
    category = categorize(chief + " " + conv.get("title", ""))
    summary = _derive_summary(report, chief)

    full = dict(report)
    full.update({
        "rid": rid, "conv_id": conv_id, "title": conv.get("title") or chief,
        "category": category, "chief_complaint": chief,
        "created_at": time.time(),
    })
    storage.save(user_id, f"reports/{rid}.json.enc", full)

    meta = {
        "rid": rid, "conv_id": conv_id, "title": full["title"],
        "category": category, "chief_complaint": chief,
        "summary": summary, "created_at": full["created_at"],
    }
    # 更新或插入索引
    replaced = False
    for i, item in enumerate(idx):
        if item["rid"] == rid:
            idx[i] = meta
            replaced = True
            break
    if not replaced:
        idx.append(meta)
    _save_idx(user_id, idx)
    return meta


def _derive_summary(report: dict, chief: str) -> str:
    """从报告结构里抽取一句摘要，供列表预览。"""
    for key in ("summary", "impression", "diagnosis", "现病史"):
        v = report.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()[:60]
    # 兜底：主诉
    return chief[:60]


def list_reports(user_id: str, category: str | None = None,
                 keyword: str | None = None) -> list[dict]:
    """列出报告元数据，按创建时间倒序；可按分类/关键词过滤。"""
    idx = _idx(user_id)
    out = []
    for m in idx:
        if category and m.get("category") != category:
            continue
        if keyword:
            kw = keyword.lower()
            hay = (m.get("title", "") + m.get("chief_complaint", "") +
                   m.get("summary", "")).lower()
            if kw not in hay:
                continue
        out.append(m)
    out.sort(key=lambda x: x.get("created_at") or 0, reverse=True)
    return out


def get_report(user_id: str, rid: str) -> dict | None:
    return storage.load(user_id, f"reports/{rid}.json.enc")


def delete_report(user_id: str, rid: str) -> bool:
    idx = _idx(user_id)
    new_idx = [m for m in idx if m["rid"] != rid]
    if len(new_idx) == len(idx):
        return False
    _save_idx(user_id, new_idx)
    storage.delete(user_id, f"reports/{rid}.json.enc")
    return True


def update_report(user_id: str, rid: str, title: str | None = None,
                  category: str | None = None) -> dict | None:
    """编辑报告标题/分类（用户手动归类）。"""
    full = get_report(user_id, rid)
    if full is None:
        return None
    if title is not None:
        full["title"] = title
    if category is not None:
        full["category"] = category
    storage.save(user_id, f"reports/{rid}.json.enc", full)
    idx = _idx(user_id)
    for m in idx:
        if m["rid"] == rid:
            if title is not None:
                m["title"] = title
            if category is not None:
                m["category"] = category
            m["chief_complaint"] = full.get("chief_complaint", m.get("chief_complaint"))
            break
    _save_idx(user_id, idx)
    return next((m for m in idx if m["rid"] == rid), None)


def categories(user_id: str) -> list[dict]:
    """返回各分类的报告计数，供前端分组展示。"""
    from collections import Counter
    idx = _idx(user_id)
    cnt = Counter(m.get("category", "其他") for m in idx)
    return [{"category": c, "count": cnt.get(c, 0)} for c in _all_categories_with_data(idx)]


def _all_categories_with_data(idx: list[dict]) -> list[str]:
    from .med_cats import CATEGORIES
    present = {m.get("category", "其他") for m in idx}
    # 已知分类顺序在前，其余按字母
    ordered = [c for c in CATEGORIES if c in present]
    extras = sorted(present - set(CATEGORIES))
    return ordered + extras

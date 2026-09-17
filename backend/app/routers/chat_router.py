"""对话路由：会话管理 + SSE 流式问答（RAG 引用 + 防幻觉）+ 报告生成。"""
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import prompts, reports, storage, summary
from ..llm import get_provider, get_any_provider
from ..rag import indexer
from .deps import get_current_user

router = APIRouter(prefix="/api", tags=["chat"])

MAX_HISTORY = 24  # 送入模型的最大历史轮数


class ChatRequest(BaseModel):
    conversation_id: str
    message: str
    provider: str | None = None


@router.get("/conversations")
def list_conversations(user: dict = Depends(get_current_user)):
    convs = storage.list_conversations(user["id"])
    return [{"id": c["id"], "title": c["title"], "updated_at": c["updated_at"],
             "message_count": len(c.get("messages", [])),
             "has_report": bool(c.get("report"))} for c in convs]


@router.post("/conversations")
def create_conversation(user: dict = Depends(get_current_user)):
    return storage.new_conversation(user["id"])


@router.get("/conversations/{conv_id}")
def get_conversation(conv_id: str, user: dict = Depends(get_current_user)):
    conv = storage.get_conversation(user["id"], conv_id)
    if not conv:
        raise HTTPException(404, "会话不存在")
    return conv


@router.delete("/conversations/{conv_id}")
def delete_conversation(conv_id: str, user: dict = Depends(get_current_user)):
    conv = storage.get_conversation(user["id"], conv_id)
    if not conv:
        raise HTTPException(404, "会话不存在")
    storage.delete(user["id"], f"conversations/{conv_id}.json.enc")
    return {"ok": True}


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/chat/stream")
async def chat_stream(body: ChatRequest, user: dict = Depends(get_current_user)):
    conv = storage.get_conversation(user["id"], body.conversation_id)
    if not conv:
        raise HTTPException(404, "会话不存在")
    message = (body.message or "").strip()
    if not message:
        raise HTTPException(400, "消息不能为空")

    prov = get_provider(body.provider) or get_provider("zhipu")
    sum_prov = get_any_provider(body.provider)  # 摘要可用任意已配置模型

    async def event_stream():
        # 1) RAG 检索（在用户自己的知识库中）
        citations = await indexer.search(user["id"], message)
        # 2) 构建消息序列
        history = conv.get("messages", [])[-MAX_HISTORY:]
        messages = [{"role": "system",
                     "content": prompts.SYSTEM_DOCTOR + "\n\n" + prompts.build_rag_context(citations)}]
        for m in history:
            messages.append({"role": m["role"], "content": m["content"]})
        messages.append({"role": "user", "content": message})

        conv["messages"].append({"role": "user", "content": message})
        # 流式过程中先给个临时标题（首条用户消息），结束后再用摘要覆盖
        if conv.get("title") in (None, "", "新的问诊"):
            conv["title"] = summary.local_title(conv["messages"])

        yield _sse({"type": "meta", "citations": [
            {"doc_name": c["doc_name"], "chunk_idx": c["chunk_idx"] + 1,
             "snippet": (c["text"] or "")[:120]} for c in citations]})

        # 3) 无可用模型：明确告知，不编造
        if prov is None or not prov.available():
            fallback = ("当前所选大模型未配置 API Key，暂时无法生成智能回答。"
                        "请在 config.yaml 中配置模型密钥后重试。"
                        "若情况紧急，请直接就医。")
            yield _sse({"type": "delta", "content": fallback})
            conv["messages"].append({"role": "assistant", "content": fallback,
                                     "citations": []})
            conv["title"] = await summary.summarize_title(conv["messages"], sum_prov)
            storage.save_conversation(user["id"], conv)
            yield _sse({"type": "done", "title": conv["title"]})
            return

        answer_parts: list[str] = []
        try:
            async for delta in prov.stream_chat(messages):
                answer_parts.append(delta)
                yield _sse({"type": "delta", "content": delta})
        except Exception as e:
            err = f"模型调用失败：{str(e)[:300]}"
            yield _sse({"type": "error", "message": err})
            # 模型出错也确保标题已整理（用本地兜底，避免无限“思考”后无标题）
            conv["title"] = summary.local_title(conv["messages"])
            storage.save_conversation(user["id"], conv)  # 保留用户消息与已收到的内容
            yield _sse({"type": "done", "title": conv["title"]})
            return

        answer = "".join(answer_parts)
        if not answer.strip():
            # 流正常结束但模型未返回任何内容（多半是中转站/模型名配置问题）
            answer = ("模型未返回任何内容，请检查 config.yaml 中该提供方的 base_url 与 chat_model 是否正确，"
                      "或确认中转站/官方接口当前可用。若情况紧急，请直接就医。")
        conv["messages"].append({"role": "assistant", "content": answer, "citations": citations})
        conv["provider"] = prov.name
        conv["model"] = prov.chat_model()
        conv["title"] = await summary.summarize_title(conv["messages"], sum_prov)
        storage.save_conversation(user["id"], conv)
        yield _sse({"type": "done", "title": conv["title"]})

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})


@router.post("/conversations/{conv_id}/report")
async def generate_report(conv_id: str, user: dict = Depends(get_current_user)):
    conv = storage.get_conversation(user["id"], conv_id)
    if not conv:
        raise HTTPException(404, "会话不存在")
    msgs = [m for m in conv.get("messages", []) if m["role"] in ("user", "assistant")]
    if len(msgs) < 2:
        raise HTTPException(400, "对话内容太少，无法生成报告")

    dialog = "\n".join(f"{'用户' if m['role'] == 'user' else '医生'}：{m['content']}" for m in msgs)

    provider = None
    for pid in (conv.get("provider"), "zhipu", "deepseek", "openai"):
        p = get_provider(pid) if pid else None
        if p and p.available():
            provider = p
            break
    if provider is None:
        raise HTTPException(400, "没有可用的大模型（未配置 API Key），无法生成报告")

    raw = await provider.chat([{"role": "user",
                                "content": prompts.REPORT_PROMPT.replace("{dialog}", dialog)}],
                              temperature=0.2)
    try:
        start, end = raw.find("{"), raw.rfind("}")
        report = json.loads(raw[start:end + 1])
    except (ValueError, json.JSONDecodeError):
        report = {"chief_complaint": raw[:2000], "parse_error": True}

    report["generated_note"] = "由 AI 问诊助手基于对话自动生成，仅供参考"
    conv["report"] = report
    storage.save_conversation(user["id"], conv)
    # 同步入库到医学报告库，便于后续按时间/分类检索查看
    meta = reports.save_report(user["id"], conv, report)
    report["_library"] = {"rid": meta["rid"], "category": meta["category"]}
    return report


@router.post("/conversations/summarize-all")
async def summarize_all(user: dict = Depends(get_current_user)):
    """为当前用户所有会话生成/刷新标题摘要（≤20字）。"""
    convs = storage.list_conversations(user["id"])
    prov = get_any_provider(None)
    updated = []
    for conv in convs:
        conv["title"] = await summary.summarize_title(conv.get("messages", []), prov)
        storage.save_conversation(user["id"], conv)
        updated.append({"id": conv["id"], "title": conv["title"]})
    return {"count": len(updated), "updated": updated}


@router.post("/conversations/{conv_id}/summarize")
async def summarize_one(conv_id: str, user: dict = Depends(get_current_user)):
    """为单个会话生成/刷新标题摘要（≤20字）。"""
    conv = storage.get_conversation(user["id"], conv_id)
    if not conv:
        raise HTTPException(404, "会话不存在")
    prov = get_any_provider(conv.get("provider"))
    conv["title"] = await summary.summarize_title(conv.get("messages", []), prov)
    storage.save_conversation(user["id"], conv)
    return {"id": conv["id"], "title": conv["title"]}

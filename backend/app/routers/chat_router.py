"""对话路由：会话管理 + SSE 流式问答（RAG 引用 + 防幻觉）+ 报告生成。"""
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import config, critic, memory, prompts, reports, storage, summary
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

    def _build_messages(message: str, citations: list[dict]) -> list[dict]:
        """组装送入生成模型的完整消息序列（含分层摘要 + 历史参考 + RAG 依据）。"""
        sys_parts = [prompts.SYSTEM_DOCTOR]

        # 分层摘要上下文（会话级 + 近期段落级）
        summary_ctx = memory.build_summary_context(conv)
        if summary_ctx:
            sys_parts.append(summary_ctx)

        # 历史关键词命中的相关内容（第二路上下文）
        history_ctx = memory.build_history_context(conv, message)
        if history_ctx:
            sys_parts.append(history_ctx)

        # RAG 知识库依据
        sys_parts.append(prompts.build_rag_context(citations))

        messages = [{"role": "system", "content": "\n\n".join(sys_parts)}]
        for m in conv.get("messages", [])[-MAX_HISTORY:]:
            messages.append({"role": m["role"], "content": m["content"]})
        messages.append({"role": "user", "content": message})
        return messages

    async def _generate(message: str, citations: list[dict]) -> str:
        """非流式生成一次完整回答（critic 循环内部使用）。"""
        messages = _build_messages(message, citations)
        return await prov.chat(messages, temperature=0.3)

    async def event_stream():
        # 1) RAG 检索（在用户自己的知识库中）
        citations = await indexer.search(user["id"], message)

        # 提前把用户消息写入会话（后续 critic 重试只替换 assistant 最终答案）
        conv["messages"].append({"role": "user", "content": message})
        if conv.get("title") in (None, "", "新的问诊"):
            conv["title"] = summary.local_title(conv["messages"])

        yield _sse({"type": "meta", "citations": [
            {"doc_name": c["doc_name"], "chunk_idx": c["chunk_idx"] + 1,
             "snippet": (c["text"] or "")[:120]} for c in citations]})

        # 2) 无可用模型：明确告知，不编造
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

        # 3) 生成 + critic 反思循环（先评后出）
        answer = ""
        final_citations = citations
        try:
            current_query = message
            for _round in range(max(1, config.CRITIC_MAX_ROUNDS)):
                answer = await _generate(current_query, final_citations)
                # critic 打分
                verdict = await critic.evaluate(current_query, answer)
                if verdict is None:
                    # critic 不可用/解析失败：直接采纳本轮，不阻断
                    break
                if verdict.passed:
                    break
                # 未达标：带上问题重新检索+生成
                current_query = critic.build_refine_query(message, verdict.issues)
                new_cites = await indexer.search(user["id"], current_query)
                if new_cites:
                    final_citations = new_cites
            # 兜底：若循环结束仍未拿到答案
            if not answer.strip():
                answer = ("模型未返回任何内容，请检查 config.yaml 中该提供方的 base_url 与 chat_model 是否正确，"
                          "或确认中转站/官方接口当前可用。若情况紧急，请直接就医。")
        except Exception as e:
            err = f"模型调用失败：{str(e)[:300]}"
            yield _sse({"type": "error", "message": err})
            conv["title"] = summary.local_title(conv["messages"])
            storage.save_conversation(user["id"], conv)
            yield _sse({"type": "done", "title": conv["title"]})
            return

        # 4) 输出最终答案（一次性流式推送，保持前端体验一致）
        yield _sse({"type": "delta", "content": answer})
        conv["messages"].append({"role": "assistant", "content": answer,
                                 "citations": final_citations})
        conv["provider"] = prov.name
        conv["model"] = prov.chat_model()
        conv["title"] = await summary.summarize_title(conv["messages"], sum_prov)
        storage.save_conversation(user["id"], conv)

        # 5) 更新分层摘要与关键词索引（异步不阻塞；异常静默）
        await memory.update_after_turn(conv, message, answer, sum_prov)
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

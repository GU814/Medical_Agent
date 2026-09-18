"""分层摘要上下文记忆：三级摘要 + 关键词索引 + 历史检索。

三级摘要随会话持久化（复用 storage 加密落盘），不新增文件体系：
  - 轮级 turn_summaries：每轮一句话摘要，turn 序号与 messages 对齐；
  - 段落级 paragraph_summaries：每 MEMORY_PARAGRAPH_SIZE 轮滚存一段；
  - 会话级 session_summary：随对话滚动更新的累积摘要；
  - 关键词索引 keywords：关键词 -> 命中轮次列表，用于历史关键词检索。

历史检索是「知识库 RAG 之外的第二路上下文」，二者并行、在 prompt 层拼接。
摘要生成优先走 LLM，失败时本地兜底，任何异常都不阻断主流程。
"""
import re
from typing import Optional

from . import config, prompts
from .llm import get_any_provider

_WORD_RE = re.compile(r"[a-zA-Z0-9]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# 需要从关键词中剔除的停用词（常见问诊虚词/代词）
_STOPWORDS = {
    "我", "你", "他", "她", "它", "是", "的", "了", "有", "没有", "没", "吗", "呢",
    "什么", "怎么", "为什么", "请问", "一下", "这个", "那个", "可以", "能", "会",
    "在", "和", "与", "或", "就", "都", "也", "还", "很", "太", "又", "才", "这",
    "那", "这些", "那些", "但是", "不过", "因为", "所以", "如果", "已经", "现在",
    "最近", "有点", "稍微", "比较", "感觉", "觉得", "想问", "咨询", "帮忙", "谢谢",
}


def _tokenize_keywords(text: str) -> list[str]:
    """从文本提取候选关键词：中文 2-gram + 英文词元，过滤停用词与单字。"""
    text = text or ""
    toks: list[str] = []
    toks += [w.lower() for w in _WORD_RE.findall(text) if len(w) >= 2]
    buf: list[str] = []
    for ch in text:
        if _CJK_RE.match(ch):
            buf.append(ch)
        else:
            if buf:
                toks += _ngrams(buf)
                buf = []
    if buf:
        toks += _ngrams(buf)
    out: list[str] = []
    for t in toks:
        if t in _STOPWORDS or len(t) < 2:
            continue
        out.append(t)
    return out


def _ngrams(chars: list[str]) -> list[str]:
    """中文切 2-gram（单字由 IDF 降权故忽略，避免噪声）。"""
    return [chars[i] + chars[i + 1] for i in range(len(chars) - 1)]


# ---------------- 数据结构初始化 ----------------

def _memory(conv: dict) -> dict:
    mem = conv.get("memory")
    if not isinstance(mem, dict):
        mem = {
            "turn_summaries": [],
            "paragraph_summaries": [],
            "session_summary": "",
            "keywords": {},
            "paragraph_size": config.MEMORY_PARAGRAPH_SIZE,
        }
        conv["memory"] = mem
    mem.setdefault("turn_summaries", [])
    mem.setdefault("paragraph_summaries", [])
    mem.setdefault("session_summary", "")
    mem.setdefault("keywords", {})
    mem.setdefault("paragraph_size", config.MEMORY_PARAGRAPH_SIZE)
    return mem


# ---------------- 摘要生成（LLM 优先，本地兜底） ----------------

async def _llm_summary(prompt: str, provider, fallback: str) -> str:
    """调用 LLM 生成摘要；失败/无模型时返回 fallback。"""
    if provider is None or not provider.available():
        return fallback
    try:
        raw = await provider.chat([{"role": "user", "content": prompt}], temperature=0.1)
        raw = (raw or "").strip()
        return raw if raw else fallback
    except Exception:
        return fallback


def _local_turn_summary(user_msg: str, assistant_msg: str) -> str:
    """轮级摘要本地兜底：截取用户消息前若干字。"""
    t = (user_msg or "").strip().replace("\n", " ")
    return t[:40] if t else "（空）"


async def summarize_turn(user_msg: str, assistant_msg: str, provider) -> str:
    """生成一轮的摘要（一句话）。"""
    dialog = f"用户：{user_msg}\n医生：{assistant_msg}"
    fallback = _local_turn_summary(user_msg, assistant_msg)
    return await _llm_summary(prompts.build_turn_summary_prompt(dialog), provider, fallback)


# ---------------- 历史关键词检索 ----------------

def search_history(conv: dict, query: str, max_hits: int = 5) -> list[dict]:
    """在会话历史中检索与 query 相关的轮次。

    返回 [{turn, summary, user, assistant}]，按命中关键词数量降序。
    仅做内存字典匹配，不调 LLM、不碰知识库 indexer。
    """
    mem = _memory(conv)
    keywords = mem.get("keywords", {})
    turn_summaries = {t.get("turn"): t.get("summary", "") for t in mem.get("turn_summaries", [])}
    messages = conv.get("messages", [])
    if not keywords or not query:
        return []

    q_terms = _tokenize_keywords(query)
    if not q_terms:
        return []

    hit_scores: dict[int, int] = {}
    for term in q_terms:
        for turn in keywords.get(term, []):
            hit_scores[turn] = hit_scores.get(turn, 0) + 1

    ranked = sorted(hit_scores.items(), key=lambda x: x[1], reverse=True)
    out = []
    for turn, score in ranked[:max_hits]:
        # 从 messages 定位该轮对应的 user/assistant 消息（turn 从 1 计）
        usr = ast = ""
        # turn 序号按「第几轮」，近似取 messages 中第 (turn-1)*2 与 (turn-1)*2+1 条
        # 更稳健：遍历 messages 统计问答轮次
        round_idx = 0
        for m in messages:
            if m.get("role") == "user":
                round_idx += 1
                if round_idx == turn:
                    usr = m.get("content", "")
            elif m.get("role") == "assistant" and round_idx == turn:
                ast = m.get("content", "")
        out.append({
            "turn": turn,
            "score": score,
            "summary": turn_summaries.get(turn, ""),
            "user": usr[:200],
            "assistant": ast[:200],
        })
    return out


def build_history_context(conv: dict, query: str) -> str:
    """把历史命中的相关内容组装成「历史参考」文本块（注入 system prompt）。"""
    if not config.MEMORY_ENABLE_HISTORY:
        return ""
    hits = search_history(conv, query)
    if not hits:
        return ""
    lines = ["【历史相关对话参考】（本次提问与以下历史内容相关，回答时需结合上下文）"]
    for h in hits:
        lines.append(f"- 第{h['turn']}轮：{h['summary'] or h['user']}")
    return "\n".join(lines)


def build_summary_context(conv: dict) -> str:
    """把摘要层（会话级 + 最近段落级）组装成上下文块。"""
    if not config.MEMORY_USE_SUMMARY:
        return ""
    mem = _memory(conv)
    parts: list[str] = []
    if mem.get("session_summary"):
        parts.append(f"【会话摘要】{mem['session_summary']}")
    paragraphs = mem.get("paragraph_summaries", [])
    if paragraphs:
        # 只取最近 2 段，避免上下文膨胀
        recent = paragraphs[-2:]
        parts.append("【近期对话摘要】" + "；".join(p.get("summary", "") for p in recent))
    return "\n".join(parts) if parts else ""


# ---------------- 摘要与索引更新 ----------------

async def update_after_turn(conv: dict, user_msg: str, assistant_msg: str, provider) -> None:
    """每轮结束后更新三级摘要与关键词索引（不阻塞回复，异常静默）。

    流程：
      1. 生成当前轮 turn_summary；
      2. 累计满 paragraph_size 轮 -> 滚存一段 paragraph_summary；
      3. 滚动更新 session_summary；
      4. 提取本轮关键词 -> 更新 keywords 索引。
    """
    try:
        mem = _memory(conv)
        turn_no = len(mem["turn_summaries"]) + 1

        # 1) 轮级摘要
        turn_summary = await summarize_turn(user_msg, assistant_msg, provider)
        mem["turn_summaries"].append({"turn": turn_no, "summary": turn_summary})

        # 2) 段落级滚存
        psize = mem.get("paragraph_size", config.MEMORY_PARAGRAPH_SIZE)
        if turn_no % psize == 0:
            seg = mem["turn_summaries"][-psize:]
            joined = "\n".join(t["summary"] for t in seg)
            para = await _llm_summary(
                prompts.build_paragraph_summary_prompt(joined),
                provider,
                joined[:120],
            )
            mem["paragraph_summaries"].append(
                {"range": [turn_no - psize + 1, turn_no], "summary": para}
            )

        # 3) 会话级滚动摘要
        recent_paras = "；".join(p["summary"] for p in mem["paragraph_summaries"][-3:])
        if recent_paras:
            prev = mem.get("session_summary", "")
            mem["session_summary"] = await _llm_summary(
                prompts.build_session_summary_prompt(prev, recent_paras),
                provider,
                prev or recent_paras[:200],
            )
        elif not mem.get("session_summary"):
            mem["session_summary"] = turn_summary

        # 4) 关键词索引
        for term in _tokenize_keywords(user_msg + " " + assistant_msg):
            kw = mem["keywords"]
            lst = kw.setdefault(term, [])
            if turn_no not in lst:
                lst.append(turn_no)
    except Exception:
        # 任何异常都不影响主回复流程
        pass

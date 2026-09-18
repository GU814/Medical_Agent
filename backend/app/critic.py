"""Critic 反思机制：生成后、输出前，用独立 critic 模型对结果评分。

低于 CRITIC_PASS_SCORE 则重新检索+生成，循环最多 CRITIC_MAX_ROUNDS 轮。
critic 模型优先取 CRITIC_PROVIDER 指定的独立 provider，缺省复用对话模型。
critic 不可用 / 解析失败时不阻断主流程（直接采纳首轮结果）。
"""
import json
from dataclasses import dataclass, field
from typing import Optional

from . import config, prompts
from .llm import get_critic_provider


@dataclass
class CriticResult:
    score: int
    issues: list[str] = field(default_factory=list)
    verdict: str = "pass"

    @property
    def passed(self) -> bool:
        return self.score >= config.CRITIC_PASS_SCORE


def parse_critic_output(raw: str) -> Optional[CriticResult]:
    """解析 critic 模型返回的 JSON；失败返回 None（调用方跳过 critic）。"""
    if not raw:
        return None
    raw = raw.strip()
    try:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end < start:
            return None
        obj = json.loads(raw[start:end + 1])
        score = obj.get("score")
        if score is None:
            return None
        score = int(round(float(score)))
        score = max(0, min(10, score))
        issues = obj.get("issues") or []
        if not isinstance(issues, list):
            issues = []
        verdict = obj.get("verdict", "pass" if score >= config.CRITIC_PASS_SCORE else "fail")
        return CriticResult(score=score, issues=[str(i) for i in issues], verdict=str(verdict))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


async def evaluate(query: str, answer: str) -> Optional[CriticResult]:
    """调用 critic 模型对回答评分；不可用/失败返回 None。"""
    try:
        prov = get_critic_provider()
        if prov is None or not prov.available():
            return None
        prompt = prompts.build_critic_prompt(query, answer)
        raw = await prov.chat([{"role": "user", "content": prompt}], temperature=0.0)
        return parse_critic_output(raw)
    except Exception:
        return None


def build_refine_query(query: str, issues: list[str]) -> str:
    """把 critic 指出的问题拼进 query，用于重新检索。"""
    if not issues:
        return query
    concise = "；".join(issues[:3])
    return f"{query}（注意补充：{concise}）"

"""配置加载：config.yaml + 环境变量覆盖 + 自动生成的密钥管理。

优先级：环境变量 > config.yaml > 内置默认值。
- config.yaml 缺失时回退到 config.example.yaml（模板），两者都没有则用空配置；
- 敏感信息（API Key 等）建议通过环境变量注入，避免硬编码进仓库。
"""
import base64
import os
import secrets
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]          # medical-agent/
DATA_DIR = ROOT / "data"
SECRET_FILE = DATA_DIR / "secret.key"


def _load_yaml() -> dict:
    for _name in ("config.yaml", "config.example.yaml"):
        _cfg_path = ROOT / _name
        if _cfg_path.exists():
            with open(_cfg_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    # 两者都不存在（例如全新克隆后尚未复制模板）：允许空配置启动，
    # 全部参数由环境变量提供。
    return {}


CFG = _load_yaml()


def _env(name: str):
    """读取环境变量；空字符串视为未设置。"""
    v = os.environ.get(name)
    return v if v not in (None, "") else None


def _ensure_secret(name: str) -> bytes:
    """读取或自动生成主密钥（jwt_secret / fernet_key 二合一存储）。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    store: dict = {}
    if SECRET_FILE.exists():
        try:
            raw = SECRET_FILE.read_text(encoding="utf-8").strip()
            store = {k: v for k, v in (line.split("=", 1) for line in raw.splitlines() if "=" in line)}
        except Exception:
            store = {}
    if name not in store:
        store[name] = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        SECRET_FILE.write_text("\n".join(f"{k}={v}" for k, v in store.items()), encoding="utf-8")
    return base64.urlsafe_b64decode(store[name])


class _Section:
    def __init__(self, d: dict):
        self._d = d or {}

    def __getattr__(self, k):
        try:
            return self._d[k]
        except KeyError:
            return None

    def get(self, k, default=None):
        return self._d.get(k, default)


SERVER = _Section(CFG.get("server") or {})
SECURITY = _Section(CFG.get("security") or {})
RAG_CFG = _Section(CFG.get("rag") or {})
LLM_CFG = _Section(CFG.get("llm") or {})

# ---- 服务器：环境变量 APP_HOST / APP_PORT / CORS_ORIGINS ----
HOST = _env("APP_HOST") or SERVER.get("host", "0.0.0.0")
PORT = int(_env("APP_PORT") or SERVER.get("port", 8600))
_cors_env = _env("CORS_ORIGINS")
if _cors_env:
    CORS_ORIGINS = [o.strip() for o in _cors_env.split(",") if o.strip()]
else:
    CORS_ORIGINS = SERVER.get("cors_origins", ["*"]) or ["*"]

# ---- 安全密钥：环境变量 JWT_SECRET / FERNET_KEY ----
JWT_SECRET = (_env("JWT_SECRET") or SECURITY.get("jwt_secret") or "").encode() or _ensure_secret("jwt_secret")
FERNET_KEY = (_env("FERNET_KEY") or SECURITY.get("fernet_key") or "").encode() or _ensure_secret("fernet_key")


def _apply_env_overrides(providers: dict) -> dict:
    """用环境变量覆盖 / 补充各 provider 配置。

    支持的环境变量（<PROVIDER> 为 provider 名的大写，如 OPENAI、ZHIPU）：
      LLM_<PROVIDER>_API_KEY           覆盖 api_key
      LLM_<PROVIDER>_BASE_URL          覆盖 base_url
      LLM_<PROVIDER>_CHAT_MODEL        覆盖 chat_model
      LLM_<PROVIDER>_VISION_MODEL      覆盖 vision_model
      LLM_<PROVIDER>_EMBEDDING_MODEL   覆盖 embedding_model
    若某 provider 仅存在于环境变量（config.yaml 中没有），也会被加入。
    """
    out = {name: dict(settings or {}) for name, settings in providers.items()}
    _fields = {
        "API_KEY": "api_key",
        "BASE_URL": "base_url",
        "CHAT_MODEL": "chat_model",
        "VISION_MODEL": "vision_model",
        "EMBEDDING_MODEL": "embedding_model",
    }
    for name, settings in out.items():
        prefix = f"LLM_{name.upper()}_"
        for env_suffix, field in _fields.items():
            v = _env(prefix + env_suffix)
            if v is not None:
                settings[field] = v
    # 仅出现在环境变量中的 provider（形如 LLM_MYPROV_API_KEY=sk-xxx）
    for env_name, val in os.environ.items():
        if env_name.startswith("LLM_") and env_name.endswith("_API_KEY"):
            prov = env_name[len("LLM_"):-len("_API_KEY")]
            if prov and prov not in out:
                out[prov] = {"api_key": val}
    return out


PROVIDERS = _apply_env_overrides((CFG.get("llm") or {}).get("providers") or {})

# ---- 默认 provider：环境变量 LLM_DEFAULT_PROVIDER ----
DEFAULT_PROVIDER = _env("LLM_DEFAULT_PROVIDER") or (CFG.get("llm") or {}).get("default_provider", "zhipu")

CHUNK_SIZE = int(RAG_CFG.get("chunk_size", 500))
CHUNK_OVERLAP = int(RAG_CFG.get("chunk_overlap", 80))
TOP_K = int(RAG_CFG.get("top_k", 6))
MIN_SCORE = float(RAG_CFG.get("min_score", 0.05))

# ---- RAG 混合检索增强配置（环境变量 > config.yaml > 默认）----
# 本地向量模型（HuggingFace 名或本地路径）
RAG_EMBED_MODEL = _env("RAG_EMBED_MODEL") or RAG_CFG.get("embed_model", "BAAI/bge-small-zh-v1.5")
# 是否启用 LLM 精排
RAG_USE_RERANK = (_env("RAG_USE_RERANK") or str(RAG_CFG.get("use_rerank", True))).lower() in ("1", "true", "yes", "on")
# 精排候选数（RRF 融合后取前 N 送入 LLM）
RAG_RERANK_TOP_N = int(_env("RAG_RERANK_TOP_N") or RAG_CFG.get("rerank_top_n", 20))
# 精排使用的 provider 名；留空则用默认对话 provider（default_provider）
RAG_RERANK_PROVIDER = _env("RAG_RERANK_PROVIDER") or RAG_CFG.get("rerank_provider", "")
# RRF 平滑常数
RAG_RRF_K = int(_env("RAG_RRF_K") or RAG_CFG.get("rrf_k", 60))
# 向量化批大小
RAG_EMBED_BATCH = int(_env("RAG_EMBED_BATCH") or RAG_CFG.get("embed_batch", 32))

# ---- Critic 反思机制（环境变量 > config.yaml > 默认）----
# 独立 critic provider 名；留空则缺省复用对话模型（get_any_provider）
CRITIC_PROVIDER = _env("CRITIC_PROVIDER") or (CFG.get("critic") or {}).get("provider", "")
# 达标阈值（0-10，>= 该值判定通过）
CRITIC_PASS_SCORE = int(_env("CRITIC_PASS_SCORE") or (CFG.get("critic") or {}).get("pass_score", 7))
# 最大反思轮数（3-5）
CRITIC_MAX_ROUNDS = int(_env("CRITIC_MAX_ROUNDS") or (CFG.get("critic") or {}).get("max_rounds", 3))

# ---- 分层摘要上下文机制（环境变量 > config.yaml > 默认）----
# 段落级摘要滚存粒度（每 N 轮滚存一段）
MEMORY_PARAGRAPH_SIZE = int(_env("MEMORY_PARAGRAPH_SIZE") or (CFG.get("memory") or {}).get("paragraph_size", 5))
# 是否启用历史关键词检索
MEMORY_ENABLE_HISTORY = (_env("MEMORY_ENABLE_HISTORY") or str((CFG.get("memory") or {}).get("enable_history", True))).lower() in ("1", "true", "yes", "on")
# 是否在上下文中注入摘要层（轮级/段落级/会话级）
MEMORY_USE_SUMMARY = (_env("MEMORY_USE_SUMMARY") or str((CFG.get("memory") or {}).get("use_summary", True))).lower() in ("1", "true", "yes", "on")

USERS_DB = DATA_DIR / "users.db"
SPACES_DIR = DATA_DIR / "spaces"

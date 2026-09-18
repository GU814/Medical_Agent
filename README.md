# 医学问询智能体（Medical Consult Agent）

一个带 RAG 知识库、防幻觉约束、健康档案与提醒、拍照识药的医学问询智能体。
当前形态：**FastAPI 后端 + 微信小程序前端**。

## 功能总览

| 模块 | 说明 |
|---|---|
| 用户隔离与隐私 | 每位用户独立加密数据空间（对话/档案/知识库互不串扰），JWT 鉴权 + Fernet 加密存储 |
| 知识库 RAG | 上传 PDF/DOCX/MD/TXT → 自动分块索引 → 混合检索（BM25 + 本地向量 → RRF 融合 → LLM 精排）作为依据，并标注引用来源 |
| 防幻觉 | 回答严格基于知识库或问诊对话；无依据时明确回复"无法确认"，禁止编造药名/剂量/诊断 |
| 流式问诊 | SSE 逐字输出；严谨追问（症状/病程/既往史等）；结束后自动生成病情检查报告单 |
| 分层摘要上下文 | 三级摘要（轮级/段落级/会话级）加速检索；关键词命中历史则参考上次对话并结合当前上下文 |
| Critic 反思 | 生成后输出前用独立 critic 模型评分，低于阈值自动重检索+重生成（3-5 轮），保证医疗回答严谨 |
| 模型切换 | Provider 适配层（zhipu/deepseek/openai 及任意 OpenAI 兼容端点），前端可选 |
| 健康档案与提醒 | 健康追踪 + 行为建议（多喝水/多运动/按时吃药）；用户自建提醒闹钟 |
| 拍照识药 | 上传药物图片/说明书照片，识别药名、功效与注意事项；模糊时明确提示 |

## 目录结构

```
medical-agent/
├── config.example.yaml    # 配置模板（提交到仓库；复制为 config.yaml 后填写）
├── .env.example           # 环境变量模板（可选；.env 不进仓库）
├── backend/               # FastAPI 后端
│   └── app/
│       ├── main.py        # 入口与路由汇总
│       ├── config.py      # 配置加载（环境变量 > config.yaml > 默认值）
│       ├── auth.py        # 注册/登录/JWT
│       ├── crypto.py      # Fernet 加密（按用户派生密钥）
│       ├── storage.py     # 用户数据空间（隔离）
│       ├── memory.py      # 分层摘要上下文（三级摘要 + 历史关键词检索）
│       ├── critic.py      # Critic 反思机制（生成后评分 + 重试循环）
│       ├── rag/           # 文档解析/分块/索引/检索
│       ├── llm/           # 模型适配层（zhipu/deepseek/openai 兼容）
│       └── routers/       # chat(SSE)/kb/profile/reminders/vision/report
├── weapp/                 # 微信小程序源码
│   ├── project.config.example.json  # 小程序配置模板（复制后填 AppID）
│   └── utils/api.js       # BASE_URL 等环境配置
├── scripts/               # 服务器运维脚本（.sh）
└── data/                  # 运行时数据（不进仓库，见安全说明）
```

## 从模板开始（克隆本仓库后必读）

以下文件**不在仓库中**，需要从模板复制并填写：

```bash
# 1. 后端配置：复制模板并填入你的 API Key
cp config.example.yaml config.yaml

# 2.（可选）环境变量方式注入 Key（优先级高于 config.yaml）
cp .env.example .env

# 3. 小程序配置：复制模板并填入你的小程序 AppID
cp weapp/project.config.example.json weapp/project.config.json
```

> `config.yaml`、`.env`、`weapp/project.config.json`、`data/` 均已被 `.gitignore`
> 排除——**真实 API Key、AppSecret 绝不能提交进仓库**。
> 也可以完全不改 `config.yaml`，直接用环境变量注入，见 `.env.example` 说明
> （命名规则 `LLM_<PROVIDER>_API_KEY` 等，由 `backend/app/config.py` 解析）。

> **混合检索的向量模型**：RAG 默认用本地中文向量模型 `BAAI/bge-small-zh-v1.5`
> （经 `sentence-transformers` 加载），首次检索时会自动联网下载（约 100MB）。
> 若离线/无法下载，可通过 `rag.embed_model` 或环境变量 `RAG_EMBED_MODEL` 指向本地
> 已下载的模型目录；模型不可用时检索会自动降级为纯 BM25，不影响基本功能。

> **Critic 反思与分层摘要**：默认开启。critic 缺省复用对话模型做评分，低于
> `critic.pass_score`（默认 7）自动重检索+重生成，最多 `critic.max_rounds`（默认 3）轮；
> 若配置了独立 `critic.provider`（或环境变量 `CRITIC_PROVIDER`），则改用该 provider 评分。
> critic 不可用时直接采纳首轮结果，不阻断回复。分层摘要随会话加密持久化，
> 相关参数见 `config.example.yaml` 的 `critic` / `memory` 段。

## 快速开始

### 1. 启动后端

```bash
# 创建虚拟环境并安装依赖（首次）
python -m venv .venv
.venv/Scripts/pip install -r backend/requirements.txt     # Windows
# source .venv/bin/activate && pip install -r backend/requirements.txt  # Linux/macOS

# 启动（默认 http://0.0.0.0:8600）
cd backend
../.venv/Scripts/python -m uvicorn app.main:app --host 0.0.0.0 --port 8600
```

启动成功后访问 `http://127.0.0.1:8600/health` 应返回 `{"ok": true}`。

### 2. 运行小程序

1. 用微信开发者工具「导入项目」选择 `weapp/` 目录（AppID 用你自己的）；
2. 开发者工具右上角「详情 → 本地设置」勾选**不校验合法域名**；
3. 小程序默认请求 `http://127.0.0.1:8600`（见 `weapp/utils/api.js`）。

> **真机调试**：把 `api.js` 中的 `LAN_BASE` 改为你电脑的局域网 IP，或直接在
> Storage 中写入键 `ma_baseurl`（推荐，免改代码，优先级最高）。
>
> **上线部署**：`PROD_BASE` 必须替换为**已备案 HTTPS 域名**，并在微信公众平台
> 「开发 → 开发管理 → 服务器域名 → request 合法域名」登记。

## 安全说明

- 密码使用 PBKDF2-SHA256（12 万轮）哈希，绝不明文存储。
- 所有用户业务数据（对话、档案、知识库、提醒）以 Fernet 加密落盘，密钥按用户派生。
- JWT 每次请求校验，数据接口仅能访问本人数据空间。
- **`data/secret.key` 是数据加密主密钥：备份服务器/迁移时必须随后端整体带走，
  丢失将导致历史加密数据永久无法解密；同时绝不能提交进仓库。**

## 免责声明

本系统输出仅供健康参考，不构成医疗诊断意见；急症请立即就医。

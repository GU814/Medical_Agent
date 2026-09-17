"""FastAPI 入口：路由汇总 + 桌面版前端静态托管。"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .routers import auth_router, chat_router, kb_router, profile_router, reports_router, vision_router

app = FastAPI(title="医学问询智能体", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS if config.CORS_ORIGINS != ["*"] else ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(chat_router.router)
app.include_router(kb_router.router)
app.include_router(profile_router.router)
app.include_router(reports_router.router)
app.include_router(vision_router.router)

DESKTOP_DIR = Path(__file__).resolve().parents[2] / "frontend" / "desktop"


@app.get("/api/health")
def health():
    return {"ok": True, "service": "medical-agent"}


# 桌面版前端静态托管（同源部署，避免 CORS/跨域问题）
if DESKTOP_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(DESKTOP_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(str(DESKTOP_DIR / "index.html"))

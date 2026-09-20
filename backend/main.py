"""Lecturely FastAPI 应用入口。

组装：日志 → 数据库（迁移）→ 崩溃恢复 → REST 路由 → WebSocket → 前端静态托管。
运行：python -m backend.main  （或 uvicorn backend.main:app）
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import BASE_DIR, get_config
from .errors import LecturelyError
from .logging_setup import get_logger, setup_logging
from .storage.db import close_engine, init_engine
from .storage.repositories.sessions import SessionRepository
from .api import health as health_api
from .api import devices as devices_api
from .api import settings as settings_api
from .api import sessions as sessions_api
from .api import folders as folders_api
from .api import recordings as recordings_api
from .api import upload as upload_api
from .api import export as export_api
from .api import search as search_api
from .api import speakers as speakers_api
from .api import ai as ai_api
from .websocket.gateway import get_gateway

logger = get_logger(__name__)

FRONTEND_DIST = BASE_DIR / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_config()
    setup_logging(cfg.data_dir / "logs")
    # 模型下载镜像（huggingface.co 不可达时用 hf-mirror）
    os.environ.setdefault("HF_ENDPOINT", cfg.hf_endpoint)
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    logger.info("Lecturely %s 启动中… data_dir=%s", __version__, cfg.data_dir)
    if not cfg.is_local_only and not cfg.api_token:
        logger.warning("当前绑定非 localhost（%s）且未设置 LECTURELY_API_TOKEN，存在安全风险！", cfg.host)

    await init_engine(cfg.database_url)
    logger.info("数据库就绪：%s", cfg.database_url)

    # 崩溃恢复：标记被中断的会话
    repo = SessionRepository()
    interrupted = await repo.list_interrupted()
    for sess in interrupted:
        await repo.update_status(sess["id"], "recovered")
        logger.warning("检测到未完成会话 %s（%s），已标记为 recovered", sess["id"], sess.get("title"))
    if interrupted:
        logger.info("共恢复 %d 个未完成会话", len(interrupted))

    # 后台预加载 ASR 引擎（模型下载+加载+warmup，不阻塞启动）
    from .asr.wlk_engine import preload_engine
    app.state.engine_task = asyncio.create_task(preload_engine(cfg))
    app.state.gateway = get_gateway(cfg)

    yield

    await app.state.gateway.shutdown()
    await close_engine()
    logger.info("Lecturely 已停止")


def create_app() -> FastAPI:
    app = FastAPI(title="Lecturely", version=__version__, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],  # Vite dev
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(LecturelyError)
    async def lecturely_error_handler(_request, exc: LecturelyError):  # noqa: ANN001
        return JSONResponse(status_code=400, content={"error": exc.to_dict()})

    # REST 路由
    app.include_router(health_api.router)
    app.include_router(devices_api.router)
    app.include_router(settings_api.router)
    app.include_router(sessions_api.router)
    app.include_router(folders_api.router)
    app.include_router(recordings_api.router)
    app.include_router(upload_api.router)
    app.include_router(export_api.router)
    app.include_router(search_api.router)
    app.include_router(speakers_api.router)
    app.include_router(ai_api.router)

    # WebSocket 路由
    @app.websocket("/ws/live")
    async def ws_live(ws: WebSocket):
        await get_gateway(get_config()).handle_connection(ws)

    # 前端静态托管（生产模式：frontend/dist 存在时）
    if FRONTEND_DIST.exists():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            if full_path.startswith("api/") or full_path.startswith("ws"):
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
            index = FRONTEND_DIST / "index.html"
            target = FRONTEND_DIST / full_path
            if full_path and target.is_file() and ".." not in full_path:
                return FileResponse(target)
            return FileResponse(index)

    return app


app = create_app()


def main() -> None:
    import uvicorn
    cfg = get_config()
    uvicorn.run("backend.main:app", host=cfg.host, port=cfg.port,
                reload=False, log_level="info")


if __name__ == "__main__":
    main()

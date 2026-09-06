# -*- coding: utf-8 -*-
"""
FastAPI 应用入口（M0）。

启动：
    uvicorn server.main:app --host 127.0.0.1 --port 8000

纪律：
    - 只 bind 127.0.0.1（本机桌面壳/浏览器；不暴露局域网）
    - CORS 放开 localhost（Vite dev server 5173 端口联调）
    - M1 起挂 frontend/dist 静态（StaticFiles html=True，HashRouter 无需 fallback）
"""

import os
import sys

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from data.database import init_db
from server.routers import agent, core, diary, diagnosis, holdings, settings
from server.routers import alert, reports


def create_app() -> FastAPI:

    @asynccontextmanager
    async def lifespan(_app):
        # v1.1 预警调度器：daemon 线程，仅交易时段 + 单标的 10 分钟限频；
        # 无启用规则时空转零网络。INVEST_DISABLE_ALERT_SCHEDULER=1 可禁（测试用）。
        if os.environ.get("INVEST_DISABLE_ALERT_SCHEDULER", "") != "1":
            from services import alert_service
            alert_service.start_scheduler()
        yield
        from services import alert_service
        alert_service.stop_scheduler()

    app = FastAPI(title="invest-concierge", version="1.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",   # Vite dev
            "http://127.0.0.1:5173",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(core.router)
    app.include_router(holdings.router)
    app.include_router(diary.router)
    app.include_router(agent.router)
    app.include_router(diagnosis.router)
    app.include_router(settings.router)
    app.include_router(alert.router)
    app.include_router(reports.router)

    # M1：前端构建产物存在则托管（桌面壳/纯浏览器模式的 UI 入口）
    # 打包(exe)时 __file__ 指向 _MEIPASS 临时解包目录，dist 以 'frontend/dist' 打包在其中
    _base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _dist = os.path.join(_base, "frontend", "dist")
    if os.path.isdir(_dist):
        from fastapi.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")

    return app


# 启动时库结构自愈（幂等：CREATE IF NOT EXISTS / 迁移补列）
init_db()

app = create_app()

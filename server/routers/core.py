# -*- coding: utf-8 -*-
"""
核心路由（M0）：health / status / nav——无重活的轻量端点。
"""

from fastapi import APIRouter

from services import nav_service, status_service

router = APIRouter(prefix="/api", tags=["core"])


@router.get("/health")
def health():
    """存活探针：uvicorn 起没起、key 配没配（无外部调用，毫秒级）"""
    return status_service.health()


@router.get("/status")
def status():
    """状态栏数据：引擎状态点 + 数据源健康 + 上次刷新 + 模型版本"""
    return status_service.status_bar()


@router.get("/nav")
def nav():
    """React 侧栏导航：各轨道 live 页清单（services 层唯一权威，不 import sidebar）"""
    return nav_service.nav_payload()

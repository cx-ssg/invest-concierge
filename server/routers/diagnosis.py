# -*- coding: utf-8 -*-
"""
诊断路由（M0）：GET /api/stocks/{code}/diagnosis。

冷启 15-40s（5+ 数据源）→ run_in_threadpool，绝不阻塞事件循环。
"""

from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool

from services import diagnosis_service

router = APIRouter(prefix="/api", tags=["diagnosis"])


@router.get("/stocks/{code}/diagnosis")
async def get_diagnosis(code: str):
    """6 引擎诊断 payload（24h TTL 缓存；返回统一过 to_jsonable）"""
    return await run_in_threadpool(diagnosis_service.get, code)

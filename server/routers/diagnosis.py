# -*- coding: utf-8 -*-
"""
诊断路由（M0）：GET /api/stocks/{code}/diagnosis。

冷启 15-40s（5+ 数据源）→ run_in_threadpool，绝不阻塞事件循环。
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from services import diagnosis_service

router = APIRouter(prefix="/api", tags=["diagnosis"])


class ReviewIn(BaseModel):
    """人审提交体（F4）。"""

    decision: str = Field(..., description="approve（通过）或 revise（要求修改）")
    note: str = Field("", description="要求修改时的批注（会带进重跑后的报告）")


@router.get("/stocks/{code}/diagnosis")
async def get_diagnosis(code: str):
    """6 引擎诊断 payload（24h TTL 缓存；返回统一过 to_jsonable）

    `ORCHESTRATOR=graph` 时：图跑到人审节点挂起，响应 `_orchestrator.review_status == "pending"`，
    此时用下方 review 端点推进。
    """
    return await run_in_threadpool(diagnosis_service.get, code)


@router.post("/stocks/{code}/diagnosis/review")
async def submit_review(code: str, body: ReviewIn):
    """人审提交（F4）：把挂起的人审节点继续推进。

    - `approve` ⇒ 图收口，返回最终诊断 payload
    - `revise`  ⇒ 带批注回到 `analyze` 重跑（受最大轮次限制，到限自动通过）

    ⚠️ 可能重跑 6 引擎（15-40s）⇒ 与 GET 一样走 `run_in_threadpool`，不阻塞事件循环。
    """
    return await run_in_threadpool(diagnosis_service.review, code, body.decision, body.note)

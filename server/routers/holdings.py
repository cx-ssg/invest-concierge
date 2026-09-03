# -*- coding: utf-8 -*-
"""
持仓路由（M0）：funds CRUD + 资产概览。
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import Optional

from services import holdings_service

router = APIRouter(prefix="/api", tags=["holdings"])


class FundIn(BaseModel):
    code: str
    name: str = ""
    amount: float = Field(gt=0)
    cost_nav: float = Field(gt=0)
    hold_shares: float = Field(gt=0)
    note: str = ""


class FundPatch(BaseModel):
    name: Optional[str] = None
    amount: Optional[float] = None
    cost_nav: Optional[float] = None
    hold_shares: Optional[float] = None
    note: Optional[str] = None


@router.get("/holdings/funds")
def list_funds():
    """全量持仓（实时字段尽力补齐）"""
    return holdings_service.list_funds()


@router.post("/holdings/funds")
def add_fund(body: FundIn):
    """新增/覆盖一条持仓"""
    return holdings_service.add_fund(
        code=body.code, name=body.name, amount=body.amount,
        cost_nav=body.cost_nav, hold_shares=body.hold_shares, note=body.note,
    )


@router.put("/holdings/funds/{code}")
def update_fund(code: str, body: FundPatch):
    """部分更新（缺省字段沿用旧值）"""
    return holdings_service.update_fund(
        code, name=body.name, amount=body.amount,
        cost_nav=body.cost_nav, hold_shares=body.hold_shares, note=body.note,
    )


@router.delete("/holdings/funds/{code}")
def delete_fund(code: str):
    """删除一条持仓"""
    return holdings_service.delete_fund(code)


@router.get("/dashboard/summary")
def dashboard_summary():
    """资产总览（纯库内字段，不拉行情）"""
    return holdings_service.summary()

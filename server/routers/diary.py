# -*- coding: utf-8 -*-
"""
日记路由（M0）：list / add / delete。
"""

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from services import diary_service

router = APIRouter(prefix="/api", tags=["diary"])


class DiaryIn(BaseModel):
    date: Optional[str] = None      # YYYY-MM-DD，缺省今天
    fund_code: str = ""
    fund_name: str = ""
    action: str = ""
    amount: float = 0.0
    note: str = ""


@router.get("/diary")
def list_diary():
    """全量日记（date DESC）"""
    return diary_service.list_entries()


@router.post("/diary")
def add_diary(body: DiaryIn):
    """追加一条日记"""
    return diary_service.add_entry(
        date=body.date, fund_code=body.fund_code, fund_name=body.fund_name,
        action=body.action, amount=body.amount, note=body.note,
    )


@router.delete("/diary/{entry_id}")
def delete_diary(entry_id: int):
    """按 id 删除单条（参数绑定）"""
    return diary_service.remove_entry(entry_id)

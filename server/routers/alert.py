# -*- coding: utf-8 -*-
"""预警路由（v1.1 粘性三件套 A）。"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from services import alert_service

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


class AlertIn(BaseModel):
    kind: str                       # 'fund' | 'stock'
    symbol: str = Field(min_length=1, max_length=16)
    name: str = ""
    metric: str                     # 'estimate_pct' | 'price'
    op: str                         # 'above' | 'below'
    threshold: float


class AlertPatchIn(BaseModel):
    enabled: bool


@router.get("")
def list_alerts():
    """全部预警规则（新→旧）"""
    return {"ok": True, "alerts": alert_service.alerts_list()}


@router.post("")
def create_alert(body: AlertIn):
    return alert_service.alerts_create(
        body.kind, body.symbol, body.name, body.metric, body.op, body.threshold)


@router.patch("/{alert_id}")
def patch_alert(alert_id: int, body: AlertPatchIn):
    return alert_service.alerts_patch(alert_id, body.enabled)


@router.delete("/{alert_id}")
def delete_alert(alert_id: int):
    return alert_service.alerts_delete(alert_id)


@router.get("/events")
def alert_events(limit: int = 50):
    """触发事件（新→旧）+ 未读数（顶栏角标 30s 轮询源）"""
    return alert_service.alerts_events(limit=limit)


@router.post("/events/read-all")
def mark_events_read():
    return alert_service.alerts_events_mark_read()

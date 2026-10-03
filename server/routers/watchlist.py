# -*- coding: utf-8 -*-
"""自选股 / 股票持仓路由（H5 · P3）。

自选股页的两个 tab 共用这一组接口：
- 「自选股」= /api/watchlist（库表 watchlist 早已存在，此前只缺 HTTP 层与 React 页面）
- 「持仓股票」= /api/stocks/holdings（库表 stock_holdings 同上）
行情逐只补齐（get_stock_info，东财不可达时自带腾讯 fallback），取不到就留空降级。
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from services import watchlist_service

router = APIRouter(prefix="/api", tags=["watchlist"])


class WatchlistIn(BaseModel):
    code: str = Field(min_length=1, max_length=16)
    name: str = ""
    market: str = ""


class StockHoldingIn(BaseModel):
    code: str = Field(min_length=1, max_length=16)
    name: str = ""
    quantity: float = Field(gt=0)
    cost_price: float = Field(gt=0)


@router.get("/watchlist")
async def list_watchlist():
    """自选列表（按加入时间倒序 + 实时行情）"""
    return await run_in_threadpool(watchlist_service.list_watchlist)


@router.get("/watchlist/search")
async def search_watchlist_candidates(q: str = ""):
    """按代码/名称搜 A 股（加入自选前的候选搜索）"""
    return await run_in_threadpool(watchlist_service.search_stock, q)


@router.post("/watchlist")
async def add_watchlist(body: WatchlistIn):
    """加入自选（名称缺省自动用行情补全）"""
    return await run_in_threadpool(
        watchlist_service.add_watchlist, body.code, body.name, body.market)


@router.delete("/watchlist/{code}")
async def remove_watchlist(code: str):
    """移出自选"""
    return await run_in_threadpool(watchlist_service.remove_watchlist, code)


@router.get("/stocks/holdings")
async def list_stock_holdings():
    """股票持仓列表（含市值/浮动盈亏，行情不可得时留空）"""
    return await run_in_threadpool(watchlist_service.list_stock_holdings)


@router.post("/stocks/holdings")
async def add_stock_holding(body: StockHoldingIn):
    """新增/加仓（加权平均成本）"""
    return await run_in_threadpool(
        watchlist_service.add_stock_holding, body.code, body.name, body.quantity, body.cost_price)


@router.delete("/stocks/holdings/{code}")
async def remove_stock_holding(code: str):
    """删除股票持仓"""
    return await run_in_threadpool(watchlist_service.remove_stock_holding, code)

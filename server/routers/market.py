# -*- coding: utf-8 -*-
"""市场行情路由（H5 · P2）：/api/market/{index,sectors,sentiment,moneyflow,valuation}。

数据源是 P1 已工具化的同一批引擎（可能挂 5-25s）⇒ 全部 run_in_threadpool，不阻塞事件循环。
"""

from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool

from services import market_service

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/index")
async def market_index():
    """指数 tab：主要宽基指数实时行情"""
    return await run_in_threadpool(market_service.index)


@router.get("/sectors")
async def market_sectors():
    """板块 tab：今日行业板块涨幅榜"""
    return await run_in_threadpool(market_service.sectors)


@router.get("/sentiment")
async def market_sentiment():
    """情绪 tab：涨停/跌停、最高连板、涨跌家数"""
    return await run_in_threadpool(market_service.sentiment)


@router.get("/moneyflow")
async def market_moneyflow():
    """资金 tab：大盘资金全景 + 行业板块资金榜"""
    return await run_in_threadpool(market_service.moneyflow)


@router.get("/valuation")
async def market_valuation():
    """估值 tab：主要宽基指数 PE/PB 及历史分位"""
    return await run_in_threadpool(market_service.valuation)

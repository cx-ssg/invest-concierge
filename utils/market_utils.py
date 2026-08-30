# -*- coding: utf-8 -*-
"""
市场工具函数 - 从 web_agent.py 迁移的共享工具
"""

import logging

from data.cache import cached, CACHE_MARKET

logger = logging.getLogger(__name__)


# ==================== 大盘指数 ====================


@cached(ttl=CACHE_MARKET)
def get_market_index():
    """获取大盘指数行情（带缓存）"""
    import data.market_api as market_api

    try:
        indexes = market_api.get_market_index()
        if indexes:
            return indexes
    except Exception as e:
        logger.warning("获取大盘指数失败: %s", e)
    return []


# ==================== 热门板块 ====================


def get_hot_sectors():
    """获取热门板块行情"""
    import data.market_api as market_api

    try:
        sectors = market_api.get_hot_sectors()
        if sectors:
            return sectors[:10]
    except Exception as e:
        logger.warning("获取热门板块失败: %s", e)
    return []


# ==================== 指数估值 ====================


@cached(ttl=CACHE_MARKET)
def get_valuation_data():
    """获取指数估值数据（来源：蛋卷基金，带缓存）"""
    from utils.common import safe_request

    try:
        url = "https://danjuanapp.com/djapi/index/valuation"
        resp = safe_request(url, timeout=10)
        if resp and resp.status_code == 200:
            raw = resp.json()
            if raw.get('data') and raw['data'].get('items'):
                items = raw['data']['items']
                result = []
                for item in items:
                    if not item.get('index_code'):
                        continue
                    pe_percentile = float(item.get('pe_percentile', 0))
                    pb_percentile = float(item.get('pb_percentile', 0))

                    if pe_percentile <= 30:
                        eva_type = "低估值"
                    elif pe_percentile <= 70:
                        eva_type = "适中"
                    else:
                        eva_type = "高估值"

                    result.append({
                        'name': item.get('index_name', ''),
                        'code': item.get('index_code', ''),
                        'pe': float(item.get('pe', 0)),
                        'pb': float(item.get('pb', 0)),
                        'pe_percentile': pe_percentile,
                        'pb_percentile': pb_percentile,
                        'eva_type': eva_type,
                    })
                if result:
                    return result[:20]
    except Exception as e:
        logger.warning("获取估值数据失败: %s", e)

    return _get_mock_valuation_data()


def _get_mock_valuation_data():
    """获取模拟估值数据（当API访问失败时使用）"""
    import data.market_api as market_api

    try:
        data = market_api.get_valuation_data()
        if data:
            result = []
            for item in data[:20]:
                pe_percentile = float(item.get('pe_percentile', 50))
                pb_percentile = float(item.get('pb_percentile', 50))

                if pe_percentile <= 30:
                    eva_type = "低估值"
                elif pe_percentile <= 70:
                    eva_type = "适中"
                else:
                    eva_type = "高估值"

                result.append({
                    'name': item.get('name', item.get('index_name', '')),
                    'code': item.get('code', item.get('index_code', '')),
                    'pe': float(item.get('pe', 0)),
                    'pb': float(item.get('pb', 0)),
                    'pe_percentile': pe_percentile,
                    'pb_percentile': pb_percentile,
                    'eva_type': eva_type,
                })
            return result
    except Exception:
        pass

    return [
        {'name': '沪深300', 'code': '000300', 'pe': 12.5, 'pb': 1.4, 'pe_percentile': 38.5, 'pb_percentile': 28.3, 'eva_type': '适中'},
        {'name': '中证500', 'code': '399625', 'pe': 22.8, 'pb': 1.8, 'pe_percentile': 42.0, 'pb_percentile': 35.6, 'eva_type': '适中'},
        {'name': '创业板指', 'code': '399006', 'pe': 45.6, 'pb': 4.2, 'pe_percentile': 55.2, 'pb_percentile': 48.9, 'eva_type': '适中'},
        {'name': '科创50', 'code': '000688', 'pe': 58.3, 'pb': 5.1, 'pe_percentile': 62.8, 'pb_percentile': 58.4, 'eva_type': '适中'},
    ]


# ==================== 情绪温度计 ====================


def calc_market_sentiment():
    """计算市场情绪温度计（基于估值水平）"""
    val_data = get_valuation_data()
    if not val_data:
        return None

    total_score = 0
    count = 0
    for item in val_data:
        pe_pct = item.get('pe_percentile', 50)
        total_score += pe_pct
        count += 1

    if count == 0:
        return None

    avg_score = total_score / count

    if avg_score <= 20:
        status = "极度低估（贪婪时刻）"
        emoji = "🟢"
        color = "#51cf66"
    elif avg_score <= 40:
        status = "低估（布局机会）"
        emoji = "🔵"
        color = "#339af0"
    elif avg_score <= 60:
        status = "适中（正常持有）"
        emoji = "🟡"
        color = "#ffd43b"
    elif avg_score <= 80:
        status = "高估（谨慎追高）"
        emoji = "🟠"
        color = "#ff922b"
    else:
        status = "极度高估（注意风险）"
        emoji = "🔴"
        color = "#ff6b6b"

    return {
        'score': round(avg_score, 1),
        'status': status,
        'emoji': emoji,
        'color': color,
        'count': count
    }
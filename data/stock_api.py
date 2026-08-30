# -*- coding: utf-8 -*-
"""
股票数据获取函数 - 通过 AkShare 获取股票行情、K线等数据
"""

import json
import re
from datetime import datetime, timedelta

from config import CACHE_TTL
from data.cache import cached, CACHE_QUOTE, CACHE_FUNDAMENTALS, CACHE_MONEYFLOW
from utils.common import safe_float_convert, request_with_retry, call_akshare_with_retry
import akshare as ak
import pandas as pd


def _get_market_name(stock_code):
    """获取所属市场名称"""
    if stock_code.startswith('6'):
        return "上海主板"
    elif stock_code.startswith('0'):
        return "深圳主板"
    elif stock_code.startswith('3'):
        return "创业板"
    elif stock_code.startswith('4'):
        return "北京交易所"
    elif stock_code.startswith('8') or stock_code.startswith('9'):
        return "科创板"
    return "未知"


# 缓存全市场行情数据，避免每次查询都重新下载
_akshare_spot_cache = None
_akshare_spot_cache_time = 0
_AKSHARE_SPOT_CACHE_TTL = 60  # 缓存60秒


def _get_akshare_spot_df():
    """获取全市场实时行情 DataFrame（带缓存）"""
    global _akshare_spot_cache, _akshare_spot_cache_time
    import time
    now = time.time()
    if _akshare_spot_cache is None or (now - _akshare_spot_cache_time) > _AKSHARE_SPOT_CACHE_TTL:
        df = call_akshare_with_retry(ak.stock_zh_a_spot_em)
        if df is not None and not df.empty:
            _akshare_spot_cache = df
            _akshare_spot_cache_time = now
        else:
            return None
    return _akshare_spot_cache


@cached(CACHE_QUOTE)
def get_stock_info(stock_code):
    """获取个股实时行情（通过 AkShare 东方财富行情）
    数据来源：AkShare（东方财富），实时缓存 60 秒
    """
    try:
        df = _get_akshare_spot_df()
        if df is None or df.empty:
            return None

        mask = df['代码'] == stock_code
        if not mask.any():
            return None

        row = df[mask].iloc[0]
        price = safe_float_convert(row.get('最新价', 0))
        open_price = safe_float_convert(row.get('今开', 0))
        prev_close = safe_float_convert(row.get('昨收', 0))
        high = safe_float_convert(row.get('最高', 0))
        low = safe_float_convert(row.get('最低', 0))
        change = safe_float_convert(row.get('涨跌额', 0))
        change_percent = safe_float_convert(row.get('涨跌幅', 0))
        volume = safe_float_convert(row.get('成交量', 0))  # 股
        amount = safe_float_convert(row.get('成交额', 0))   # 元
        turnover_rate = safe_float_convert(row.get('换手率', 0))
        amplitude = safe_float_convert(row.get('振幅', 0))
        pe = safe_float_convert(row.get('市盈率-动态', 0))
        pb = safe_float_convert(row.get('市净率', 0))
        total_mv = safe_float_convert(row.get('总市值', 0))  # 万元
        circ_mv = safe_float_convert(row.get('流通市值', 0))  # 万元

        # 成交量从股转为手
        volume_shou = volume / 100 if volume else 0

        result = {
            'name': str(row.get('名称', '')),
            'code': stock_code,
            'market': _get_market_name(stock_code),
            'price': price,
            'high': high,
            'low': low,
            'open': open_price,
            'prev_close': prev_close,
            'change': change,
            'change_percent': change_percent,
            'volume': volume_shou,
            'amount': amount,
            'turnover_rate': turnover_rate,
            'amplitude': amplitude,
            'pe': pe,
            'pb': pb,
            'market_cap': circ_mv * 10000,  # 转为元
            'total_market_cap': total_mv * 10000,
        }
        return result
    except Exception as e:
        print("获取股票 {} 行情失败：{}".format(stock_code, e))
        return None


@cached(CACHE_QUOTE)
def search_stock(keyword):
    """搜索股票（通过 AkShare 东方财富搜索），缓存 60 秒"""
    try:
        # 使用全市场行情数据做模糊匹配（本地搜索更可靠）
        df = _get_akshare_spot_df()
        if df is None or df.empty:
            return []

        results = []
        keyword_upper = str(keyword).strip().upper()
        for _, row in df.iterrows():
            code = str(row.get('代码', '')).strip()
            name = str(row.get('名称', '')).strip()
            if keyword_upper in code or keyword_upper in name.upper() or keyword_upper in name:
                results.append({
                    'code': code,
                    'name': name,
                    'type': '股票',
                })
            if len(results) >= 20:
                break
        return results
    except Exception as e:
        print("搜索股票失败：{}".format(e))
        return []


@cached(CACHE_QUOTE)
def get_stock_kline(stock_code, days=60, ktype='daily'):
    """获取股票K线数据（使用 AkShare 东方财富）
    ktype: 'daily'=日K, 'weekly'=周K, 'monthly'=月K
    数据来源：AkShare（东方财富），实时缓存 60 秒
    """
    period_map = {
        'daily': 'daily',
        'weekly': 'weekly',
        'monthly': 'monthly',
    }
    period = period_map.get(ktype, 'daily')
    try:
        df = call_akshare_with_retry(ak.stock_zh_a_hist, symbol=stock_code, period=period, adjust="qfq")
        if df is not None and not df.empty:
            # 取最近 days 条
            df = df.tail(days)
            dates = []
            opens = []
            highs = []
            lows = []
            closes = []
            volumes = []
            for _, row in df.iterrows():
                dates.append(str(row.get('日期', '')))
                opens.append(safe_float_convert(row.get('开盘', 0)))
                closes.append(safe_float_convert(row.get('收盘', 0)))
                highs.append(safe_float_convert(row.get('最高', 0)))
                lows.append(safe_float_convert(row.get('最低', 0)))
                volumes.append(safe_float_convert(row.get('成交量', 0)))
            return {
                'dates': dates,
                'opens': opens,
                'highs': highs,
                'lows': lows,
                'closes': closes,
                'volumes': volumes,
            }
    except Exception as e:
        print("获取股票 {} K线失败：{}".format(stock_code, e))
    return None


@cached(CACHE_MONEYFLOW)
def get_stock_moneyflow(stock_code):
    """获取个股资金流向（使用 AkShare），缓存 1 小时
    根据股票代码自动判断市场：6开头=沪市，其他=深市
    """
    try:
        # 判断市场：6开头为沪市，其他为深市
        market = 'sh' if stock_code and stock_code.startswith('6') else 'sz'
        df = call_akshare_with_retry(ak.stock_individual_fund_flow, stock=stock_code, market=market)
        if df is not None and not df.empty:
            latest = df.iloc[-1]
            return {
                'date': str(latest.get('日期', '')),
                'main_net': safe_float_convert(latest.get('主力净流入', 0)),
                'small_net': safe_float_convert(latest.get('小单净流入', 0)),
                'medium_net': safe_float_convert(latest.get('中单净流入', 0)),
                'big_net': safe_float_convert(latest.get('大单净流入', 0)),
                'main_net_percent': safe_float_convert(latest.get('主力净流入占比', 0)),
            }
    except Exception as e:
        print("获取 {} 资金流向失败：{}".format(stock_code, e))
    return None


@cached(CACHE_QUOTE)
def get_hot_stocks(market='all'):
    """获取热门股票排行榜（使用 AkShare 东方财富热门股票），缓存 60 秒"""
    try:
        df = call_akshare_with_retry(ak.stock_hot_rank_em)
        if df is not None and not df.empty:
            results = []
            for _, row in df.head(20).iterrows():
                results.append({
                    'code': str(row.get('代码', '')),
                    'name': str(row.get('名称', '')),
                    'hot_score': safe_float_convert(row.get('热度', 0)),
                    'price': safe_float_convert(row.get('最新价', 0)),
                    'change_percent': safe_float_convert(row.get('涨跌幅', 0)),
                })
            return results
    except Exception as e:
        print("获取热门股票失败：{}".format(e))
    return []


@cached(CACHE_QUOTE)
def get_stock_rank(field='涨跌幅', order='desc', count=30):
    """获取股票排行榜（使用 AkShare），缓存 60 秒"""
    try:
        df = call_akshare_with_retry(ak.stock_zh_a_spot_em)
        if df is not None and not df.empty:
            # 按指定字段排序
            if field in df.columns:
                df_sorted = df.sort_values(by=field, ascending=(order == 'asc'))
            else:
                df_sorted = df.sort_values(by='涨跌幅', ascending=False)

            results = []
            for _, row in df_sorted.head(count).iterrows():
                results.append({
                    'code': str(row.get('代码', '')),
                    'name': str(row.get('名称', '')),
                    'price': safe_float_convert(row.get('最新价', 0)),
                    'change_percent': safe_float_convert(row.get('涨跌幅', 0)),
                    'change_amount': safe_float_convert(row.get('涨跌额', 0)),
                    'volume': safe_float_convert(row.get('成交量', 0)),
                    'amount': safe_float_convert(row.get('成交额', 0)),
                    'turnover': safe_float_convert(row.get('换手率', 0)),
                    'pe': safe_float_convert(row.get('市盈率-动态', 0)),
                    'pb': safe_float_convert(row.get('市净率', 0)),
                    'total_mv': safe_float_convert(row.get('总市值', 0)),
                })
            return results
    except Exception as e:
        print("获取股票排行榜失败：{}".format(e))
    return []


def format_stock_info(stock_info):
    """格式化股票行情信息为可读文本"""
    if not stock_info:
        return "未获取到股票数据"

    name = stock_info.get('name', '')
    code = stock_info.get('code', '')
    price = stock_info.get('price', 0)
    change = stock_info.get('change', 0)
    change_percent = stock_info.get('change_percent', 0)
    high = stock_info.get('high', 0)
    low = stock_info.get('low', 0)
    open_price = stock_info.get('open', 0)
    prev_close = stock_info.get('prev_close', 0)
    volume = stock_info.get('volume', 0)
    amount = stock_info.get('amount', 0)

    sign = "+" if change >= 0 else ""
    emoji = "📈" if change >= 0 else "📉"

    result = "{} **{}（{}）**\n\n".format(emoji, name, code)
    result += "当前价格：**{}**\n".format(price)
    result += "涨跌额：{}{}\n".format(sign, change)
    result += "涨跌幅：{}{}%\n".format(sign, change_percent)
    result += "今开：{} | 昨收：{}\n".format(open_price, prev_close)
    result += "最高：{} | 最低：{}\n".format(high, low)
    result += "成交量：{:.2f}万手\n".format(volume / 10000) if volume >= 10000 else "成交量：{}手\n".format(volume)
    result += "成交额：{:.2f}亿\n".format(amount / 100000000) if amount >= 100000000 else "成交额：{:.2f}万\n".format(amount / 10000)

    return result
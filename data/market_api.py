# -*- coding: utf-8 -*-
"""
市场数据获取 - 大盘指数、市场情绪、估值等数据
使用 AkShare 稳定接口替代原始 HTTP 请求
"""

import akshare as ak
import pandas as pd
import time
import requests
import traceback
from datetime import datetime, timedelta

from config import CACHE_TTL
from data.cache import cached, CACHE_QUOTE, CACHE_VALUATION, CACHE_SECTOR
from utils.common import safe_float_convert, safe_get, safe_get_dict, call_akshare_with_retry, safe_request, fetch_with_timeout, is_safe_public_url


_INDEX_MAP = {
    '000001': '上证指数',
    '399001': '深证成指',
    '399006': '创业板指',
    '000688': '科创50',
    '000016': '上证50',
    '000300': '沪深300',
    '000905': '中证500',
}


# 行情缓存 5 分钟：TUN 代理拦截数据源时，过短 TTL 会让每次页面 rerun 都重新
# 等满超时（交互卡顿的主因）；指数/板块本就是低频变化数据
@cached(CACHE_QUOTE, cache_failures=True, failure_ttl=300)
def get_market_index():
    """获取大盘指数数据 - 使用 AkShare stock_zh_index_spot_em"""
    try:
        time.sleep(0.3)
        # 2026-09-03: 用 fetch_with_timeout 硬限 5s——东财弱网时 akshare 内部重试可达 8s+，
        # 到点立即降级切腾讯，不让整页等待（工具注释本就为此设计）
        df = fetch_with_timeout(ak.stock_zh_index_spot_em, timeout=5)
        if df is None or df.empty:
            # 东财 akshare 失败 → fallback（腾讯，评审修复：只在此分支才需要 fallback）
            return _get_market_index_tencent()

        result = []
        # 列名映射：代码、名称、最新价、涨跌额、涨跌幅
        for _, row in df.iterrows():
            code = str(safe_get(row, '代码', ''))
            name = safe_get(row, '名称', '')

            # 只保留主要指数
            matched = False
            for c in _INDEX_MAP:
                if c in code or _INDEX_MAP[c] in name:
                    matched = True
                    break

            if not matched:
                continue

            result.append({
                'name': name,
                'code': code,
                'price': safe_float_convert(safe_get(row, '最新价', 0)),
                'change': safe_float_convert(safe_get(row, '涨跌额', 0)),
                'change_percent': safe_float_convert(safe_get(row, '涨跌幅', 0)),
            })
        return result
    except Exception as e:
        print("AkShare获取大盘指数失败：{}".format(e))
        # 东财 akshare 失败 → 优先切腾讯（东财直连在弱网时同样不可达，直接跳过省一轮重试）
        return _get_market_index_tencent()


def _get_market_index_fallback():
    """备用方案：通过东方财富 push2 API 获取"""
    url = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    params = {
        "fltt": "2",
        "fields": "f2,f3,f4,f12,f14",
        "secids": "1.000001,0.399001,0.399006,1.000688,1.000016,1.000300,1.000905",
        "_": int(datetime.now().timestamp() * 1000)
    }
    headers = {
        "Referer": "https://quote.eastmoney.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    try:
        resp = safe_request(url, params=params, headers=headers, timeout=3, max_retries=1)
        if resp is None:
            return []
        data = resp.json()
        result = []
        for item in (data or {}).get('data', {}).get('diff', []):
            if item is None:
                continue
            result.append({
                'name': safe_get(item, 'f14', ''),
                'code': safe_get(item, 'f12', ''),
                'price': safe_float_convert(safe_get(item, 'f2', 0)),
                'change': safe_float_convert(safe_get(item, 'f4', 0)),
                'change_percent': safe_float_convert(safe_get(item, 'f3', 0)),
            })
        return result
    except Exception as e:
        print("备用大盘指数失败：{}".format(e))
        return []



def _get_market_index_tencent():
    """腾讯行情 fallback：东财不可达时切腾讯（2026-09-03 实测可用）
    返回与主函数同结构：[{name, code, price, change, change_percent}]
    走 safe_request（内置出网安全校验 + 2 次重试，与蛋卷估值同一通道）
    """
    url = "https://qt.gtimg.cn/q=s_sh000001,s_sz399001,s_sz399006,s_sh000300,s_sh000016,s_sh000905"
    try:
        resp = safe_request(url, headers={
            'Referer': 'https://weixin/',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        }, timeout=3, max_retries=1)
        if resp is None:
            return []
        body = resp.text
        result = []
        # 腾讯格式: v_s_sh000001="1~上证指数~000001~3942.09~0.70~..."
        # 字段: 1名字 2代码 3现价 4涨跌额(?) 5涨跌额 6涨跌幅... 按 ~ 分割
        for line in body.strip().split(';'):
            line = line.strip()
            if not line or '="' not in line:
                continue
            code_part = line.split('=')[0].replace('v_s_', '').strip()
            payload = line.split('="', 1)[1].rstrip('"')
            fields = payload.split('~')
            if len(fields) < 6:
                continue
            # 腾讯字段（实测 2026-09-03）：[0]未知 [1]名字 [2]代码 [3]当前价 [4]涨跌额 [5]涨跌幅%
            name = fields[1] if len(fields) > 1 else code_part
            price = safe_float_convert(fields[3], default=0) if len(fields) > 3 else 0
            change = safe_float_convert(fields[4], default=0) if len(fields) > 4 else 0
            change_pct = safe_float_convert(fields[5], default=0) if len(fields) > 5 else 0
            result.append({
                'name': name,
                'code': code_part,
                'price': price,
                'change': change,
                'change_percent': change_pct,
            })
        return result
    except Exception as e:
        print("腾讯获取大盘指数失败：{}".format(e))
        return []


def _get_index_valuation_from_danjuan(index_code):
    """
    从蛋卷基金（雪球）获取指数 PE/PB 及分位值
    index_code: 如 '000300'（沪深300）, '000016'（上证50）, '399006'（创业板指）, '000905'（中证500）
    返回 {'pe': float, 'pe_pct': float, 'pb': float, 'pb_pct': float, 'name': str}
    """
    url = "https://danjuanfunds.com/djapi/index_eva/dj?index_code={}".format(index_code)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://danjuanfunds.com/",
    }

    try:
        resp = safe_request(url, headers=headers, timeout=10)
        if resp is None:
            raise requests.RequestException("网络请求失败或不安全的请求地址：{}".format(url))
        data = resp.json()
        if data.get("data"):
            items = data["data"].get("items", [])
            # 从 items 列表中匹配目标指数（index_code 格式如 SH000300、SZ399006）
            for item in items:
                item_code = item.get("index_code", "")
                # 去掉前缀 SH/SZ/CS 等，匹配纯数字代码
                if item_code.endswith(index_code):
                    return {
                        "name": item.get("name", ""),
                        "pe": float(item.get("pe", 0)),
                        # 蛋卷返回的是 0-1 范围小数，转换为 0-100 百分制
                        "pe_pct": float(item.get("pe_percentile", 0)) * 100,
                        "pb": float(item.get("pb", 0)),
                        "pb_pct": float(item.get("pb_percentile", 0)) * 100,
                    }
    except Exception as e:
        print("蛋卷基金获取 {} 估值失败: {}".format(index_code, e))
        traceback.print_exc()

    return {"name": "", "pe": 0, "pe_pct": 0, "pb": 0, "pb_pct": 0}


@cached(CACHE_VALUATION)
def get_valuation_data():
    """获取指数估值数据 - 使用蛋卷基金（雪球）接口"""
    INDEX_MAP = {
        "000300": "沪深300",
        "000016": "上证50",
        "399006": "创业板指",
        "000905": "中证500",
    }

    result = []
    for code, name in INDEX_MAP.items():
        try:
            val = _get_index_valuation_from_danjuan(code)

            pe = val["pe"]
            pb = val["pb"]
            pe_pct = val["pe_pct"]
            pb_pct = val["pb_pct"]

            # 判断估值状态
            if pe_pct < 30:
                eva_type = "低估"
                eva_int = 0
            elif pe_pct < 70:
                eva_type = "正常"
                eva_int = 1
            else:
                eva_type = "高估"
                eva_int = 2

            result.append({
                "name": name,
                "code": code,
                "pe": round(pe, 2),
                "pe_percentile": round(pe_pct, 1),
                "pb": round(pb, 2),
                "pb_percentile": round(pb_pct, 1),
                "eva_type": eva_type,
                "eva_type_int": eva_int,
                "update_date": datetime.now().strftime("%Y-%m-%d"),
            })
            print("[DEBUG] {}({}) PE={:.2f}({:.1f}%) PB={:.2f}({:.1f}%) -> {}".format(name, code, pe, pe_pct, pb, pb_pct, eva_type))
        except Exception as e:
            print("获取 {}({}) 估值失败: {}".format(name, code, e))
            traceback.print_exc()

    if result:
        return result

    # 备用估值数据
    return _get_valuation_fallback()


def _get_valuation_fallback():
    """备用估值数据"""
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1", "pz": "50", "po": "1", "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2", "invt": "2", "fid": "f3",
        "fs": "m:90+t:2",
        "fields": "f12,f14,f2,f3,f4,f100,f111,f112,f115,f117,f120,f121,f122,f123,f124,f125,f126,f127,f128,f129,f130,f131",
        "_": int(datetime.now().timestamp() * 1000)
    }
    headers = {
        "Referer": "https://data.eastmoney.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    try:
        resp = call_akshare_with_retry(lambda: __import__('requests').get(url, params=params, headers=headers, timeout=10))
        if resp is None:
            return []
        data = resp.json()
        result = []
        for item in (data or {}).get('data', {}).get('diff', []):
            if item is None:
                continue
            pe = safe_float_convert(safe_get(item, 'f100'))
            pe_percentile = safe_float_convert(safe_get(item, 'f121'))
            pb = safe_float_convert(safe_get(item, 'f112'))
            pb_percentile = safe_float_convert(safe_get(item, 'f122'))
            eva_type = safe_get(item, 'f131', '')
            eva_type_int = 1 if eva_type == '低估值' else (2 if eva_type == '正常' else 3)

            result.append({
                'name': safe_get(item, 'f14', ''),
                'code': safe_get(item, 'f12', ''),
                'pe': pe,
                'pe_percentile': pe_percentile,
                'pb': pb,
                'pb_percentile': pb_percentile,
                'eva_type': eva_type,
                'eva_type_int': eva_type_int,
                'update_date': datetime.now().strftime('%Y-%m-%d'),
            })
        return result
    except Exception as e:
        print("备用估值数据失败：{}".format(e))
        return []


@cached(CACHE_SECTOR, cache_failures=True, failure_ttl=300)
def get_hot_sectors():
    """获取热门板块涨跌幅 - 使用 AkShare stock_board_industry_name_em"""
    try:
        time.sleep(0.3)
        df = call_akshare_with_retry(ak.stock_board_industry_name_em)
        if df is not None and not df.empty:
            # 按涨跌幅排序取前10
            sort_col = None
            for col in ['涨跌幅', '涨跌幅(%)']:
                if col in df.columns:
                    sort_col = col
                    break
            if sort_col:
                df = df.sort_values(by=sort_col, ascending=False)

            result = []
            for _, row in df.head(10).iterrows():
                result.append({
                    'name': safe_get(row, '板块名称', safe_get(row, '名称', '')),
                    'code': safe_get(row, '板块代码', safe_get(row, '代码', '')),
                    'change': safe_float_convert(safe_get(row, sort_col, safe_get(row, '涨跌幅', 0))),
                })
            return result
    except Exception as e:
        print("AkShare获取行业板块失败：{}".format(e))

    # 备用
    return _get_hot_sectors_fallback()


def _get_hot_sectors_fallback():
    """备用获取热门板块"""
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1", "pz": "10", "po": "1", "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2", "invt": "2", "fid": "f3",
        "fs": "m:90+t:3",
        "fields": "f12,f14,f2,f3,f4",
        "_": int(datetime.now().timestamp() * 1000)
    }
    headers = {
        "Referer": "https://data.eastmoney.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    try:
        resp = call_akshare_with_retry(lambda: __import__('requests').get(url, params=params, headers=headers, timeout=10))
        if resp is None:
            return []
        data = resp.json()
        result = []
        for item in (data or {}).get('data', {}).get('diff', []):
            if item is None:
                continue
            result.append({
                'name': safe_get(item, 'f14', ''),
                'code': safe_get(item, 'f12', ''),
                'change': safe_float_convert(safe_get(item, 'f3', 0)),
            })
        return result
    except Exception as e:
        print("备用热门板块失败：{}".format(e))
        return []


def calc_market_sentiment():
    """计算市场情绪温度计（基于估值水平）"""
    val_data = get_valuation_data()

    if not val_data:
        return None

    # 找沪深300和创业板指
    hs300 = None
    cyb = None
    for item in val_data:
        if item is None:
            continue
        name = safe_get(item, 'name', '')
        if '沪深300' in name:
            hs300 = item
        if '创业板' in name:
            cyb = item

    if not hs300 or not cyb:
        return None

    # 综合估值分（沪深300权重60%，创业板40%）
    val_score = safe_float_convert(safe_get(hs300, 'pe_percentile', 0)) * 0.6 + safe_float_convert(safe_get(cyb, 'pe_percentile', 0)) * 0.4

    # 情绪分数 0-100
    score = val_score

    # 情绪状态和建议
    if score < 20:
        status = "极度恐惧"
        color = "#51cf66"
        emoji = "\U0001f631"
        suggestion = "市场极度恐慌，黄金坑机会！可以加大定投金额（增加50%-100%），勇敢布局"
    elif score < 40:
        status = "恐惧"
        color = "#69db7c"
        emoji = "\U0001f628"
        suggestion = "市场偏冷，可以适当多买，定投金额增加20%-50%"
    elif score < 60:
        status = "中性"
        color = "#ffd43b"
        emoji = "\U0001f610"
        suggestion = "市场情绪正常，按原计划定投即可，不用加也不用减"
    elif score < 80:
        status = "贪婪"
        color = "#ffa94d"
        emoji = "\U0001f60f"
        suggestion = "市场偏热，可以减少定投金额，或者开始分批止盈"
    else:
        status = "极度贪婪"
        color = "#ff6b6b"
        emoji = "\U0001f911"
        suggestion = "市场极度疯狂，风险很大！建议停止定投，考虑逐步止盈"

    return {
        'score': round(score, 1),
        'status': status,
        'color': color,
        'emoji': emoji,
        'suggestion': suggestion,
        'hs300_pe': safe_get(hs300, 'pe_percentile', 0),
        'cyb_pe': safe_get(cyb, 'pe_percentile', 0),
        'update_date': safe_get(hs300, 'update_date', '')
    }
# -*- coding: utf-8 -*-
"""
市场情绪数据获取 - 涨跌停、连板高度、赚钱效应、成交量、北向资金、板块效应
"""

import requests
import json
import re
from datetime import datetime, timedelta

from config import CACHE_TTL
from data.cache import cached, CACHE_SENTIMENT
import utils.common
safe_float_convert = getattr(utils.common, "safe_float_convert", lambda x, default=0.0: default)
safe_get = getattr(utils.common, "safe_get", lambda d, k, default=None: default)
safe_get_dict = getattr(utils.common, "safe_get_dict", lambda d, *keys, default=None: default)
request_with_retry = getattr(utils.common, "request_with_retry", lambda url, **kwargs: None)


def safe_int_convert(value, default=0):
    """安全地将值转换为整数"""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _get_headers():
    """获取通用请求头"""
    return {
        "Referer": "https://data.eastmoney.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }


@cached(CACHE_SENTIMENT)
def get_limit_up_down_data():
    """获取涨跌停数据（涨停家数、跌停家数）
    使用东方财富涨停板API
    """
    # 涨停数据
    url_up = "https://push2.eastmoney.com/api/qt/clist/get"
    params_up = {
        "pn": "1",
        "pz": "500",
        "po": "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f3",
        "fs": "m:0+t:6+f:!2,m:0+t:80+f:!2,m:1+t:2+f:!2,m:1+t:23+f:!2",
        "fields": "f12,f14,f2,f3,f4,f8",
        "_": int(datetime.now().timestamp() * 1000)
    }
    headers = _get_headers()
    try:
        resp = request_with_retry(url_up, headers=headers, params=params_up, timeout=15)
        if resp is None:
            return None
        data = resp.json()
        items = safe_get_dict(data, 'data', 'diff', default=[])
        limit_up_count = 0
        limit_down_count = 0
        for item in items:
            if item is None:
                continue
            change = safe_float_convert(safe_get(item, 'f3'))
            if change >= 9.8:  # 涨停（涨幅>=9.8%）
                limit_up_count += 1
            elif change <= -9.8:  # 跌停（跌幅<=-9.8%）
                limit_down_count += 1
        return {
            'limit_up': limit_up_count,
            'limit_down': limit_down_count,
        }
    except Exception as e:
        print("获取涨跌停数据失败：{}".format(e))
        return None


@cached(CACHE_SENTIMENT)
def get_top_board_height():
    """获取最高连板高度"""
    try:
        import akshare as ak
        df = ak.stock_zt_pool_em(date=datetime.now().strftime('%Y%m%d'))
        if df.empty:
            return 0
        max_board = 0
        for _, row in df.iterrows():
            board_str = str(row.get('连板数', '0'))
            try:
                board_num = int(board_str.replace('板', '').strip())
            except:
                board_num = 0
            if board_num > max_board:
                max_board = board_num
        return max_board
    except Exception as e:
        print("获取连板高度失败：{}".format(e))
        return 0


@cached(CACHE_SENTIMENT)
def get_market_breadth():
    """获取市场涨跌家数（赚钱效应）"""
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
        if df.empty:
            return None
        up_count = int((df['涨跌幅'] > 0).sum())
        down_count = int((df['涨跌幅'] < 0).sum())
        total = len(df)
        if total == 0:
            return None
        up_ratio = round(up_count / total * 100, 1)
        return {
            'up_count': up_count,
            'down_count': down_count,
            'total': total,
            'up_ratio': up_ratio
        }
    except Exception as e:
        print("获取涨跌家数失败：{}".format(e))
        return None


@cached(CACHE_SENTIMENT)
def get_turnover_data():
    """获取两市成交额（单位：元）"""
    try:
        import akshare as ak
        df_sh = ak.stock_zh_index_daily_em(symbol='sh000001')
        df_sz = ak.stock_zh_index_daily_em(symbol='sz399001')
        if df_sh.empty or df_sz.empty:
            return 0
        sh_amount = df_sh.iloc[-1]['amount']
        sz_amount = df_sz.iloc[-1]['amount']
        return int(sh_amount + sz_amount)
    except Exception as e:
        print("获取成交额失败：{}".format(e))
        return 0


@cached(CACHE_SENTIMENT)
def get_north_flow_data():
    """获取北向资金数据
    使用东方财富资金流向API
    """
    url = "https://push2.eastmoney.com/api/qt/kamt.kline/get"
    params = {
        "klt": "1",
        "lmt": "1",
        "secid": "1.000001",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "_": int(datetime.now().timestamp() * 1000)
    }
    headers = {
        "Referer": "https://data.eastmoney.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    try:
        resp = request_with_retry(url, headers=headers, params=params, timeout=15)
        if resp is None:
            return 0
        data = resp.json()
        klines = safe_get_dict(data, 'data', 'klines', default=[])
        if klines and len(klines) > 0:
            # 格式: "2026-06-25,123.45,67.89,55.56"
            parts = klines[0].split(',')
            if len(parts) >= 4:
                # 北向资金净流入 = 沪股通净流入 + 深股通净流入
                sh_flow = safe_float_convert(parts[1])  # 沪股通
                sz_flow = safe_float_convert(parts[2])  # 深股通
                total_flow = sh_flow + sz_flow
                return total_flow
        return 0
    except Exception as e:
        print("获取北向资金数据失败：{}".format(e))
        return 0


@cached(CACHE_SENTIMENT)
def get_hot_sector_limit_up():
    """获取涨停最多的板块及涨停家数
    使用东方财富板块API
    """
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1",
        "pz": "10",
        "po": "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f3",
        "fs": "m:90+t:3",
        "fields": "f12,f14,f2,f3,f4,f20,f21,f8",
        "_": int(datetime.now().timestamp() * 1000)
    }
    headers = _get_headers()
    try:
        resp = request_with_retry(url, headers=headers, params=params, timeout=15)
        if resp is None:
            return None
        data = resp.json()
        items = safe_get_dict(data, 'data', 'diff', default=[])
        top_sector = None
        max_limit_up = 0
        for item in items:
            if item is None:
                continue
            # f8 可能包含涨停家数
            limit_up_count = safe_int_convert(safe_get(item, 'f8'))
            if limit_up_count > max_limit_up:
                max_limit_up = limit_up_count
                top_sector = {
                    'name': safe_get(item, 'f14', ''),
                    'code': safe_get(item, 'f12', ''),
                    'limit_up_count': limit_up_count,
                    'change': safe_float_convert(safe_get(item, 'f3')),
                }
        return top_sector
    except Exception as e:
        print("获取板块涨停数据失败：{}".format(e))
        return None


def calc_sentiment_score():
    """计算市场情绪温度总分（0-100分）
    6个维度：
    1. 涨跌停数量（20分）
    2. 连板高度（20分）
    3. 赚钱效应（20分）
    4. 成交量（15分）
    5. 北向资金（15分）
    6. 板块效应（10分）
    """
    result = {
        'total_score': 0,
        'dimensions': {},
        'market_overview': {},
        'suggestion': '',
        'stage': '',
        'stage_emoji': '',
        'color': '',
        'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }

    # 1. 涨跌停数量（20分）
    limit_data = get_limit_up_down_data()
    if limit_data:
        limit_up = limit_data.get('limit_up', 0)
        limit_down = limit_data.get('limit_down', 0)

        # 涨停得分（10分）
        if limit_up > 100:
            up_score = 10
        elif limit_up >= 50:
            up_score = 7
        elif limit_up >= 30:
            up_score = 4
        elif limit_up >= 10:
            up_score = 2
        else:
            up_score = 0

        # 跌停得分（10分）- 反向
        if limit_down < 5:
            down_score = 10
        elif limit_down <= 10:
            down_score = 7
        elif limit_down <= 20:
            down_score = 4
        elif limit_down <= 50:
            down_score = 2
        else:
            down_score = 0

        limit_score = up_score + down_score
        result['dimensions']['limit_up_down'] = {
            'name': '涨跌停数量',
            'score': limit_score,
            'max_score': 20,
            'detail': '涨停{}家（{}分），跌停{}家（{}分）'.format(limit_up, up_score, limit_down, down_score),
            'limit_up': limit_up,
            'limit_down': limit_down,
            'up_score': up_score,
            'down_score': down_score,
        }
    else:
        result['dimensions']['limit_up_down'] = {
            'name': '涨跌停数量',
            'score': 0,
            'max_score': 20,
            'detail': '数据缺失',
            'limit_up': None,
            'limit_down': None,
        }

    # 2. 连板高度（20分）
    board_height = get_top_board_height()
    if board_height > 0:
        if board_height > 7:
            board_score = 20
        elif board_height >= 5:
            board_score = 15
        elif board_height >= 3:
            board_score = 10
        elif board_height >= 2:
            board_score = 5
        else:
            board_score = 0
        result['dimensions']['board_height'] = {
            'name': '连板高度',
            'score': board_score,
            'max_score': 20,
            'detail': '最高{}连板（{}分）'.format(board_height, board_score),
            'board_height': board_height,
        }
    else:
        result['dimensions']['board_height'] = {
            'name': '连板高度',
            'score': 0,
            'max_score': 20,
            'detail': '数据缺失',
            'board_height': None,
        }

    # 3. 赚钱效应（20分）
    breadth = get_market_breadth()
    if breadth:
        up_ratio = breadth.get('up_ratio', 0)
        if up_ratio > 70:
            breadth_score = 20
        elif up_ratio >= 60:
            breadth_score = 15
        elif up_ratio >= 50:
            breadth_score = 10
        elif up_ratio >= 40:
            breadth_score = 5
        else:
            breadth_score = 0
        result['dimensions']['breadth'] = {
            'name': '赚钱效应',
            'score': breadth_score,
            'max_score': 20,
            'detail': '上涨{:.1f}%（{}家/{}家），{}分'.format(up_ratio, breadth.get('up_count', 0), breadth.get('total', 0), breadth_score),
            'up_count': breadth.get('up_count', 0),
            'down_count': breadth.get('down_count', 0),
            'up_ratio': up_ratio,
        }
        result['market_overview']['up_count'] = breadth.get('up_count', 0)
        result['market_overview']['down_count'] = breadth.get('down_count', 0)
    else:
        result['dimensions']['breadth'] = {
            'name': '赚钱效应',
            'score': 0,
            'max_score': 20,
            'detail': '数据缺失',
        }

    # 4. 成交量（15分）
    turnover = get_turnover_data()
    if turnover > 0:
        turnover_yi = turnover / 100000000  # 转换为亿
        if turnover_yi > 15000:
            turnover_score = 15
        elif turnover_yi >= 10000:
            turnover_score = 12
        elif turnover_yi >= 8000:
            turnover_score = 8
        elif turnover_yi >= 5000:
            turnover_score = 4
        else:
            turnover_score = 0
        result['dimensions']['turnover'] = {
            'name': '成交量',
            'score': turnover_score,
            'max_score': 15,
            'detail': '两市成交{:.0f}亿（{}分）'.format(turnover_yi, turnover_score),
            'turnover': turnover_yi,
        }
        result['market_overview']['turnover'] = turnover_yi
    else:
        result['dimensions']['turnover'] = {
            'name': '成交量',
            'score': 0,
            'max_score': 15,
            'detail': '数据缺失',
        }

    # 5. 北向资金（15分）
    north_flow = get_north_flow_data()
    if north_flow != 0:
        if north_flow > 100:
            north_score = 15
        elif north_flow >= 50:
            north_score = 12
        elif north_flow >= 0:
            north_score = 8
        elif north_flow >= -50:
            north_score = 4
        else:
            north_score = 0
        result['dimensions']['north_flow'] = {
            'name': '北向资金',
            'score': north_score,
            'max_score': 15,
            'detail': '净流入{:.2f}亿（{}分）'.format(north_flow, north_score),
            'north_flow': round(north_flow, 2),
        }
        result['market_overview']['north_flow'] = round(north_flow, 2)
    else:
        result['dimensions']['north_flow'] = {
            'name': '北向资金',
            'score': 0,
            'max_score': 15,
            'detail': '数据缺失',
        }

    # 6. 板块效应（10分）
    hot_sector = get_hot_sector_limit_up()
    if hot_sector:
        sector_limit_up = hot_sector.get('limit_up_count', 0)
        if sector_limit_up > 10:
            sector_score = 10
        elif sector_limit_up >= 5:
            sector_score = 7
        elif sector_limit_up >= 3:
            sector_score = 4
        elif sector_limit_up >= 1:
            sector_score = 2
        else:
            sector_score = 0
        result['dimensions']['sector'] = {
            'name': '板块效应',
            'score': sector_score,
            'max_score': 10,
            'detail': '{}板块{}只涨停（{}分）'.format(hot_sector.get('name', ''), sector_limit_up, sector_score),
            'sector_name': hot_sector.get('name', ''),
            'sector_limit_up': sector_limit_up,
            'sector_change': hot_sector.get('change', 0),
        }
        result['market_overview']['hot_sector'] = hot_sector.get('name', '')
    else:
        result['dimensions']['sector'] = {
            'name': '板块效应',
            'score': 0,
            'max_score': 10,
            'detail': '数据缺失',
        }

    # 计算总分
    total = sum(d.get('score', 0) for d in result['dimensions'].values())
    result['total_score'] = total

    # 判断情绪阶段
    if total <= 20:
        result['stage'] = '冰点期'
        result['stage_emoji'] = '❄️'
        result['color'] = '#51cf66'  # 绿色（冷）
        result['suggestion'] = '轻仓试错，观望为主。市场情绪极度低迷，不宜重仓操作，可小仓位试探性布局。'
        result['position'] = '0-20%'
        result['risk'] = '市场可能继续下探，注意控制仓位'
    elif total <= 40:
        result['stage'] = '启动期'
        result['stage_emoji'] = '🌱'
        result['color'] = '#69db7c'
        result['suggestion'] = '逐步加仓，关注龙头。市场情绪开始回暖，可逐步增加仓位，关注率先走强的龙头板块。'
        result['position'] = '20-40%'
        result['risk'] = '反弹可能夭折，不宜追高'
    elif total <= 60:
        result['stage'] = '发酵期'
        result['stage_emoji'] = '🔥'
        result['color'] = '#ffd43b'  # 黄色
        result['suggestion'] = '积极参与，持有为主。市场情绪活跃，赚钱效应明显，可积极参与主流热点。'
        result['position'] = '40-60%'
        result['risk'] = '注意板块轮动节奏，避免追涨杀跌'
    elif total <= 80:
        result['stage'] = '高潮期'
        result['stage_emoji'] = '🚀'
        result['color'] = '#ffa94d'  # 橙色
        result['suggestion'] = '减仓止盈，注意风险。市场情绪亢奋，短期可能见顶，建议逐步减仓锁定利润。'
        result['position'] = '20-40%'
        result['risk'] = '高潮过后往往是退潮，注意风险控制'
    else:
        result['stage'] = '退潮期'
        result['stage_emoji'] = '⚠️'
        result['color'] = '#ff6b6b'  # 红色
        result['suggestion'] = '空仓观望，控制回撤。市场情绪过热，风险积聚，建议空仓或极轻仓等待风险释放。'
        result['position'] = '0-10%'
        result['risk'] = '退潮期亏钱效应明显，管住手最重要'

    return result
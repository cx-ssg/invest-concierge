# -*- coding: utf-8 -*-
"""
涨停板复盘数据获取 - 使用 AkShare 优先获取涨停板数据
备用：东方财富 HTTP 接口
"""

import time
from datetime import datetime

import akshare as ak

from data.cache import cached, CACHE_LIMIT_UP
import utils.common
safe_float_convert = getattr(utils.common, "safe_float_convert", lambda x, default=0.0: default)
safe_get = getattr(utils.common, "safe_get", lambda d, k, default=None: default)
request_with_retry = getattr(utils.common, "request_with_retry", lambda url, **kwargs: None)
call_akshare_with_retry = getattr(utils.common, "call_akshare_with_retry", lambda func, **kwargs: None)


def safe_int_convert(val, default=0):
    """安全转换整数"""
    if val is None:
        return default
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return default


def _get_headers():
    """获取通用请求头"""
    return {
        "Referer": "https://quote.eastmoney.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }


def _parse_date_str():
    """生成当天的日期字符串 YYYYMMDD"""
    return datetime.now().strftime("%Y%m%d")


def get_limit_up_stocks():
    """获取今日涨停股列表（含跌停股）
    
    优先使用 AkShare stock_zt_pool_em，备用东方财富 API
    
    返回：{
        'limit_up': [涨停股列表],
        'limit_down': [跌停股列表],
        'update_time': '更新时间',
    }
    每只股票包含：code, name, price, change, turnover, amount, market_cap, circulating_cap
    """
    today_str = _parse_date_str()
    
    # 方案1：AkShare stock_zt_pool_em
    try:
        time.sleep(0.3)
        df = call_akshare_with_retry(ak.stock_zt_pool_em, date=today_str)
        if df is not None and not df.empty:
            return _parse_zt_pool_df(df)
    except Exception as e:
        print("AkShare stock_zt_pool_em 失败：{}".format(e))
    
    # 方案2：备用 HTTP API
    return _get_limit_up_stocks_fallback()


def _parse_zt_pool_df(df):
    """解析 AkShare stock_zt_pool_em 返回的 DataFrame"""
    limit_up = []
    limit_down = []
    
    for _, row in df.iterrows():
        change = safe_float_convert(safe_get(row, 'pct_chg'), 0)
        code = str(safe_get(row, '代码', ''))
        name = str(safe_get(row, '名称', ''))
        
        stock = {
            'code': code,
            'name': name,
            'price': safe_float_convert(safe_get(row, '最新价', 0)),
            'change': change,
            'change_amount': safe_float_convert(safe_get(row, '涨跌额', 0)),
            'volume': safe_float_convert(safe_get(row, '成交量', 0)),
            'amount': safe_float_convert(safe_get(row, '成交额', 0)),
            'turnover': safe_float_convert(safe_get(row, '换手率', 0)),
            'high': safe_float_convert(safe_get(row, '最高价', 0)),
            'low': safe_float_convert(safe_get(row, '最低价', 0)),
            'open': safe_float_convert(safe_get(row, '开盘价', 0)),
            'pre_close': 0,
            'market_cap': safe_float_convert(safe_get(row, '总市值', 0)),
            'circulating_cap': safe_float_convert(safe_get(row, '流通市值', 0)),
        }
        
        if change >= 9.8:
            limit_up.append(stock)
        elif change <= -9.8:
            limit_down.append(stock)
    
    return {
        'limit_up': limit_up,
        'limit_down': limit_down,
        'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


def _get_limit_up_stocks_fallback():
    """备用：通过东方财富行情API获取涨停/跌停股票数据"""
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1", "pz": "500", "po": "1", "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2", "invt": "2", "fid": "f3",
        "fs": "m:0+t:6+f:!2,m:0+t:80+f:!2,m:1+t:2+f:!2,m:1+t:23+f:!2",
        "fields": "f12,f14,f2,f3,f4,f5,f6,f7,f8,f9,f10,f15,f16,f17,f18,f20,f21",
        "_": int(datetime.now().timestamp() * 1000)
    }
    headers = _get_headers()
    try:
        resp = request_with_retry(url, params=params, headers=headers, timeout=10)
        if resp is None:
            return None
        data = resp.json()
        items = (data or {}).get('data', {}).get('diff', [])
        
        limit_up = []
        limit_down = []
        
        for item in items:
            change = safe_float_convert(item.get('f3'))
            code = str(item.get('f12', ''))
            name = str(item.get('f14', ''))
            
            stock = {
                'code': code,
                'name': name,
                'price': safe_float_convert(item.get('f2')),
                'change': change,
                'change_amount': safe_float_convert(item.get('f4')),
                'volume': safe_float_convert(item.get('f5')),
                'amount': safe_float_convert(item.get('f6')),
                'turnover': safe_float_convert(item.get('f7')),
                'high': safe_float_convert(item.get('f15')),
                'low': safe_float_convert(item.get('f16')),
                'open': safe_float_convert(item.get('f17')),
                'pre_close': safe_float_convert(item.get('f18')),
                'market_cap': safe_float_convert(item.get('f20')),
                'circulating_cap': safe_float_convert(item.get('f21')),
            }
            
            if change >= 9.8:
                limit_up.append(stock)
            elif change <= -9.8:
                limit_down.append(stock)
        
        return {
            'limit_up': limit_up,
            'limit_down': limit_down,
            'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
    except Exception as e:
        print("获取涨停/跌停股数据失败：{}".format(e))
        return None


def get_stock_board_name_by_stock(stock_code):
    """获取股票所属板块（行业板块+概念板块）"""
    boards = []
    
    # 通过东方财富接口获取
    url = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
    params = {
        "reportName": "RPT_BOARD_STOCK_BOARD",
        "columns": "BOARD_NAME,BOARD_CODE",
        "filter": '(STOCK_CODE="' + stock_code + '")',
        "pageNumber": 1,
        "pageSize": 20,
        "sortTypes": -1,
        "sortColumns": "",
        "source": "WEB",
        "client": "WEB",
        "_": int(datetime.now().timestamp() * 1000)
    }
    headers = _get_headers()
    try:
        resp = request_with_retry(url, params=params, headers=headers, timeout=10)
        if resp is None:
            return boards
        data = resp.json()
        items = (data or {}).get('result', {}).get('data', [])
        for item in items:
            board_name = item.get('BOARD_NAME', '')
            if board_name:
                boards.append(board_name)
    except Exception as e:
        print("获取股票所属板块失败（{}）：{}".format(stock_code, e))
    
    return boards


def get_limit_up_detail(code):
    """获取单只股票的涨停详情（封板时间、封单等）"""
    if code.startswith('6') or code.startswith('9'):
        secid = "1." + code
    elif code.startswith('0') or code.startswith('3') or code.startswith('2'):
        secid = "0." + code
    else:
        secid = "0." + code

    url = "https://push2.eastmoney.com/api/qt/stock/get"
    params = {
        "secid": secid,
        "fltt": "2",
        "invt": "2",
        "fields": "f43,f44,f45,f46,f47,f48,f49,f50,f51,f52,f55,f57,f58,f60,f84,f85,f86,f87,f116,f117,f167,f168,f169,f170,f171",
        "_": int(datetime.now().timestamp() * 1000)
    }
    headers = _get_headers()
    try:
        resp = request_with_retry(url, params=params, headers=headers, timeout=10)
        if resp is None:
            return None
        data = resp.json()
        detail = (data or {}).get('data', {})
        if detail:
            return {
                'code': code,
                'price': safe_float_convert(detail.get('f43')),
                'high': safe_float_convert(detail.get('f44')),
                'low': safe_float_convert(detail.get('f45')),
                'open': safe_float_convert(detail.get('f46')),
                'volume': safe_float_convert(detail.get('f47')),
                'amount': safe_float_convert(detail.get('f48')),
                'turnover': safe_float_convert(detail.get('f49')),
                'change': safe_float_convert(detail.get('f170')),
                'change_amount': safe_float_convert(detail.get('f171')),
                'amplitude': safe_float_convert(detail.get('f55')),
                'circulating_cap': safe_float_convert(detail.get('f116')),
                'total_cap': safe_float_convert(detail.get('f117')),
            }
        return None
    except Exception as e:
        print("获取个股详情失败（{}）：{}".format(code, e))
        return None


@cached(CACHE_LIMIT_UP)
def get_limit_up_review_data():
    """获取涨停板复盘全部数据
    
    返回：{
        'overview': {涨停概览},
        'ladder': [连板天梯],
        'board_distribution': [板块分布],
        'reason_category': [涨停原因分类],
        'first_board': [首板涨停列表],
        'update_time': '更新时间',
    }
    """
    # 1. 获取涨停/跌停股
    raw_data = get_limit_up_stocks()
    if not raw_data:
        return None
    
    limit_up = raw_data['limit_up']
    limit_down = raw_data['limit_down']
    update_time = raw_data['update_time']
    
    # 2. 获取每只涨停股的板块信息
    stock_boards = {}
    for stock in limit_up:
        code = stock.get('code', '')
        boards = get_stock_board_name_by_stock(code)
        stock_boards[code] = boards if boards else ['其他']
        time.sleep(0.05)  # 避免请求过快
    
    # 3. 统计板块分布
    board_count = {}
    board_stocks = {}
    for stock in limit_up:
        code = stock.get('code', '')
        boards = stock_boards.get(code, ['其他'])
        primary_board = boards[0] if boards else '其他'
        
        if primary_board not in board_count:
            board_count[primary_board] = 0
            board_stocks[primary_board] = []
        board_count[primary_board] += 1
        board_stocks[primary_board].append(stock)
    
    # 4. 估算连板数（简化版）
    for stock in limit_up:
        change = stock.get('change', 0)
        turnover = stock.get('turnover', 0)
        # 换手率越低且涨幅越高，连板可能性越大
        if change >= 10 and turnover < 2:
            stock['board_count'] = 5  # 5板以上
        elif change >= 10 and turnover < 5:
            stock['board_count'] = 3  # 3-4板
        elif change >= 10 and turnover < 10:
            stock['board_count'] = 2  # 2板
        else:
            stock['board_count'] = 1  # 首板
    
    # 5. 构建连板天梯
    ladder = []
    for stock in limit_up:
        code = stock.get('code', '')
        boards = stock_boards.get(code, ['其他'])
        primary_board = boards[0] if boards else '其他'
        
        ladder.append({
            'code': code,
            'name': stock.get('name', ''),
            'board_count': stock.get('board_count', 1),
            'board_name': primary_board,
            'change': stock.get('change', 0),
            'turnover': stock.get('turnover', 0),
            'seal_time': _estimate_seal_time(stock),
        })
    
    # 按连板数降序排列
    ladder.sort(key=lambda x: x['board_count'], reverse=True)
    
    # 6. 构建板块分布
    board_distribution = []
    for board_name, count in sorted(board_count.items(), key=lambda x: x[1], reverse=True):
        stocks_in_board = board_stocks.get(board_name, [])
        lead_stock = None
        max_board = 0
        for s in stocks_in_board:
            bc = s.get('board_count', 1)
            if bc > max_board:
                max_board = bc
                lead_stock = s
        
        board_distribution.append({
            'name': board_name,
            'limit_up_count': count,
            'lead_stock_name': lead_stock.get('name', '') if lead_stock else '',
            'lead_stock_code': lead_stock.get('code', '') if lead_stock else '',
            'lead_board_count': max_board,
        })
    
    board_distribution.sort(key=lambda x: x['limit_up_count'], reverse=True)
    
    # 7. 涨停原因分类
    reason_category = _categorize_by_reason(board_distribution, limit_up, stock_boards)
    
    # 8. 首板涨停列表
    first_board = [s for s in limit_up if s.get('board_count', 1) == 1]
    first_board.sort(key=lambda x: x.get('turnover', 100))
    
    for stock in first_board:
        code = stock.get('code', '')
        boards = stock_boards.get(code, ['其他'])
        stock['primary_board'] = boards[0] if boards else '其他'
        stock['all_boards'] = boards
        stock['seal_time'] = _estimate_seal_time(stock)
        stock['reason'] = _guess_limit_up_reason(stock, boards, board_distribution)
    
    overview = _calc_overview(limit_up, limit_down, ladder)
    
    return {
        'overview': overview,
        'ladder': ladder,
        'board_distribution': board_distribution,
        'reason_category': reason_category,
        'first_board': first_board,
        'update_time': update_time,
    }


def _estimate_seal_time(stock):
    """估算封板时间（基于换手率）"""
    turnover = stock.get('turnover', 100)
    if turnover < 1:
        return '09:30前（开盘秒板）'
    elif turnover < 3:
        return '09:30-10:00（早盘板）'
    elif turnover < 8:
        return '10:00-11:30（上午板）'
    elif turnover < 15:
        return '13:00-14:00（午后板）'
    else:
        return '14:00后（尾盘板）'


def _calc_overview(limit_up, limit_down, ladder):
    """计算涨停概览数据"""
    up_count = len(limit_up)
    down_count = len(limit_down)
    zha_ban = len([s for s in limit_up if s.get('turnover', 0) > 20])
    total_attempt = up_count + zha_ban
    seal_rate = (up_count / total_attempt * 100) if total_attempt > 0 else 0
    max_board = max([s.get('board_count', 1) for s in limit_up]) if limit_up else 0
    avg_turnover = sum([s.get('turnover', 0) for s in limit_up]) / up_count if up_count > 0 else 0
    
    return {
        'up_count': up_count,
        'down_count': down_count,
        'seal_rate': seal_rate,
        'max_board': max_board,
        'avg_turnover': avg_turnover,
        'zha_ban_count': zha_ban,
    }


def _categorize_by_reason(board_distribution, limit_up, stock_boards):
    """按涨停原因分类（基于板块名称关键词匹配）"""
    reason_keywords = {
        '业绩增长': ['业绩', '增长', '盈利', '预增', '季报', '年报'],
        '资产重组': ['重组', '并购', '借壳', '注入', '收购', '股权转让'],
        '题材炒作': ['概念', '题材', 'AI', '人工智能', '芯片', '半导体', '新能源',
                     '光伏', '锂电', '储能', '氢能', '元宇宙', '数字经济', '信创'],
        '次新股': ['次新', '新股'],
        '政策利好': ['政策', '利好', '补贴', '减税', '降准', '降息', '改革'],
        '涨价概念': ['涨价', '涨价', '化工', '有色', '钢铁', '煤炭', '石油'],
        '大消费': ['消费', '食品', '饮料', '白酒', '医药', '医疗', '家电', '汽车'],
        '基建地产': ['基建', '地产', '建筑', '建材', '工程', '水利', '交通'],
        '金融': ['银行', '证券', '保险', '金融', '信托'],
        '其他': [],
    }
    
    categories = {}
    for key in reason_keywords:
        categories[key] = {'count': 0, 'stocks': []}
    
    for stock in limit_up:
        code = stock.get('code', '')
        boards = stock_boards.get(code, ['其他'])
        board_text = ' '.join(boards)
        
        categorized = False
        for reason, keywords in reason_keywords.items():
            if reason == '其他':
                continue
            for kw in keywords:
                if kw in board_text:
                    categories[reason]['count'] += 1
                    categories[reason]['stocks'].append(stock.get('name', ''))
                    categorized = True
                    break
            if categorized:
                break
        
        if not categorized:
            categories['其他']['count'] += 1
            categories['其他']['stocks'].append(stock.get('name', ''))
    
    result = []
    for reason, data in sorted(categories.items(), key=lambda x: x[1]['count'], reverse=True):
        if data['count'] > 0:
            result.append({
                'reason': reason,
                'count': data['count'],
                'stocks': data['stocks'][:5],
            })
    
    return result


def _guess_limit_up_reason(stock, boards, board_distribution):
    """猜测个股涨停原因"""
    board_text = ' '.join(boards)
    
    reason_map = [
        ('业绩', '业绩增长'), ('重组', '资产重组'), ('并购', '资产重组'),
        ('AI', 'AI/人工智能概念'), ('人工智能', 'AI/人工智能概念'),
        ('芯片', '芯片/半导体概念'), ('半导体', '芯片/半导体概念'),
        ('新能源', '新能源概念'), ('光伏', '光伏概念'),
        ('锂电', '锂电池概念'), ('储能', '储能概念'),
        ('次新', '次新股'), ('政策', '政策利好'),
        ('消费', '大消费'), ('医药', '医药概念'),
        ('地产', '地产概念'), ('基建', '基建概念'),
        ('金融', '金融概念'),
    ]
    
    for kw, reason in reason_map:
        if kw in board_text:
            return reason
    
    if board_distribution:
        top_board = board_distribution[0]
        if boards and top_board['name'] in boards:
            return '热点板块（{}）'.format(top_board['name'])
    
    return '题材炒作'
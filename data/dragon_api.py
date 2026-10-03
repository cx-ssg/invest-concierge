# -*- coding: utf-8 -*-
"""
龙头股识别器 - 基于龙头战法，自动识别各板块龙头股
使用 AkShare 获取涨停股数据，分析板块效应和龙头评分
"""

import time
from datetime import datetime

import akshare as ak

from data.cache import cached, CACHE_DRAGON
from utils.common import safe_float_convert, request_with_retry, call_akshare_with_retry

# ==================== 工具函数 ====================


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


# ==================== 数据获取 ====================

def get_limit_up_stocks():
    """获取今日涨停股列表
    使用 AkShare stock_zt_pool_em 优先，备用东方财富行情API
    返回：涨停股列表，每只包含代码、名称、涨幅、涨停时间、封单金额、换手率等
    """
    # 方案1：AkShare stock_zt_pool_em
    try:
        today_str = datetime.now().strftime("%Y%m%d")
        time.sleep(0.3)
        df = call_akshare_with_retry(ak.stock_zt_pool_em, date=today_str)
        if df is not None and not df.empty:
            stocks = []
            # stock_zt_pool_em 列名: 序号, 代码, 名称, 涨跌幅, 最新价, 成交额, 流通市值, 总市值, 换手率, 封板资金, 首次封板时间, 最后封板时间, 炸板次数, 涨停统计, 连板数, 所属行业
            for _, row in df.iterrows():
                change = safe_float_convert(row.get('涨跌幅', 0))
                if change >= 9.8:
                    stock = {
                        'code': str(row.get('代码', '')),
                        'name': str(row.get('名称', '')),
                        'price': safe_float_convert(row.get('最新价', 0)),
                        'change': change,
                        'change_amount': safe_float_convert(row.get('涨跌幅', 0)) * safe_float_convert(row.get('最新价', 0)) / 100,
                        'volume': 0,
                        'amount': safe_float_convert(row.get('成交额', 0)),
                        'turnover': safe_float_convert(row.get('换手率', 0)),
                        'high': 0,
                        'low': 0,
                        'open': 0,
                        'pre_close': safe_float_convert(row.get('最新价', 0)) / (1 + safe_float_convert(row.get('涨跌幅', 0)) / 100) if change != -100 else 0,
                        'market_cap': safe_float_convert(row.get('总市值', 0)),
                        'circulating_cap': safe_float_convert(row.get('流通市值', 0)),
                        'board_count': int(row.get('连板数', 1)) if row.get('连板数', 1) != '1' else 1,
                    }
                    stocks.append(stock)
            if stocks:
                return stocks
    except Exception as e:
        print("AkShare stock_zt_pool_em 获取涨停股失败：{}".format(e))

    # 方案2：备用 HTTP API
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1",
        "pz": "200",
        "po": "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f3",
        "fs": "m:0+t:6+f:!2,m:0+t:80+f:!2,m:1+t:2+f:!2,m:1+t:23+f:!2",
        "fields": "f12,f14,f2,f3,f4,f5,f6,f7,f8,f9,f10,f15,f16,f17,f18,f20,f21",
        "_": int(datetime.now().timestamp() * 1000)
    }
    headers = _get_headers()
    try:
        resp = request_with_retry(url, params=params, headers=headers, timeout=10)
        if resp is None:
            return []
        data = resp.json()
        items = (data or {}).get('data', {}).get('diff', [])
        stocks = []
        for item in items:
            change = safe_float_convert(item.get('f3'))
            if change >= 9.8:  # 涨停股
                stock = {
                    'code': str(item.get('f12', '')),
                    'name': str(item.get('f14', '')),
                    'price': safe_float_convert(item.get('f2')),
                    'change': change,
                    'change_amount': safe_float_convert(item.get('f4')),
                    'volume': safe_float_convert(item.get('f5')),  # 成交量（手）
                    'amount': safe_float_convert(item.get('f6')),  # 成交额（元）
                    'turnover': safe_float_convert(item.get('f7')),  # 换手率%
                    'high': safe_float_convert(item.get('f15')),  # 最高
                    'low': safe_float_convert(item.get('f16')),  # 最低
                    'open': safe_float_convert(item.get('f17')),  # 开盘
                    'pre_close': safe_float_convert(item.get('f18')),  # 昨收
                    'market_cap': safe_float_convert(item.get('f20')),  # 总市值
                    'circulating_cap': safe_float_convert(item.get('f21')),  # 流通市值
                }
                stocks.append(stock)
        return stocks
    except Exception as e:
        print("获取涨停股数据失败：{}".format(e))
        return []


def get_lhb_stats():
    """获取龙虎榜统计
    使用 AkShare stock_lhb_stock_statistic_em 获取龙虎榜上榜统计
    返回：DataFrame 或空列表
    """
    try:
        time.sleep(0.3)
        df = call_akshare_with_retry(ak.stock_lhb_stock_statistic_em)
        if df is not None and not df.empty:
            return df
    except Exception as e:
        print("AkShare stock_lhb_stock_statistic_em 失败：{}".format(e))
    return []


def get_stock_board_info():
    """获取板块信息（涨停股所属板块）
    使用东方财富板块API
    返回：板块字典 {股票代码: [板块名]}
    """
    # 获取行业板块列表
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1",
        "pz": "500",
        "po": "0",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f3",
        "fs": "m:90+t:2",
        "fields": "f12,f14",
        "_": int(datetime.now().timestamp() * 1000)
    }
    headers = _get_headers()
    try:
        resp = request_with_retry(url, params=params, headers=headers, timeout=10)
        if resp is None:
            return []
        data = resp.json()
        items = (data or {}).get('data', {}).get('diff', [])
        boards = []
        for item in items:
            boards.append({
                'code': str(item.get('f12', '')),
                'name': str(item.get('f14', ''))
            })
        return boards
    except Exception as e:
        print("获取板块列表失败：{}".format(e))
        return []


def get_stock_board_members(board_code):
    """获取某个板块的成分股"""
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1",
        "pz": "500",
        "po": "0",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f3",
        "fs": "b:" + board_code + "+f:!50",
        "fields": "f12,f14",
        "_": int(datetime.now().timestamp() * 1000)
    }
    headers = _get_headers()
    try:
        resp = request_with_retry(url, params=params, headers=headers, timeout=10)
        if resp is None:
            return []
        data = resp.json()
        items = (data or {}).get('data', {}).get('diff', [])
        return [str(item.get('f12', '')) for item in items]
    except Exception as e:
        print("获取板块成分股失败：{}".format(e))
        return []


def get_stock_board_name_by_stock(stock_code):
    """通过东方财富概念板块接口获取股票所属板块"""
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
            return []
        data = resp.json()
        items = (data or {}).get('result', {}).get('data', [])
        boards = []
        for item in items:
            board_name = item.get('BOARD_NAME', '')
            if board_name:
                boards.append(board_name)
        return boards
    except Exception as e:
        print("获取股票所属板块失败（{}）：{}".format(stock_code, e))
        return []


def get_stock_concept_boards(stock_code):
    """获取股票所属概念板块（简化版，通过东方财富接口）"""
    # 使用东方财富概念板块接口
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1",
        "pz": "500",
        "po": "0",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f3",
        "fs": "m:90+t:3",
        "fields": "f12,f14",
        "_": int(datetime.now().timestamp() * 1000)
    }
    headers = _get_headers()
    try:
        resp = request_with_retry(url, params=params, headers=headers, timeout=10)
        if resp is None:
            return []
        data = resp.json()
        items = (data or {}).get('data', {}).get('diff', [])
        # 简化处理：返回所有概念板块名
        return [str(item.get('f14', '')) for item in items[:50]]
    except Exception as e:
        print("获取概念板块失败：{}".format(e))
        return []


def get_limit_up_detail(code):
    """获取单只股票的涨停详情（封板时间、封单等）
    通过东方财富个股行情接口获取
    """
    # 判断市场
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


# ==================== 龙头识别核心逻辑 ====================

def calc_board_score(stock, board_limit_up_count):
    """计算单只股票的龙头评分（6个维度，总分100分）

    参数：
        stock: 股票数据字典
        board_limit_up_count: 同板块涨停数量

    返回：
        dict: 各维度得分和总分
    """
    scores = {}
    total = 0

    # 1. 涨幅排名（25分）
    change = stock.get('change', 0)
    # 涨停股涨幅都在9.8%以上，按涨幅高低给分
    if change >= 10.0:
        scores['涨幅排名'] = 25
    elif change >= 9.98:
        scores['涨幅排名'] = 20
    elif change >= 9.95:
        scores['涨幅排名'] = 15
    elif change >= 9.9:
        scores['涨幅排名'] = 10
    else:
        scores['涨幅排名'] = 5
    total += scores['涨幅排名']

    # 2. 涨停时间（20分）- 通过换手率和涨幅估算封板时间
    # 换手率低且涨幅大 → 可能早盘封板
    turnover = stock.get('turnover', 0)
    if turnover < 3 and change >= 10:
        scores['涨停时间'] = 20  # 开盘30分钟内封板
        scores['涨停时间_desc'] = '开盘快速封板'
    elif turnover < 8 and change >= 10:
        scores['涨停时间'] = 15  # 上午封板
        scores['涨停时间_desc'] = '上午封板'
    elif turnover < 15 and change >= 10:
        scores['涨停时间'] = 10  # 下午封板
        scores['涨停时间_desc'] = '下午封板'
    elif turnover < 25 and change >= 10:
        scores['涨停时间'] = 5  # 尾盘封板
        scores['涨停时间_desc'] = '尾盘封板'
    else:
        scores['涨停时间'] = 0
        scores['涨停时间_desc'] = '未涨停'
    total += scores['涨停时间']

    # 3. 封单强度（15分）
    # 封单金额 = 成交额 * 封单比例（估算）
    # 用换手率和流通市值估算封单强度
    circulating_cap = stock.get('circulating_cap', 0)
    amount = stock.get('amount', 0)
    if circulating_cap > 0 and amount > 0:
        # 封单强度 ≈ (成交额 / 换手率 * 剩余封单比例) / 流通市值
        # 简化：换手率越低，封单越强
        if turnover < 2:
            scores['封单强度'] = 15
            scores['封单强度_desc'] = '封单极强'
        elif turnover < 5:
            scores['封单强度'] = 12
            scores['封单强度_desc'] = '封单较强'
        elif turnover < 10:
            scores['封单强度'] = 8
            scores['封单强度_desc'] = '封单一般'
        elif turnover < 20:
            scores['封单强度'] = 4
            scores['封单强度_desc'] = '封单较弱'
        else:
            scores['封单强度'] = 1
            scores['封单强度_desc'] = '封单极弱'
    else:
        scores['封单强度'] = 1
        scores['封单强度_desc'] = '数据不足'
    total += scores['封单强度']

    # 4. 板块效应（20分）
    if board_limit_up_count >= 10:
        scores['板块效应'] = 20
        scores['板块效应_desc'] = '板块极度强势（{}只涨停）'.format(board_limit_up_count)
    elif board_limit_up_count >= 5:
        scores['板块效应'] = 15
        scores['板块效应_desc'] = '板块强势（{}只涨停）'.format(board_limit_up_count)
    elif board_limit_up_count >= 3:
        scores['板块效应'] = 10
        scores['板块效应_desc'] = '板块较强（{}只涨停）'.format(board_limit_up_count)
    elif board_limit_up_count >= 1:
        scores['板块效应'] = 5
        scores['板块效应_desc'] = '板块一般（{}只涨停）'.format(board_limit_up_count)
    else:
        scores['板块效应'] = 0
        scores['板块效应_desc'] = '无板块效应'
    total += scores['板块效应']

    # 5. 换手健康度（10分）
    if 10 <= turnover <= 20:
        scores['换手健康度'] = 10
        scores['换手健康度_desc'] = '换手健康（{}%）'.format(turnover)
    elif (5 <= turnover < 10) or (20 < turnover <= 30):
        scores['换手健康度'] = 7
        scores['换手健康度_desc'] = '换手适中（{}%）'.format(turnover)
    elif (3 <= turnover < 5) or (30 < turnover <= 40):
        scores['换手健康度'] = 4
        scores['换手健康度_desc'] = '换手一般（{}%）'.format(turnover)
    else:
        scores['换手健康度'] = 1
        scores['换手健康度_desc'] = '换手异常（{}%）'.format(turnover)
    total += scores['换手健康度']

    # 6. 市场辨识度（10分）- 基于连板数估算
    # 连板数通过涨幅和换手率综合判断
    board_count = stock.get('board_count', 1)
    if board_count >= 5:
        scores['市场辨识度'] = 10
        scores['市场辨识度_desc'] = '{}板，市场总龙头'.format(board_count)
    elif board_count >= 3:
        scores['市场辨识度'] = 7
        scores['市场辨识度_desc'] = '{}板，辨识度高'.format(board_count)
    elif board_count >= 2:
        scores['市场辨识度'] = 4
        scores['市场辨识度_desc'] = '{}板，有一定辨识度'.format(board_count)
    else:
        scores['市场辨识度'] = 2
        scores['市场辨识度_desc'] = '首板，关注中'
    total += scores['市场辨识度']

    return {
        'scores': scores,
        'total': total,
    }


def judge_stage(total_score, board_limit_up_count):
    """判断龙头所处阶段

    参数：
        total_score: 龙头总分
        board_limit_up_count: 同板块涨停数量

    返回：
        dict: 阶段信息
    """
    if total_score >= 80 and board_limit_up_count >= 5:
        return {
            'stage': '主升期',
            'emoji': '🚀',
            'color': '#ff6b6b',
            'advice': '积极参与、持有',
            'description': '连板加速，板块效应强，市场合力推动'
        }
    elif total_score >= 60:
        return {
            'stage': '分歧期',
            'emoji': '⚡',
            'color': '#ffd43b',
            'advice': '谨慎、减仓',
            'description': '放量换手，板块分化，注意风险'
        }
    elif total_score >= 40:
        return {
            'stage': '启动期',
            'emoji': '🌱',
            'color': '#51cf66',
            'advice': '关注、试错',
            'description': '首板或二板，板块刚启动，值得关注'
        }
    else:
        return {
            'stage': '见顶期',
            'emoji': '🔝',
            'color': '#868e96',
            'advice': '卖出、回避',
            'description': '高位放量，板块退潮，风险大于机会'
        }


def get_board_limit_up_count(limit_up_stocks, board_name, stock_boards):
    """统计某个板块的涨停股数量"""
    count = 0
    for stock in limit_up_stocks:
        code = stock.get('code', '')
        boards = stock_boards.get(code, [])
        if board_name in boards:
            count += 1
    return count


@cached(CACHE_DRAGON)
def identify_dragon_stocks():
    """主函数：识别龙头股

    流程：
    1. 获取所有涨停股
    2. 获取每只股票的所属板块
    3. 按板块分组统计涨停数量
    4. 对每只涨停股计算龙头评分
    5. 按板块选出龙头股

    返回：
        dict: {
            'hot_boards': [...],  # 热门板块榜
            'dragon_stocks': [...],  # 龙头股列表
            'limit_up_stocks': [...],  # 所有涨停股
            'board_stocks': {...},  # 板块分组
        }
    """
    # 1. 获取涨停股
    limit_up_stocks = get_limit_up_stocks()
    if not limit_up_stocks:
        return None

    # 2. 获取每只股票的所属板块（简化：用行业板块）
    stock_boards = {}
    for stock in limit_up_stocks:
        code = stock.get('code', '')
        boards = get_stock_board_name_by_stock(code)
        if not boards:
            boards = ['其他']
        stock_boards[code] = boards

    # 3. 统计各板块涨停数量
    board_limit_up_count = {}
    for stock in limit_up_stocks:
        code = stock.get('code', '')
        boards = stock_boards.get(code, ['其他'])
        for board in boards:
            if board not in board_limit_up_count:
                board_limit_up_count[board] = 0
            board_limit_up_count[board] += 1

    # 4. 按板块分组涨停股
    board_stocks = {}
    for stock in limit_up_stocks:
        code = stock.get('code', '')
        boards = stock_boards.get(code, ['其他'])
        primary_board = boards[0] if boards else '其他'
        if primary_board not in board_stocks:
            board_stocks[primary_board] = []
        board_stocks[primary_board].append(stock)

    # 5. 对每只涨停股计算龙头评分
    dragon_candidates = []
    for stock in limit_up_stocks:
        code = stock.get('code', '')
        boards = stock_boards.get(code, ['其他'])
        primary_board = boards[0] if boards else '其他'
        board_count = board_limit_up_count.get(primary_board, 0)

        # 使用实际连板数（从 AkShare 数据中获取），如果没有则估算
        if 'board_count' not in stock or stock.get('board_count', 0) < 1:
            change = stock.get('change', 0)
            turnover = stock.get('turnover', 0)
            if change >= 10 and turnover < 3:
                board_count_est = 3
            elif change >= 10 and turnover < 8:
                board_count_est = 2
            else:
                board_count_est = 1
            stock['board_count'] = board_count_est

        result = calc_board_score(stock, board_count)
        stage = judge_stage(result['total'], board_count)

        dragon_candidates.append({
            'stock': stock,
            'board': primary_board,
            'all_boards': boards,
            'board_limit_up_count': board_count,
            'scores': result['scores'],
            'total_score': result['total'],
            'stage': stage,
        })

    # 6. 按总分排序
    dragon_candidates.sort(key=lambda x: x['total_score'], reverse=True)

    # 7. 整理热门板块榜
    hot_boards = []
    for board, count in sorted(board_limit_up_count.items(), key=lambda x: x[1], reverse=True):
        if count >= 1:
            # 找这个板块的龙头（得分最高的）
            board_dragons = [d for d in dragon_candidates if d['board'] == board]
            top_dragon = board_dragons[0] if board_dragons else None
            hot_boards.append({
                'name': board,
                'limit_up_count': count,
                'top_dragon': top_dragon,
            })

    return {
        'hot_boards': hot_boards,
        'dragon_stocks': dragon_candidates,
        'limit_up_stocks': limit_up_stocks,
        'board_stocks': board_stocks,
        'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


def get_board_concept_stocks():
    """获取各概念板块的涨停股分布（简化版）
    使用东方财富概念板块数据
    """
    # 获取概念板块列表
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1",
        "pz": "200",
        "po": "0",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f3",
        "fs": "m:90+t:3",
        "fields": "f12,f14,f3,f4,f20",
        "_": int(datetime.now().timestamp() * 1000)
    }
    headers = _get_headers()
    try:
        resp = request_with_retry(url, params=params, headers=headers, timeout=10)
        if resp is None:
            return []
        data = resp.json()
        items = (data or {}).get('data', {}).get('diff', [])
        boards = []
        for item in items:
            change = safe_float_convert(item.get('f3'))
            boards.append({
                'code': str(item.get('f12', '')),
                'name': str(item.get('f14', '')),
                'change': change,
                'amount': safe_float_convert(item.get('f20')),
            })
        # 按涨幅排序
        boards.sort(key=lambda x: x['change'], reverse=True)
        return boards[:30]  # 取前30个热门概念
    except Exception as e:
        print("获取概念板块数据失败：{}".format(e))
        return []


# ==================== Agent 工具适配层（H6 · 2026-10-04） ====================
# 依据：docs/AGENT_TOOLS_PLAN.md §3.2「dragon_api 12 个函数全部工具化（打板/龙头/阶段判定）……
# 这类"游资向"功能做成独立页面反而敏感（荐股观感），藏在 agent 工具里由用户主动问」。
# H6（P4）落地：**只加薄适配层，不改任何业务函数、不新增数据逻辑、不做页面**。
#
# 为什么不把 12 个函数原样写进 TOOL_REGISTRY（合并依据）：
#   1. 数据形状：get_lhb_stats 返回 DataFrame，而工具载荷必须 JSON 可序列化 —— 直接注册时
#      agent_core._truncate 只能 str(df)，493 行 × 20 列的 repr 会被 8000 字符腰斩成半张表；
#   2. 空值语义：这批函数失败时返回**空列表**，注册表的 none_error 契约只认 None
#      ⇒ 模型看到 `[]` 分不清「今天真的没有」与「数据源不可得」（H6 实测：push2 被墙时
#      get_stock_board_info / get_stock_concept_boards 均静默返回 []）；
#   3. 工具粒度：板块清单有三个函数 —— get_stock_board_info（行业 [{code,name}]）、
#      get_stock_concept_boards（概念**仅名称**、无代码，且虽名为"按股取板块"实现上忽略入参）、
#      get_board_concept_stocks（概念 [{code,name,change,amount}] 且按涨幅排序）。后两者是同一
#      数据源（m:90+t:3）的两种投影，保留信息量更大、能接着查成分股的 get_board_concept_stocks
#      ⇒ 合并为 agent_board_list(board_type=industry|concept)；
#   4. 阶段判定三件套（calc_board_score / judge_stage / get_board_limit_up_count）与
#      get_stock_board_name_by_stock 都是 identify_dragon_stocks 的内部子步骤，入参是
#      "涨停股 dict / 板块成分表"这类 LLM 无法构造的结构 ⇒ 由 get_dragon_stocks 整体暴露。
#
# 工具映射（12 业务函数 → 7 工具，函数:工具 = 12:7）：
#   get_limit_up_pool   ← get_limit_up_stocks
#   get_limit_up_detail ← get_limit_up_detail
#   get_lhb_stats       ← get_lhb_stats
#   get_dragon_stocks   ← identify_dragon_stocks（内部已涵盖 calc_board_score / judge_stage /
#                          get_board_limit_up_count / get_stock_board_name_by_stock）
#   get_board_list      ← get_stock_board_info（industry）
#                          + get_board_concept_stocks（concept；取代 get_stock_concept_boards）
#   get_board_members   ← get_stock_board_members
#   get_stock_boards    ← get_stock_board_name_by_stock
# 未单独暴露但**全部 12 个业务函数均可达**：get_stock_concept_boards 由 concept 分支取代。
#
# 合规：游资向短线数据，每个成功载荷都带 source + risk_note（含「不构成投资建议」），
# 工具描述同款；列表统一裁到 20 条（与 agent_core._truncate 的 list_top_n 同口径，
# 避免"先裁再截断"出现两个截断标记），真实总数放在 count 里。

DRAGON_SOURCE = "东方财富（akshare 涨停池/龙虎榜统计 + push2/datacenter 行情接口）"
DRAGON_RISK_NOTE = (
    "游资向短线数据（打板/龙虎榜），情绪驱动、时效极短、次日不确定性高；"
    "仅供研究参考，不构成投资建议，不荐股。"
)

# 与 agent_core._truncate(list_top_n=20) 同口径
AGENT_TOOL_LIST_TOP_N = 20


def _envelope(data, count=None, **extra):
    """统一工具载荷信封：来源 + 风险提示 + 真实条数 + 数据（短线数据的合规标注）"""
    payload = {"source": DRAGON_SOURCE, "risk_note": DRAGON_RISK_NOTE}
    payload.update(extra)
    if count is not None:
        payload["count"] = count
    payload["data"] = data
    return payload


def agent_limit_up_pool():
    """Agent 工具 get_limit_up_pool ← get_limit_up_stocks（空 → None ⇒ NOT_FOUND）"""
    stocks = get_limit_up_stocks()
    if not stocks:
        return None
    return _envelope(stocks[:AGENT_TOOL_LIST_TOP_N], len(stocks), trade_date=datetime.now().strftime("%Y-%m-%d"))


def agent_limit_up_detail(stock_code):
    """Agent 工具 get_limit_up_detail ← get_limit_up_detail（空 → None ⇒ NOT_FOUND）"""
    detail = get_limit_up_detail(str(stock_code))
    if not detail:
        return None
    return _envelope(detail)


def agent_lhb_stats():
    """Agent 工具 get_lhb_stats ← get_lhb_stats（DataFrame → records，空 → None）"""
    df = get_lhb_stats()
    if df is None or getattr(df, "empty", True):
        return None
    total = int(len(df))
    # 只取前 20 条 records（原始 493 行 × 20 列的表若整表 str() 会被 _truncate 腰斩成半张）
    records = [{str(k): v for k, v in row.items()}
               for row in df.head(AGENT_TOOL_LIST_TOP_N).to_dict("records")]
    return _envelope(records, total)


def agent_dragon_stocks():
    """Agent 工具 get_dragon_stocks ← identify_dragon_stocks（整体暴露打板链条，空 → None）

    identify_dragon_stocks 的原始返回把「同一条龙头记录」同时塞进 hot_boards.top_dragon /
    dragon_stocks / board_stocks（同一份数据 3 次），加上全部涨停股逐只明细，JSON 体积
    可达几十 KB —— 这里只做**裁剪投影**（不改一个数字、不加一个判断），保留模型真正需要的
    榜单、六维评分与阶段；涨停股池明细由 get_limit_up_pool 单独提供。
    """
    data = identify_dragon_stocks()
    if not data:
        return None

    def _brief(item):
        stock = item.get("stock") or {}
        stage = item.get("stage") or {}
        return {
            "code": stock.get("code"),
            "name": stock.get("name"),
            "board": item.get("board"),
            "board_limit_up_count": item.get("board_limit_up_count"),
            "total_score": item.get("total_score"),
            "stage": stage.get("stage"),
            "stage_advice": stage.get("advice"),
            "stage_description": stage.get("description"),
            "scores": item.get("scores"),
            "change": stock.get("change"),
            "turnover": stock.get("turnover"),
            "amount": stock.get("amount"),
            "board_count": stock.get("board_count"),
        }

    dragons_all = data.get("dragon_stocks") or []
    dragons = [_brief(d) for d in dragons_all[:AGENT_TOOL_LIST_TOP_N]]
    hot_boards = []
    for board in (data.get("hot_boards") or [])[:AGENT_TOOL_LIST_TOP_N]:
        top = board.get("top_dragon") or {}
        hot_boards.append({
            "name": board.get("name"),
            "limit_up_count": board.get("limit_up_count"),
            "top_dragon_code": (top.get("stock") or {}).get("code"),
            "top_dragon_name": (top.get("stock") or {}).get("name"),
            "top_dragon_score": top.get("total_score"),
        })
    return _envelope(
        {
            "update_time": data.get("update_time"),
            "hot_boards": hot_boards,
            "dragon_stocks": dragons,
        },
        count=len(dragons_all),
        limit_up_total=len(data.get("limit_up_stocks") or []),
        board_group_count=len(data.get("board_stocks") or {}),
    )


def agent_board_list(board_type="industry"):
    """Agent 工具 get_board_list ← get_stock_board_info（行业）+ get_board_concept_stocks（概念）

    概念分支刻意不接 get_stock_concept_boards：它同一数据源但只取名称（无代码）、
    且忽略入参 —— 模型拿到名称后无法接着查成分股，属退化投影。
    """
    kind = str(board_type or "industry").strip().lower()
    if kind in ("concept", "concepts", "gn", "t3", "概念", "概念板块"):
        boards = get_board_concept_stocks()
        kind_label = "concept"
    else:
        boards = get_stock_board_info()
        kind_label = "industry"
    if not boards:
        return None
    return _envelope(boards[:AGENT_TOOL_LIST_TOP_N], len(boards), board_type=kind_label)


def agent_board_members(board_code):
    """Agent 工具 get_board_members ← get_stock_board_members（该接口只返回成分股代码）"""
    codes = get_stock_board_members(str(board_code))
    if not codes:
        return None
    return _envelope(codes[:AGENT_TOOL_LIST_TOP_N], len(codes), board_code=str(board_code))


def agent_stock_boards(stock_code):
    """Agent 工具 get_stock_boards ← get_stock_board_name_by_stock（个股所属板块名）"""
    boards = get_stock_board_name_by_stock(str(stock_code))
    if not boards:
        return None
    return _envelope(boards, len(boards), stock_code=str(stock_code))

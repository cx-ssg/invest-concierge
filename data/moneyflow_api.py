# -*- coding: utf-8 -*-
"""
资金流向数据获取 - 使用 AkShare 获取大盘资金流向、板块资金流向、个股资金流向、北向资金等数据
"""

import akshare as ak
import pandas as pd
import time
from datetime import datetime, timedelta


from data.cache import cached, CACHE_MONEYFLOW
import utils.common
safe_float_convert = getattr(utils.common, "safe_float_convert", lambda x, default=0.0: default)
safe_get = getattr(utils.common, "safe_get", lambda d, k, default=None: default)
call_akshare_with_retry = getattr(utils.common, "call_akshare_with_retry", lambda func, **kwargs: None)


@cached(CACHE_MONEYFLOW)
def get_market_moneyflow():
    """获取大盘资金流向概览
    
    返回：
    - main_flow: 主力资金净流入（亿元）
    - super_large_flow: 超大单净流入（亿元）
    - large_flow: 大单净流入（亿元）
    - medium_flow: 中单净流入（亿元）
    - small_flow: 小单净流入（亿元）
    - north_flow: 北向资金净流入（亿元）
    - south_flow: 南向资金净流入（亿元）
    - update_time: 更新时间
    """
    result = {
        'main_flow': None,
        'super_large_flow': None,
        'large_flow': None,
        'medium_flow': None,
        'small_flow': None,
        'north_flow': None,
        'south_flow': None,
        'update_time': datetime.now().strftime('%Y-%m-%d %H:%M'),
    }
    
    # 1. 获取沪深两市资金流向
    try:
        time.sleep(0.3)
        df_money = call_akshare_with_retry(ak.stock_sector_fund_flow_rank, indicator="今日", sector_type="地域")
        if df_money is not None and not df_money.empty:
            # 从地域板块资金流汇总中提取整体数据
            if '主力净流入-净额' in df_money.columns:
                total_main = df_money['主力净流入-净额'].sum()
                if total_main is not None:
                    result['main_flow'] = safe_float_convert(total_main) / 100000000  # 转为亿元
    except Exception as e:
        print("获取沪深资金流向失败：{}".format(e))
    
    # 2. 获取个股资金流向来汇总超大单、大单等
    try:
        time.sleep(0.3)
        df_individual = call_akshare_with_retry(ak.stock_individual_fund_flow, stock="sh600519", market="sh")
        if df_individual is not None:
            pass
    except Exception:
        pass
    
    # 3. 使用 stock_market_fund_flow 获取整体资金流向
    try:
        time.sleep(0.3)
        df_flow = call_akshare_with_retry(ak.stock_market_fund_flow)
        if df_flow is not None and not df_flow.empty:
            latest = df_flow.iloc[-1] if len(df_flow) > 0 else df_flow.iloc[0]
            
            # 尝试获取各类型资金流向
            for col in df_flow.columns:
                col_str = str(col)
                val = safe_float_convert(safe_get(latest, col))
                
                if '主力' in col_str and '净流入' in col_str:
                    if result['main_flow'] is None:
                        result['main_flow'] = val
                elif '超大单' in col_str and '净流入' in col_str:
                    result['super_large_flow'] = val
                elif '大单' in col_str and '净流入' in col_str:
                    result['large_flow'] = val
                elif '中单' in col_str and '净流入' in col_str:
                    result['medium_flow'] = val
                elif '小单' in col_str and '净流入' in col_str:
                    result['small_flow'] = val
    except Exception as e:
        print("获取市场资金流向失败：{}".format(e))
    
    # 4. 获取北向资金 - 使用 stock_hsgt_hist_em 替代 stock_hsgt_north_net_flow_in_em
    try:
        time.sleep(0.3)
        df_north = call_akshare_with_retry(ak.stock_hsgt_hist_em, symbol="北向资金")
        if df_north is not None and not df_north.empty:
            latest = df_north.iloc[-1]
            # 列名: '当日成交净买额'（单位：亿元）
            val = safe_float_convert(safe_get(latest, '当日成交净买额'))
            if val != 0:
                result['north_flow'] = val
    except Exception as e:
        print("获取北向资金失败：{}".format(e))
    
    # 5. 获取南向资金 - 使用 stock_hsgt_hist_em 替代 stock_hsgt_north_net_flow_in_em
    try:
        time.sleep(0.3)
        df_south = call_akshare_with_retry(ak.stock_hsgt_hist_em, symbol="南向资金")
        if df_south is not None and not df_south.empty:
            latest = df_south.iloc[-1]
            val = safe_float_convert(safe_get(latest, '当日成交净买额'))
            if val != 0:
                result['south_flow'] = val
    except Exception as e:
        print("获取南向资金失败：{}".format(e))
    
    return result


@cached(CACHE_MONEYFLOW)
def get_sector_moneyflow(top_n=20):
    """获取行业板块资金流向排名
    
    返回列表，每个元素包含：
    - name: 板块名称
    - change: 涨跌幅（%）
    - main_flow: 主力净流入（亿元）
    - main_flow_ratio: 主力净流入占比（%）
    """
    results = []
    try:
        time.sleep(0.3)
        df = call_akshare_with_retry(ak.stock_sector_fund_flow_rank, indicator="今日", sector_type="行业")
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                name = safe_get(row, '名称', '')
                change = safe_float_convert(safe_get(row, '涨跌幅'))
                
                # 主力净流入（可能有不同列名）
                main_flow = None
                for col in ['主力净流入-净额', '主力净流入', '主力净流入净额']:
                    if col in df.columns:
                        val = safe_float_convert(safe_get(row, col))
                        if val != 0:
                            main_flow = val / 100000000  # 转为亿元
                            break
                
                # 主力净流入占比
                main_ratio = None
                for col in ['主力净流入-净占比', '主力净流入占比', '主力净流入净占比']:
                    if col in df.columns:
                        val = safe_float_convert(safe_get(row, col))
                        if val != 0:
                            main_ratio = val
                            break
                
                results.append({
                    'name': name,
                    'change': change,
                    'main_flow': main_flow if main_flow is not None else 0,
                    'main_flow_ratio': main_ratio if main_ratio is not None else 0,
                })
                
                if len(results) >= top_n:
                    break
    except Exception as e:
        print("获取板块资金流向失败：{}".format(e))
    
    return results


@cached(CACHE_MONEYFLOW)
def get_stock_moneyflow_rank(top_n=20, order='desc'):
    """获取个股资金流向排行
    
    参数：
    - top_n: 返回数量
    - order: 'desc' 净流入排行 / 'asc' 净流出排行
    
    返回列表，每个元素包含：
    - name: 股票名称
    - code: 股票代码
    - change: 涨跌幅（%）
    - main_flow: 主力净流入（亿元）
    - main_flow_ratio: 主力净流入占比（%）
    """
    results = []
    try:
        time.sleep(0.3)
        df = call_akshare_with_retry(ak.stock_individual_fund_flow_rank, indicator="今日")
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                name = safe_get(row, '名称', '')
                code = str(safe_get(row, '代码', ''))
                change = safe_float_convert(safe_get(row, '涨跌幅'))
                
                # 主力净流入
                main_flow = None
                for col in ['主力净流入-净额', '主力净流入', '主力净流入净额']:
                    if col in df.columns:
                        val = safe_float_convert(safe_get(row, col))
                        if val != 0:
                            main_flow = val / 100000000
                            break
                
                # 主力净流入占比
                main_ratio = None
                for col in ['主力净流入-净占比', '主力净流入占比', '主力净流入净占比']:
                    if col in df.columns:
                        val = safe_float_convert(safe_get(row, col))
                        if val != 0:
                            main_ratio = val
                            break
                
                results.append({
                    'name': name,
                    'code': code,
                    'change': change,
                    'main_flow': main_flow if main_flow is not None else 0,
                    'main_flow_ratio': main_ratio if main_ratio is not None else 0,
                })
            
            # 排序
            reverse = (order == 'desc')
            results.sort(key=lambda x: x['main_flow'], reverse=reverse)
            results = results[:top_n]
    except Exception as e:
        print("获取个股资金流向排行失败：{}".format(e))
    
    return results


@cached(CACHE_MONEYFLOW)
def get_north_moneyflow_details():
    """获取北向资金详细数据
    
    返回：
    - today_flow: 今日净流入（亿元）
    - recent_5d: 近5日累计净流入（亿元）
    - recent_10d: 近10日累计净流入（亿元）
    - recent_30d: 近30日累计净流入（亿元）
    - top_holdings: 北向资金持股最多的股票（前10）
    - top_increase: 北向资金增持最多的股票（前10）
    """
    result = {
        'today_flow': None,
        'recent_5d': None,
        'recent_10d': None,
        'recent_30d': None,
        'top_holdings': [],
        'top_increase': [],
    }
    
    # 1. 获取北向资金历史净流入 - 使用 stock_hsgt_hist_em 替代 stock_hsgt_north_net_flow_in_em
    try:
        time.sleep(0.3)
        df_north = call_akshare_with_retry(ak.stock_hsgt_hist_em, symbol="北向资金")
        if df_north is not None and not df_north.empty:
            # 今日净流入（列名: '当日成交净买额'）
            latest = df_north.iloc[-1]
            val = safe_float_convert(safe_get(latest, '当日成交净买额'))
            if val != 0:
                result['today_flow'] = val
            
            # 近5日累计
            if len(df_north) >= 5:
                recent_5 = df_north.tail(5)
                total_5d = safe_float_convert(recent_5['当日成交净买额'].sum()) if '当日成交净买额' in df_north.columns else 0
                result['recent_5d'] = total_5d
            
            # 近10日累计
            if len(df_north) >= 10:
                recent_10 = df_north.tail(10)
                total_10d = safe_float_convert(recent_10['当日成交净买额'].sum()) if '当日成交净买额' in df_north.columns else 0
                result['recent_10d'] = total_10d
            
            # 近30日累计
            if len(df_north) >= 30:
                recent_30 = df_north.tail(30)
                total_30d = safe_float_convert(recent_30['当日成交净买额'].sum()) if '当日成交净买额' in df_north.columns else 0
                result['recent_30d'] = total_30d
    except Exception as e:
        print("获取北向资金历史数据失败：{}".format(e))
    
    # 2. 获取北向资金持股排行 - 使用 stock_hsgt_hold_stock_em 替代 stock_hsgt_holding_analyse_em
    try:
        time.sleep(0.3)
        # stock_hsgt_hold_stock_em 参数: market in {"北向", "沪股通", "深股通"}, indicator in {"今日排行", "3日排行", "5日排行", "10日排行", "月排行", "季排行", "年排行"}
        df_hold = call_akshare_with_retry(ak.stock_hsgt_hold_stock_em, market="北向", indicator="今日排行")
        if df_hold is not None and not df_hold.empty:
            for _, row in df_hold.head(10).iterrows():
                result['top_holdings'].append({
                    'name': safe_get(row, '名称', ''),
                    'code': str(safe_get(row, '代码', '')),
                    'hold_value': safe_float_convert(safe_get(row, '持股市值', 0)),
                    'hold_ratio': safe_float_convert(safe_get(row, '占流通股比例', 0)),
                })
    except Exception as e:
        print("获取北向资金持股排行失败：{}".format(e))
    
    # 3. 获取北向资金增持排行 - 持股占流通股比例排行
    try:
        time.sleep(0.3)
        df_inc = call_akshare_with_retry(ak.stock_hsgt_hold_stock_em, market="北向", indicator="今日排行")
        if df_inc is not None and not df_inc.empty:
            # 找比例变动列
            change_col = None
            for col in ['持股比例变动', '比例变动', '占流通股比例变动', '占流通股比例']:
                if col in df_inc.columns:
                    change_col = col
                    break
            
            if change_col:
                df_sorted = df_inc.sort_values(by=change_col, ascending=False)
                for _, row in df_sorted.head(10).iterrows():
                    result['top_increase'].append({
                        'name': safe_get(row, '名称', ''),
                        'code': str(safe_get(row, '代码', '')),
                        'change_ratio': safe_float_convert(safe_get(row, change_col, 0)),
                        'hold_ratio': safe_float_convert(safe_get(row, '占流通股比例', 0)),
                    })
    except Exception as e:
        print("获取北向资金增持排行失败：{}".format(e))
    
    return result
# -*- coding: utf-8 -*-
"""
基金工具函数 - 从 web_agent.py 迁移的共享工具
"""

import io
import base64
import logging

import matplotlib.pyplot as plt
import streamlit as st
from datetime import datetime

from data.cache import cached, CACHE_QUOTE
from data.database import load_funds, save_funds
from utils.common import load_json_file, save_json_file, safe_request

logger = logging.getLogger(__name__)


# ==================== 基金信息查询 ====================


@cached(ttl=CACHE_QUOTE)
def get_fund_info(fund_code):
    """查询基金信息，返回字典（带缓存）"""
    import data.fund_api as fund_api

    try:
        fund_data = fund_api.get_fund_real_time(fund_code)
        if fund_data:
            result = {
                'name': fund_data.get('fund_name', ''),
                'fundcode': fund_data.get('fund_code', fund_code),
                'dwjz': fund_data.get('estimated_unit_value', 0),
                'gsz': fund_data.get('estimated_unit_value', 0),
                'gszzl': fund_data.get('estimated_change_percent', 0),
                'gztime': fund_data.get('estimation_time', '')
            }
            return result
    except Exception as e:
        logger.warning("获取基金信息失败: %s", e)
    return None


@cached(ttl=CACHE_QUOTE)
def get_fund_history(fund_code, days=365):
    """查询基金历史净值（翻页获取更多数据，带缓存）"""
    import data.fund_api as fund_api

    try:
        history = fund_api.get_fund_history(fund_code, days=days)
        if history:
            return history
    except Exception as e:
        logger.warning("获取基金历史净值失败: %s", e)
    return []


def add_fund(fund_code, amount, fund_name=None):
    """添加基金到持仓"""
    funds = load_funds()
    if fund_code in funds:
        return False, "该基金已在持仓中"

    data = get_fund_info(fund_code)
    if data:
        name = fund_name or data.get('name', '')
        current_price = float(data.get('gsz', data.get('dwjz', 1)))
        shares = round(amount / current_price, 4)
        funds[fund_code] = {
            'name': name,
            'amount': amount,
            'shares': shares,
            'buy_price': current_price,
        }
        save_funds(funds)
        return True, "添加成功"
    else:
        return False, "获取基金信息失败，请检查代码"


def get_my_portfolio():
    """查询持仓收益，返回详细文本"""
    funds = load_funds()
    if not funds:
        return "暂无持仓数据"

    total_invest = sum(fund['amount'] for fund in funds.values())
    total_value = total_invest
    lines = []

    for code, fund in funds.items():
        fund_data = get_fund_info(code)
        if fund_data:
            current_price = float(fund_data.get('gsz', fund_data.get('dwjz', fund['buy_price'])))
            current_value = fund['shares'] * current_price
            profit = current_value - fund['amount']
            rate = (profit / fund['amount']) * 100 if fund['amount'] > 0 else 0
            total_value += (current_value - fund['amount'])
            lines.append("  {}（{}）：投入{}元，当前{}元，收益{}元（{}%）".format(
                fund_data.get('name', fund['name']), code,
                round(fund['amount'], 2), round(current_value, 2),
                round(profit, 2), round(rate, 2)
            ))
        else:
            lines.append("  {}（{}）：获取数据失败".format(fund['name'], code))

    profit = total_value - total_invest
    rate = (profit / total_invest) * 100 if total_invest > 0 else 0

    result = "总投入{}元，当前总价值{}元，总收益{}元（{}%）\n".format(
        round(total_invest, 2), round(total_value, 2),
        round(profit, 2), round(rate, 2)
    )
    result += "\n各基金详情：\n"
    result += "\n".join(lines)
    return result


def get_portfolio_chart():
    """返回持仓饼图的 base64 编码（供 AI 使用）"""
    funds = load_funds()
    if not funds:
        return ""

    names = []
    amounts = []

    for code, fund in funds.items():
        fund_data = get_fund_info(code)
        if fund_data:
            current_price = float(fund_data.get('gsz', fund_data.get('dwjz', fund['buy_price'])))
            current_value = fund['shares'] * current_price
            names.append(fund_data.get('name', fund['name']))
            amounts.append(current_value)
        else:
            names.append(fund['name'])
            amounts.append(fund['amount'])

    fig, ax = plt.subplots(figsize=(10, 6))
    colors = ['#ff9999', '#66b3ff', '#99ff99', '#ffcc99', '#ff99cc', '#c2c2f0',
              '#ffb3e6', '#c4e17f', '#76d7c4', '#f0e68c', '#dda0dd', '#87ceeb']
    ax.pie(amounts, labels=names, autopct='%1.1f%%', startangle=90, colors=colors[:len(names)])
    ax.axis('equal')

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=100, bbox_inches='tight')
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('utf-8')


# ==================== 定投计算 ====================


def calc_dca(fund_code, monthly_amount, months=12):
    """计算定投收益（基于历史数据回测）"""
    history = get_fund_history(fund_code, days=months * 31 + 30)
    if not history:
        return None

    total_invest = 0
    total_shares = 0
    monthly_invest = monthly_amount

    data = history[-months:] if len(history) >= months else history
    step = max(1, len(data) // months) if len(data) > months else 1

    count = 0
    for i in range(0, len(data), step):
        if count >= months:
            break
        unit_value = float(data[i][1]) if isinstance(data[i], (list, tuple)) else float(data[i].get('unit_value', 1))
        if unit_value > 0:
            total_invest += monthly_invest
            total_shares += monthly_invest / unit_value
        count += 1

    if total_shares == 0:
        return None

    latest_value = float(data[-1][1]) if isinstance(data[-1], (list, tuple)) else float(data[-1].get('unit_value', 1))
    total_value = total_shares * latest_value
    profit = total_value - total_invest
    rate = (profit / total_invest) * 100 if total_invest > 0 else 0

    return {
        'total_invest': round(total_invest, 2),
        'total_value': round(total_value, 2),
        'profit': round(profit, 2),
        'rate': round(rate, 2),
        'price': latest_value,
        'months': count
    }


# ==================== 基金诊断指标计算 ====================


def calc_fund_metrics(fund_code, days=365):
    """计算基金的各项指标"""
    history = get_fund_history(fund_code, days=days)
    if not history or len(history) < 2:
        return None

    prices = [float(h[1]) if isinstance(h, (list, tuple)) else float(h.get('unit_value', 1)) for h in history]
    if not prices or prices[0] == 0:
        return None

    total_return = (prices[-1] - prices[0]) / prices[0] * 100

    daily_returns = [(prices[i] - prices[i - 1]) / prices[i - 1] for i in range(1, len(prices))]

    avg_return = sum(daily_returns) / len(daily_returns) if daily_returns else 0
    variance = sum((r - avg_return) ** 2 for r in daily_returns) / len(daily_returns) if daily_returns else 0
    volatility = (variance ** 0.5) * (252 ** 0.5) * 100

    risk_free = 0.02
    excess_return = avg_return * 252 - risk_free
    sharpe = excess_return / (volatility / 100) if volatility > 0 else 0

    negative_returns = [r for r in daily_returns if r < 0]
    if negative_returns:
        down_var = sum(r ** 2 for r in negative_returns) / len(negative_returns)
        down_dev = (down_var ** 0.5) * (252 ** 0.5)
        sortino = excess_return / (down_dev * 100) if down_dev > 0 else 0
    else:
        sortino = 0

    max_drawdown = 0
    peak = prices[0]
    for p in prices:
        if p > peak:
            peak = p
        drawdown = (peak - p) / peak
        if drawdown > max_drawdown:
            max_drawdown = drawdown

    wins = sum(1 for r in daily_returns if r > 0)
    win_rate = wins / len(daily_returns) * 100 if daily_returns else 0

    return {
        'total_return': round(total_return, 2),
        'volatility': round(volatility, 2),
        'sharpe': round(sharpe, 2),
        'sortino': round(sortino, 2),
        'max_drawdown': round(max_drawdown * 100, 2),
        'win_rate': round(win_rate, 2),
        'data_days': len(prices)
    }


# ==================== 关注列表 ====================


def add_to_watchlist(fund_code, fund_name):
    """添加到关注列表"""
    watchlist = load_json_file("watchlist.json", [])
    if not isinstance(watchlist, list):
        watchlist = []
    for item in watchlist:
        if item.get('code') == fund_code:
            return False, "已在关注列表中"
    watchlist.append({'code': fund_code, 'name': fund_name, 'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')})
    save_json_file("watchlist.json", watchlist)
    return True, "添加成功"
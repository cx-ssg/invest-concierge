# -*- coding: utf-8 -*-
"""
基金数据获取函数 - 通过 AkShare 获取基金行情、历史数据等
"""

import re
from datetime import datetime, timedelta

from config import CACHE_TTL
from data.cache import cached, CACHE_QUOTE
import akshare as ak
import pandas as pd

import utils.common
safe_float_convert = getattr(utils.common, "safe_float_convert", lambda x, default=0.0: default)


def _get_fund_list():
    """获取全量基金列表（缓存在模块级别）"""
    if not hasattr(_get_fund_list, '_cache'):
        try:
            df = ak.fund_name_em()
            # 统一列名
            df.columns = [c.strip() for c in df.columns]
            _get_fund_list._cache = df
        except Exception as e:
            print("获取基金列表失败：{}".format(e))
            _get_fund_list._cache = pd.DataFrame()
    return _get_fund_list._cache


@cached(CACHE_QUOTE, cache_failures=True, failure_ttl=300)
def get_fund_info(fund_code):
    """获取基金基本信息（名称、基金类型等）"""
    try:
        df = _get_fund_list()
        if df.empty:
            return None
        # 查找基金
        row = df[df['基金代码'].astype(str) == str(fund_code).zfill(6)]
        if row.empty:
            # 再试试 ETF/LOF 行情
            try:
                etf_df = ak.fund_etf_spot_em()
                etf_row = etf_df[etf_df['代码'].astype(str) == str(fund_code).zfill(6)]
                if not etf_row.empty:
                    r = etf_row.iloc[0]
                    return {
                        'name': str(r.get('名称', '')),
                        'code': fund_code,
                        'dwjz': safe_float_convert(r.get('最新价', 0), default='--'),
                        'gsz': '--',
                        'gszzl': safe_float_convert(r.get('涨跌幅', 0), default=0),
                        'gztime': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    }
            except Exception:
                pass
            return None
        r = row.iloc[0]
        return {
            'name': str(r.get('基金简称', str(r.get('基金名称', '')))),
            'code': fund_code,
            'dwjz': '--',
            'gsz': '--',
            'gszzl': 0,
            'gztime': '',
        }
    except Exception as e:
        print("获取基金 {} 信息失败：{}".format(fund_code, e))
        return None


def get_fund_history(fund_code, days=365):
    """获取基金历史净值数据（公开契约不变：返回 (dates, values)，空为 ([], [])）。

    2026-09-04 优化：实际拉取走 _fetch_fund_history 缓存（成功 1 小时——净值日更粒度，
    失败/空数据负缓存 5 分钟）。此前无缓存：load_funds_snapshot 每次逐只重拉历史净值，
    同会话重复问同一只基金每次都付全量网络成本。
    """
    res = _fetch_fund_history(fund_code, days=days)
    return res if res is not None else ([], [])


@cached(3600, cache_failures=True, failure_ttl=300)
def _fetch_fund_history(fund_code, days=365):
    """真实拉取基金历史净值（供 get_fund_history 的缓存层）。

    缓存语义：有数据 → (dates, values) 缓存 1h；None（拉取失败或空）负缓存 5 分钟
    （cached 的 cache_failures 只认 None，空数据在此归一为 None）。
    """
    try:
        df = ak.fund_open_fund_info_em(symbol=fund_code, indicator="单位净值走势")
        if df is None or df.empty:
            return None
        df = df.sort_index()
        dates = []
        values = []
        for idx, row in df.iterrows():
            try:
                d = pd.to_datetime(idx)
                v = float(row.iloc[0])
                dates.append(d)
                values.append(v)
            except (ValueError, TypeError):
                continue
        # 截取需要的天数
        if len(dates) > days:
            dates = dates[-days:]
            values = values[-days:]
        return (dates, values) if dates else None
    except Exception as e:
        print("获取基金 {} 历史数据失败：{}".format(fund_code, e))
        return None


@cached(CACHE_QUOTE)
def search_fund(keyword):
    """搜索基金"""
    try:
        df = _get_fund_list()
        if df.empty:
            return []
        results = []
        keyword = str(keyword).strip().upper()
        for _, row in df.iterrows():
            code = str(row.get('基金代码', '')).strip()
            name = str(row.get('基金简称', str(row.get('基金名称', '')))).strip()
            pinyin = str(row.get('拼音缩写', '')).strip().upper()
            pinyin_full = str(row.get('拼音全称', '')).strip().upper()
            if (keyword in code or keyword in name.upper() or
                    keyword in pinyin or keyword in pinyin_full or keyword in name):
                results.append({
                    'code': code,
                    'name': name,
                    'type': str(row.get('基金类型', '')),
                    'pinyin': pinyin,
                })
        return results[:50]  # 最多返回50条
    except Exception as e:
        print("搜索基金失败：{}".format(e))
        return []


# 为 fund_search.py 提供 search_funds 别名
search_funds = search_fund


def compare_funds(fund_code1, fund_code2):
    """对比两只基金的基本情况"""
    info1 = get_fund_info(fund_code1)
    info2 = get_fund_info(fund_code2)
    if not info1 or not info2:
        return "获取基金数据失败，请检查基金代码是否正确"

    result = "📊 基金对比结果：\n\n"
    result += "**{}（{}）** vs **{}（{}）**\n\n".format(
        info1.get('name', ''), fund_code1,
        info2.get('name', ''), fund_code2
    )
    result += "| 指标 | {} | {} |\n".format(info1.get('name', ''), info2.get('name', ''))
    result += "|------|------|------|\n"
    result += "| 最新净值 | {} | {} |\n".format(info1.get('dwjz', '--'), info2.get('dwjz', '--'))
    result += "| 估算净值 | {} | {} |\n".format(info1.get('gsz', '--'), info2.get('gsz', '--'))
    result += "| 今日涨跌 | {}% | {}% |\n".format(info1.get('gszzl', 0), info2.get('gszzl', 0))
    result += "| 更新时间 | {} | {} |\n".format(info1.get('gztime', ''), info2.get('gztime', ''))
    return result


def compare_funds_structured(fund_code1, fund_code2):
    """基金对比的结构化版本（M0：返 JSON-ready dict 给 React/API；Markdown 版保留给 agent 工具）。

    返回 {"ok": bool, "error": str, "funds": [info1, info2], "metrics": [...]}；
    metrics 行形如 {"key","label","value1","value2"}。
    """
    info1 = get_fund_info(fund_code1)
    info2 = get_fund_info(fund_code2)
    if not info1 or not info2:
        missing = []
        if not info1:
            missing.append(fund_code1)
        if not info2:
            missing.append(fund_code2)
        return {
            "ok": False,
            "error": "获取基金数据失败，请检查基金代码是否正确：{}".format(", ".join(missing)),
            "funds": [info1, info2],
            "metrics": [],
        }

    def _num(v, default=None):
        try:
            return round(float(v), 4)
        except (TypeError, ValueError):
            return default

    metrics = [
        {"key": "dwjz", "label": "最新净值", "value1": _num(info1.get('dwjz')), "value2": _num(info2.get('dwjz'))},
        {"key": "gsz", "label": "估算净值", "value1": _num(info1.get('gsz')), "value2": _num(info2.get('gsz'))},
        {"key": "gszzl", "label": "今日涨跌(%)", "value1": _num(info1.get('gszzl')), "value2": _num(info2.get('gszzl'))},
        {"key": "gztime", "label": "更新时间", "value1": info1.get('gztime', ''), "value2": info2.get('gztime', '')},
    ]
    return {"ok": True, "error": "", "funds": [info1, info2], "metrics": metrics}


def calc_fund_metrics(fund_code, days=365):
    """计算基金的各种指标（收益率、最大回撤、波动率、夏普比率等）"""
    dates, values = get_fund_history(fund_code, days)
    if not dates or len(values) < 20:
        return None

    # 各周期收益率
    returns = {}
    periods = {
        '近1周': 7, '近1月': 30, '近3月': 90,
        '近6月': 180, '近1年': 365
    }
    for name, period in periods.items():
        if len(values) >= period:
            ret = (values[-1] - values[-period]) / values[-period] * 100
            returns[name] = round(ret, 2)

    # 最大回撤
    max_drawdown = 0
    peak = values[0]
    for v in values:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100
        if dd > max_drawdown:
            max_drawdown = dd

    # 年化波动率（日收益率标准差 * sqrt(245)）
    daily_returns = []
    for i in range(1, len(values)):
        r = (values[i] - values[i - 1]) / values[i - 1]
        daily_returns.append(r)

    if daily_returns:
        mean_r = sum(daily_returns) / len(daily_returns)
        variance = sum((r - mean_r) ** 2 for r in daily_returns) / len(daily_returns)
        daily_vol = variance ** 0.5
        annual_vol = daily_vol * (245 ** 0.5) * 100
    else:
        annual_vol = 0

    # 夏普比率（假设无风险利率 2%）
    if annual_vol > 0:
        annual_return = (values[-1] - values[0]) / values[0] * 100
        sharpe = (annual_return - 2) / annual_vol
    else:
        sharpe = 0

    return {
        'returns': returns,
        'max_drawdown': round(max_drawdown, 2),
        'volatility': round(annual_vol, 2),
        'sharpe': round(sharpe, 2),
        'dates': dates,
        'values': values,
    }


def calc_profit_rate(profit, amount):
    """计算收益率"""
    if amount == 0:
        return 0
    return round((profit / amount) * 100, 2)


def backtest_strategy(fund_code, total_amount, months, strategy='dca'):
    """回测定投/一次性买入策略"""
    days = months * 30 + 30
    dates, values = get_fund_history(fund_code, days)

    if not dates or len(values) < 30:
        return None

    if strategy == 'lump_sum':
        # 一次性买入
        buy_price = values[0]
        shares = total_amount / buy_price
        final_value = shares * values[-1]
        profit = final_value - total_amount
        profit_rate = (profit / total_amount) * 100

        return {
            'final_value': final_value,
            'profit': profit,
            'profit_rate': profit_rate,
            'dates': dates,
            'values': [shares * v for v in values],
            'invest_line': [total_amount] * len(values),
        }

    elif strategy == 'dca':
        # 每月定投
        monthly_amount = total_amount / months
        total_shares = 0
        total_invested = 0
        portfolio_values = []
        invest_line = []
        dates_snapshot = []

        # 找出每月第一个交易日
        last_month = None
        for i, d in enumerate(dates):
            if d.month != last_month:
                if len(dates_snapshot) < months:
                    price = values[i]
                    shares_bought = monthly_amount / price
                    total_shares += shares_bought
                    total_invested += monthly_amount
                    dates_snapshot.append(d)
                    last_month = d.month

                portfolio_values.append(total_shares * values[i])
                invest_line.append(total_invested)

        final_value = total_shares * values[-1]
        profit = final_value - total_invested
        profit_rate = (profit / total_invested) * 100

        return {
            'final_value': final_value,
            'profit': profit,
            'profit_rate': profit_rate,
            'dates': dates,
            'values': portfolio_values if portfolio_values else [total_shares * v for v in values],
            'invest_line': invest_line if invest_line else [total_invested] * len(values),
        }

    return None


def dca_result(fund_code, monthly_amount, months):
    """定投回测的纯计算层（M0 拆分：无 Streamlit/无 matplotlib，可直接服务 API/测试）。

    返回 dict：
        fund_code/fund_name/monthly_amount/months/total_invest/
        final_value/profit/profit_rate + dates/values/invest_line（曲线序列）
    数据不足或代码错误时返回 None。
    """
    backtest = backtest_strategy(fund_code, monthly_amount * months, months, 'dca')
    if not backtest:
        return None
    fund_info = get_fund_info(fund_code)
    fund_name = fund_info.get('name', '未知基金') if fund_info else fund_code
    return {
        'fund_code': fund_code,
        'fund_name': fund_name,
        'monthly_amount': monthly_amount,
        'months': months,
        'total_invest': monthly_amount * months,
        'final_value': backtest['final_value'],
        'profit': backtest['profit'],
        'profit_rate': backtest['profit_rate'],
        'dates': backtest['dates'],
        'values': backtest['values'],
        'invest_line': backtest['invest_line'],
    }


def calc_dca(fund_code, monthly_amount, months):
    """计算定投收益（M0：渲染与计算分离，本函数保留 UI 侧 Streamlit 渲染）"""
    result = dca_result(fund_code, monthly_amount, months)
    if not result:
        import streamlit as st
        st.error("定投计算失败，请检查基金代码是否正确")
        return

    import streamlit as st
    import matplotlib.pyplot as plt
    import matplotlib
    matplotlib.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
    matplotlib.rcParams['axes.unicode_minus'] = False

    fund_name = result.get('fund_name', fund_code)

    st.subheader("📊 {} 定投回测结果".format(fund_name))
    st.caption("每月定投 {} 元，共 {} 个月（{} 年）".format(monthly_amount, months, months // 12))

    total_invest = monthly_amount * months
    profit = result['profit']
    profit_rate = result['profit_rate']

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("总投入", "{} 元".format(total_invest))
    with col2:
        delta = "+{:.2f} 元".format(profit) if profit >= 0 else "{:.2f} 元".format(profit)
        st.metric("最终市值", "{:.2f} 元".format(result['final_value']), delta=delta)
    with col3:
        st.metric("总收益率", "{:.2f}%".format(profit_rate))

    if profit >= 0:
        st.success("🎉 恭喜！定投 {} 个月赚了 {:.2f} 元（{:.2f}%）".format(months, profit, profit_rate))
    else:
        st.error("😅 定投 {} 个月亏了 {:.2f} 元（{:.2f}%）".format(months, abs(profit), abs(profit_rate)))

    st.markdown("---")
    st.subheader("📈 定投收益曲线")

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(result['dates'], result['values'], linewidth=2, label='定投市值', color='#339af0')
    ax.plot(result['dates'], result['invest_line'], '--', label='累计投入', color='#868e96', alpha=0.7)
    ax.fill_between(result['dates'], result['invest_line'], result['values'],
                     where=[v >= i for v, i in zip(result['values'], result['invest_line'])],
                     color='#51cf66', alpha=0.1, label='盈利区域')
    ax.fill_between(result['dates'], result['invest_line'], result['values'],
                     where=[v < i for v, i in zip(result['values'], result['invest_line'])],
                     color='#ff6b6b', alpha=0.1, label='亏损区域')
    ax.set_xlabel('日期')
    ax.set_ylabel('金额（元）')
    ax.set_title('定投收益曲线')
    ax.legend(loc='best')
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    st.pyplot(fig)
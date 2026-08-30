# -*- coding: utf-8 -*-
"""
图表绘制相关函数 - 基金走势图、对比图、持仓分析图等
"""

import matplotlib
matplotlib.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timedelta
import streamlit as st
import numpy as np

from data.fund_api import get_fund_info, get_fund_history, safe_float_convert


def plot_fund_trend(fund_code, days=30):
    """绘制基金走势图"""
    fund_info = get_fund_info(fund_code)
    if not fund_info:
        st.error("未找到基金 {}，请检查基金代码是否正确".format(fund_code))
        return

    fund_name = fund_info.get('name', '未知基金')
    st.markdown("**{}（{}）**".format(fund_name, fund_code))

    dates, values = get_fund_history(fund_code, days)
    if not dates:
        st.warning("暂无历史数据")
        return

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(dates, values, linewidth=2, color='#339af0', label='净值')

    # 填充区域
    ax.fill_between(dates, values, min(values), alpha=0.1, color='#339af0')

    # 标注最新值
    ax.annotate('{:.4f}'.format(values[-1]),
                xy=(dates[-1], values[-1]),
                xytext=(10, 10), textcoords='offset points',
                fontsize=11, fontweight='bold', color='#339af0',
                bbox=dict(boxstyle='round,pad=0.3', facecolor='#1A1A1A', edgecolor='#339af0', alpha=0.8))

    ax.set_xlabel('日期', fontsize=12)
    ax.set_ylabel('单位净值', fontsize=12)
    ax.set_title('{} 净值走势（{}天）'.format(fund_name, days), fontsize=14, fontweight='bold')
    ax.legend(loc='best')
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    plt.xticks(rotation=45)
    plt.tight_layout()
    st.pyplot(fig)

    # 显示统计数据
    if len(values) >= 2:
        change = (values[-1] - values[0]) / values[0] * 100
        max_val = max(values)
        min_val = min(values)
        max_date = dates[values.index(max_val)].strftime('%m-%d')
        min_date = dates[values.index(min_val)].strftime('%m-%d')

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("区间涨幅", "{:.2f}%".format(change))
        with col2:
            st.metric("最高", "{:.4f}（{}）".format(max_val, max_date))
        with col3:
            st.metric("最低", "{:.4f}（{}）".format(min_val, min_date))
        with col4:
            st.metric("波动幅度", "{:.2f}%".format((max_val - min_val) / min_val * 100))


def plot_multi_fund_compare(fund_codes, days=30):
    """绘制多基金对比图"""
    fig, ax = plt.subplots(figsize=(14, 7))
    colors = ['#339af0', '#ff6b6b', '#51cf66', '#ffd43b', '#cc5de8', '#20c997', '#f06595', '#74c0fc']

    for i, code in enumerate(fund_codes):
        fund_info = get_fund_info(code)
        fund_name = fund_info.get('name', code) if fund_info else code
        dates, values = get_fund_history(code, days)

        if dates and values:
            # 归一化到 100
            base = values[0]
            normalized = [(v / base) * 100 for v in values]
            color = colors[i % len(colors)]
            ax.plot(dates, normalized, linewidth=2, label='{} ({})'.format(fund_name, code), color=color)

            # 标注最新值
            ax.annotate('{:.2f}'.format(normalized[-1]),
                        xy=(dates[-1], normalized[-1]),
                        xytext=(5, 0), textcoords='offset points',
                        fontsize=9, color=color, fontweight='bold')

    ax.axhline(y=100, color='#868e96', linestyle='--', alpha=0.5, linewidth=1)
    ax.set_xlabel('日期', fontsize=12)
    ax.set_ylabel('归一化净值（基期=100）', fontsize=12)
    ax.set_title('基金走势对比（{}天）'.format(days), fontsize=14, fontweight='bold')
    ax.legend(loc='best', fontsize=10)
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
    plt.xticks(rotation=45)
    plt.tight_layout()
    st.pyplot(fig)


def plot_portfolio_pie(funds_data):
    """绘制持仓饼图"""
    fig, ax = plt.subplots(figsize=(8, 8))

    names = [f['name'] for f in funds_data]
    values = [f['amount'] for f in funds_data]
    profits = [f.get('profit', 0) for f in funds_data]

    colors = ['#339af0', '#ff6b6b', '#51cf66', '#ffd43b', '#cc5de8',
              '#20c997', '#f06595', '#74c0fc', '#ff922b', '#845ef7']

    wedges, texts, autotexts = ax.pie(
        values, labels=None, autopct='%1.1f%%',
        colors=colors[:len(values)],
        startangle=90,
        pctdistance=0.75,
        wedgeprops={'edgecolor': '#1A1A1A', 'linewidth': 2}
    )

    # 设置百分比文字颜色
    for autotext in autotexts:
        autotext.set_color('white')
        autotext.set_fontsize(11)
        autotext.set_fontweight('bold')

    # 图例
    legend_labels = []
    for i, name in enumerate(names):
        profit = profits[i]
        sign = "+" if profit >= 0 else ""
        legend_labels.append("{} ({}元, {}{}元)".format(
            name, values[i], sign, profit
        ))

    ax.legend(wedges, legend_labels,
              title="持仓明细",
              loc="center left",
              bbox_to_anchor=(1, 0, 0.5, 1),
              fontsize=10,
              title_fontsize=12)

    ax.set_title('持仓分布', fontsize=16, fontweight='bold', pad=20)
    plt.tight_layout()
    st.pyplot(fig)


def plot_profit_bar(funds_data):
    """绘制收益柱状图"""
    fig, ax = plt.subplots(figsize=(12, 6))

    names = [f['name'] for f in funds_data]
    profits = [f.get('profit', 0) for f in funds_data]
    profit_rates = [f.get('profit_rate', 0) for f in funds_data]

    x = range(len(names))
    colors = ['#ff6b6b' if p >= 0 else '#51cf66' for p in profits]

    bars = ax.bar(x, profits, color=colors, width=0.6, edgecolor='#1A1A1A', linewidth=1)

    # 在柱子上标注数值
    for i, (bar, profit, rate) in enumerate(zip(bars, profits, profit_rates)):
        height = bar.get_height()
        sign = "+" if profit >= 0 else ""
        ax.annotate('{}{:.2f}元\n({}{:.2f}%)'.format(sign, profit, sign, rate),
                    xy=(bar.get_x() + bar.get_width() / 2, height),
                    xytext=(0, 3 if profit >= 0 else -15),
                    textcoords="offset points",
                    ha='center', va='bottom' if profit >= 0 else 'top',
                    fontsize=9, fontweight='bold',
                    color='#ff6b6b' if profit >= 0 else '#51cf66')

    ax.axhline(y=0, color='#868e96', linestyle='-', alpha=0.5)
    ax.set_xlabel('基金', fontsize=12)
    ax.set_ylabel('收益（元）', fontsize=12)
    ax.set_title('各基金收益情况', fontsize=14, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels(names, rotation=45, ha='right', fontsize=9)
    ax.grid(True, alpha=0.3, axis='y')
    plt.tight_layout()
    st.pyplot(fig)


def plot_dca_chart(result, fund_name, monthly_amount, months):
    """绘制定投收益曲线图"""
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
    ax.set_title('{} 定投收益曲线'.format(fund_name))
    ax.legend(loc='best')
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    st.pyplot(fig)

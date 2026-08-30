"""
持仓概览卡片组件
可复用的持仓信息展示组件

使用方式:
    from ui_components.holdings_card import render_holdings_card, render_holdings_summary
    
    # 侧边栏紧凑模式
    render_holdings_card(compact=True)
    
    # 主页面完整模式
    render_holdings_card(compact=False)
    
    # 获取统计数据
    summary = render_holdings_summary()

数据依赖:
    data.database.load_funds() -> Dict {code: {name, code, amount, cost_nav, hold_shares}}
    其中 amount=当前市值, cost_nav=持仓成本净值, hold_shares=持有份额

注意事项:
    - 所有渲染使用纯HTML，避免Streamlit组件DOM冲突
    - 盈亏颜色使用A股习惯：红涨绿跌
    - 空数据显示友好提示，不崩溃
"""
import streamlit as st
from typing import Dict, Optional
import logging

# 配置日志
logger = logging.getLogger(__name__)

from data.database import load_funds


def _calc_fund_profit(fund: Dict):
    """
    根据数据库字段计算基金盈亏数据
    
    Args:
        fund: 基金字典，包含 amount/cost_nav/hold_shares
    
    Returns:
        tuple: (nav, profit, profit_rate, market_value, cost_nav, shares)
    """
    amount = fund.get('amount', 0)        # 当前市值
    cost_nav_val = fund.get('cost_nav', 0)     # 成本净值
    shares = fund.get('hold_shares', 0)   # 持有份额
    cost = cost_nav_val * shares                # 总成本
    nav = amount / shares if shares > 0 else 0  # 当前净值
    profit = amount - cost
    profit_rate = (profit / cost * 100) if cost > 0 else 0
    return nav, profit, profit_rate, amount, cost_nav_val, shares


def render_holdings_card(compact: bool = False):
    """
    渲染持仓概览卡片
    
    Args:
        compact: True=侧边栏紧凑模式, False=主页面完整模式
    
    数据流:
        load_funds() -> 计算统计 -> 渲染HTML/组件
    
    DOM安全:
        - 按钮/组件严格在 markdown 外部
        - 基金列表使用纯HTML div，避免 st.columns 嵌套问题
    """
    try:
        funds_dict = load_funds()
        if funds_dict:
            funds = list(funds_dict.values())
            logger.debug("加载基金数据: {}只".format(len(funds)))
        else:
            funds = []
    except Exception as e:
        logger.error("加载基金数据失败: {}".format(e))
        st.error("加载持仓数据失败: {}".format(e))
        return

    # 空数据处理
    if not funds:
        if compact:
            st.markdown(
                '<div class="empty-state" style="padding:16px 8px;">'
                '<div class="es-icon">💼</div><div class="es-title">暂无持仓</div>'
                '<div class="es-hint">到「我的持仓」页添加第一笔基金</div></div>',
                unsafe_allow_html=True)
        else:
            st.markdown(
                '<div class="card"><div class="empty-state">'
                '<div class="es-icon">📭</div><div class="es-title">还没有持仓记录</div>'
                '<div class="es-hint">到「我的持仓」页录入第一笔基金后，<br/>'
                '这里会展示资产总览与收益追踪</div></div></div>',
                unsafe_allow_html=True)
        return

    # === 计算统计数据 ===
    total_value = sum(f.get('amount', 0) for f in funds)
    total_cost = sum(f.get('cost_nav', 0) * f.get('hold_shares', 0) for f in funds)
    total_profit = total_value - total_cost
    profit_rate = (total_profit / total_cost * 100) if total_cost > 0 else 0

    # A股口径：红涨绿跌（涨=红 / 跌=绿），颜色由 CSS 类 text-up/text-down 承载
    profit_sign = "+" if total_profit >= 0 else ""

    if compact:
        # ========== 紧凑模式：用于侧边栏 ==========
        cls = "text-up" if total_profit >= 0 else "text-down"
        st.markdown("""
        <div class="metric-card" style="padding:13px 15px;">
            <div class="m-lbl">总资产概览</div>
            <div class="m-val">¥{value:,.2f}</div>
            <div class="m-delta num"><span class="{cls}">{ps}¥{profit:,.2f}（{ps}{rate:.2f}%）</span>
             <span style="color:var(--text-3);">累计</span></div>
        </div>
        """.format(value=total_value, cls=cls, ps=profit_sign,
                   profit=total_profit, rate=profit_rate), unsafe_allow_html=True)

        # 显示前3只基金（纯HTML，不嵌套Streamlit组件）
        for fund in funds[:3]:
            nav, fund_profit, fund_rate, amount, _, _ = _calc_fund_profit(fund)
            f_cls = "text-up" if fund_profit >= 0 else "text-down"
            f_sign = "+" if fund_profit >= 0 else ""

            st.markdown("""
            <div class="row-item">
                <div>
                    <div style="font-size:12.5px;color:var(--text-1);">{name}</div>
                    <div style="font-size:10.5px;color:var(--text-3);margin-top:1px;">{code}</div>
                </div>
                <div style="text-align:right;">
                    <div style="font-size:12.5px;color:var(--text-1);" class="num">¥{market_value:,.0f}</div>
                    <div style="font-size:11px;margin-top:1px;" class="num {fcls}">{fs}{fr:.2f}%</div>
                </div>
            </div>
            """.format(
                name=fund.get('name', '未知')[:8],
                code=fund.get('code', ''),
                market_value=amount,
                fcls=f_cls,
                fs=f_sign,
                fr=fund_rate
            ), unsafe_allow_html=True)
    else:
        # ========== 完整模式：用于主页面 ==========
        profit_cls = "text-up" if total_profit >= 0 else "text-down"
        up_cnt = sum(1 for f in funds if _calc_fund_profit(f)[1] >= 0)
        down_cnt = len(funds) - up_cnt

        def _mcard(lbl, val, delta_html):
            return ('<div class="metric-card"><div class="m-lbl">{}</div>'
                    '<div class="m-val">{}</div><div class="m-delta">{}</div></div>'
                    ).format(lbl, val, delta_html)

        cards_html = "".join([
            _mcard("总资产", "¥{:,.2f}".format(total_value),
                   '<span class="{}">{}{:.2f}%</span> 累计收益率'.format(
                       profit_cls, profit_sign, profit_rate)),
            _mcard("持仓总盈亏", '<span class="{}">{}¥{:,.2f}</span>'.format(
                profit_cls, profit_sign, total_profit), "按最新估值计算"),
            _mcard("累计收益率", '<span class="{}">{}{:.2f}%</span>'.format(
                profit_cls, profit_sign, profit_rate), "成本口径"),
            _mcard("持仓数量", "{} 只".format(len(funds)),
                   '<span class="text-up">{} 涨</span> · <span class="text-down">{} 跌</span>'.format(
                       up_cnt, down_cnt)),
        ])
        st.markdown(
            '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:16px;">{}</div>'.format(
                cards_html), unsafe_allow_html=True)

        # 基金列表（纯HTML行，避免组件嵌套冲突）
        rows_html = ""
        for fund in funds:
            nav, fund_profit, fund_rate, amount, cost_nav_val, shares = _calc_fund_profit(fund)
            f_cls = "text-up" if fund_profit >= 0 else "text-down"
            f_sign = "+" if fund_profit >= 0 else ""
            rows_html += """
            <div class="row-item" style="padding:11px 8px;">
                <div style="flex:2;">
                    <div style="font-size:13.5px;font-weight:600;color:var(--text-1);">{name}</div>
                    <div style="font-size:11px;color:var(--text-3);margin-top:2px;">{code}</div>
                </div>
                <div style="flex:1;text-align:center;">
                    <div style="font-size:11px;color:var(--text-3);">净值</div>
                    <div style="font-size:13.5px;color:var(--text-2);margin-top:2px;" class="num">{nav_val:.4f}</div>
                </div>
                <div style="flex:1;text-align:center;">
                    <div style="font-size:11px;color:var(--text-3);">市值</div>
                    <div style="font-size:13.5px;color:var(--text-2);margin-top:2px;" class="num">¥{market_val:,.0f}</div>
                </div>
                <div style="flex:1;text-align:right;">
                    <div style="font-size:11px;color:var(--text-3);">盈亏</div>
                    <div style="font-size:13.5px;font-weight:600;margin-top:2px;" class="num {fcls}">{fs}{fr:.2f}%</div>
                </div>
            </div>
            """.format(
                name=fund.get('name', '未知'),
                code=fund.get('code', ''),
                nav_val=nav,
                market_val=amount,
                fcls=f_cls,
                fs=f_sign,
                fr=fund_rate
            )
        st.markdown(
            '<div class="card"><div class="card-title">📋 持仓明细</div>'
            '<div class="card-subtitle">净值与市值按最新估值计算 · 红涨绿跌</div>{}</div>'.format(rows_html),
            unsafe_allow_html=True)


def render_holdings_summary() -> Dict:
    """
    获取持仓摘要数据（供其他组件使用，纯数据函数，不渲染UI）
    
    Returns:
        Dict: {
            'total_value': float,    # 总资产
            'total_profit': float,   # 总盈亏
            'profit_rate': float,    # 收益率(%)
            'fund_count': int        # 持仓数量
        }
    
    示例:
        summary = render_holdings_summary()
        if summary['fund_count'] > 0:
            st.metric("总资产", "¥{:,.2f}".format(summary['total_value']))
    """
    try:
        funds_dict = load_funds()
    except Exception as e:
        logger.error("获取持仓摘要失败: {}".format(e))
        return {
            'total_value': 0,
            'total_profit': 0,
            'profit_rate': 0,
            'fund_count': 0
        }

    if not funds_dict:
        return {
            'total_value': 0,
            'total_profit': 0,
            'profit_rate': 0,
            'fund_count': 0
        }

    funds = list(funds_dict.values())
    total_value = sum(f.get('amount', 0) for f in funds)
    total_cost = sum(f.get('cost_nav', 0) * f.get('hold_shares', 0) for f in funds)
    total_profit = total_value - total_cost
    profit_rate = (total_profit / total_cost * 100) if total_cost > 0 else 0

    return {
        'total_value': total_value,
        'total_profit': total_profit,
        'profit_rate': profit_rate,
        'fund_count': len(funds)
    }
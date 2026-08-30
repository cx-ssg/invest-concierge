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
            st.info("暂无持仓数据")
        else:
            st.warning("📭 暂无持仓数据，快去添加吧！")
        return

    # === 计算统计数据 ===
    total_value = sum(f.get('amount', 0) for f in funds)
    total_cost = sum(f.get('cost_nav', 0) * f.get('hold_shares', 0) for f in funds)
    total_profit = total_value - total_cost
    profit_rate = (total_profit / total_cost * 100) if total_cost > 0 else 0

    # A股习惯：红涨绿跌
    if total_profit >= 0:
        profit_color = "#FF4D4D"
    else:
        profit_color = "#00C853"
    profit_sign = "+" if total_profit >= 0 else ""

    if compact:
        # ========== 紧凑模式：用于侧边栏 ==========
        st.markdown("""
        <div style="
            background: linear-gradient(135deg, #2A2A2A 0%, #333 100%); 
            border-radius: 10px; 
            padding: 12px; 
            margin: 8px 0;
        ">
            <div style="font-size: 12px; color: #999; margin-bottom: 4px;">总资产</div>
            <div style="font-size: 20px; font-weight: bold; color: #FFF;">
                ¥{value:,.2f}
            </div>
            <div style="font-size: 13px; color: {pc}; margin-top: 4px;">
                {ps}¥{profit:,.2f} ({ps}{rate:.2f}%)
            </div>
        </div>
        """.format(
            value=total_value,
            pc=profit_color,
            ps=profit_sign,
            profit=total_profit,
            rate=profit_rate
        ), unsafe_allow_html=True)

        # 显示前3只基金（纯HTML，不嵌套Streamlit组件）
        for fund in funds[:3]:
            nav, fund_profit, fund_rate, amount, _, _ = _calc_fund_profit(fund)
            f_color = "#FF4D4D" if fund_profit >= 0 else "#00C853"
            f_sign = "+" if fund_profit >= 0 else ""

            st.markdown("""
            <div style="
                display: flex; 
                justify-content: space-between; 
                align-items: center;
                padding: 8px 0; 
                border-bottom: 1px solid #333;
            ">
                <div>
                    <div style="font-size: 13px; color: #CCC;">{name}</div>
                    <div style="font-size: 11px; color: #666;">{code}</div>
                </div>
                <div style="text-align: right;">
                    <div style="font-size: 13px; color: #FFF;">
                        ¥{market_value:,.0f}
                    </div>
                    <div style="font-size: 11px; color: {fc};">
                        {fs}{fr:.2f}%
                    </div>
                </div>
            </div>
            """.format(
                name=fund.get('name', '未知')[:8],
                code=fund.get('code', ''),
                market_value=amount,
                fc=f_color,
                fs=f_sign,
                fr=fund_rate
            ), unsafe_allow_html=True)
    else:
        # ========== 完整模式：用于主页面 ==========
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.metric(
                label="总资产",
                value="¥{:,.2f}".format(total_value),
                delta=None
            )
        
        with col2:
            st.metric(
                label="总盈亏",
                value="¥{:,.2f}".format(total_profit),
                delta="{}{:.2f}%".format(profit_sign, profit_rate)
            )
        
        with col3:
            st.metric(
                label="持仓数量",
                value="{}只".format(len(funds)),
                delta=None
            )
        
        # 基金列表（纯HTML，避免组件嵌套冲突）
        st.markdown("---")
        st.markdown("### 📋 持仓明细")
        
        for fund in funds:
            nav, fund_profit, fund_rate, amount, cost_nav_val, shares = _calc_fund_profit(fund)
            f_color = "#FF4D4D" if fund_profit >= 0 else "#00C853"
            f_sign = "+" if fund_profit >= 0 else ""
            
            # 每只基金一行：纯HTML div，不使用st.columns
            st.markdown("""
            <div style="
                display: flex; 
                justify-content: space-between; 
                align-items: center;
                padding: 10px 12px;
                margin: 4px 0;
                background: #2A2A2A;
                border-radius: 8px;
                border-left: 3px solid {border_color};
            ">
                <div style="flex: 2;">
                    <div style="font-size: 14px; font-weight: bold; color: #FFF;">{name}</div>
                    <div style="font-size: 11px; color: #666;">{code}</div>
                </div>
                <div style="flex: 1; text-align: center;">
                    <div style="font-size: 12px; color: #999;">净值</div>
                    <div style="font-size: 14px; color: #CCC;">¥{nav_val:.4f}</div>
                </div>
                <div style="flex: 1; text-align: center;">
                    <div style="font-size: 12px; color: #999;">市值</div>
                    <div style="font-size: 14px; color: #CCC;">¥{market_val:,.0f}</div>
                </div>
                <div style="flex: 1; text-align: right;">
                    <div style="font-size: 12px; color: #999;">盈亏</div>
                    <div style="font-size: 14px; font-weight: bold; color: {fc};">
                        {fs}{fr:.2f}%
                    </div>
                </div>
            </div>
            """.format(
                border_color=f_color,
                name=fund.get('name', '未知'),
                code=fund.get('code', ''),
                nav_val=nav,
                market_val=amount,
                fc=f_color,
                fs=f_sign,
                fr=fund_rate
            ), unsafe_allow_html=True)


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
"""
基金卡片组件
可复用的基金信息展示卡片

使用方式:
    from ui_components.fund_card import render_fund_card, render_fund_grid
    
    # 单个卡片
    render_fund_card(fund_info_dict)
    
    # 网格布局
    render_fund_grid(funds_list, columns=3)

注意:
    - 所有 st.button 使用唯一 key，避免 Streamlit 组件冲突
    - 按钮放在 markdown 外部，避免 DOM 嵌套问题
    - 盈亏颜色使用 A 股习惯：红涨绿跌
"""
import streamlit as st
from typing import Dict, List, Optional
import hashlib


def _make_key(prefix: str, fund_code: str, unique_id: str = "") -> str:
    """
    生成唯一组件 key
    
    Args:
        prefix: 前缀 (buy/sell/analyze)
        fund_code: 基金代码
        unique_id: 额外唯一标识（防止同页面多次渲染同一基金）
    
    Returns:
        唯一 key 字符串
    """
    if unique_id:
        raw = f"{prefix}_{fund_code}_{unique_id}"
    else:
        raw = f"{prefix}_{fund_code}"
    # 使用 hash 确保 key 合法且唯一
    return f"{prefix}_{hashlib.md5(raw.encode()).hexdigest()[:8]}"


def render_fund_card(
    fund_info: Dict, 
    show_actions: bool = True,
    unique_id: str = ""
):
    """
    渲染单个基金卡片
    
    Args:
        fund_info: 基金信息字典，必须包含:
            - name: 基金名称
            - code: 基金代码
            - nav: 当前净值
            - cost: 持仓成本
            - shares: 持有份额
            - type: 基金类型（可选，默认"混合型"）
        show_actions: 是否显示操作按钮（默认True）
        unique_id: 唯一标识，用于区分同基金多次渲染（如列表+详情同时出现）
    
    示例:
        fund = {
            'name': '易方达蓝筹精选',
            'code': '005827',
            'nav': 2.3456,
            'cost': 2.1000,
            'shares': 1000.00,
            'type': '混合型'
        }
        render_fund_card(fund)
    """
    # === 数据提取与计算 ===
    fund_name = fund_info.get('name', '未知基金')
    fund_code = fund_info.get('code', '000000')
    fund_type = fund_info.get('type', '混合型')
    nav = fund_info.get('nav', 0)
    cost = fund_info.get('cost', 0)
    shares = fund_info.get('shares', 0)
    
    # 计算盈亏
    profit = (nav - cost) * shares
    profit_rate = ((nav - cost) / cost * 100) if cost > 0 else 0
    
    # A股习惯：红涨绿跌
    if profit >= 0:
        profit_color = "#FF4D4D"  # 涨：红色
    else:
        profit_color = "#00C853"  # 跌：绿色
    profit_sign = "+" if profit >= 0 else ""
    border_color = profit_color  # 左边框颜色跟随盈亏
    
    market_value = shares * nav  # 持仓市值
    
    # === 渲染卡片主体（纯HTML，不嵌套Streamlit组件） ===
    st.markdown(f"""
    <div style="
        background: linear-gradient(135deg, #2A2A2A 0%, #333 100%);
        border-radius: 12px; 
        padding: 16px; 
        margin: 8px 0;
        border-left: 4px solid {border_color};
    ">
        <!-- 第一行：基金名称 + 净值 -->
        <div style="display: flex; justify-content: space-between; align-items: start;">
            <div>
                <div style="font-size: 16px; font-weight: bold; color: #FFF;">
                    {fund_name}
                </div>
                <div style="font-size: 12px; color: #666; margin-top: 4px;">
                    {fund_code} | {fund_type}
                </div>
            </div>
            <div style="text-align: right;">
                <div style="font-size: 18px; font-weight: bold; color: #FFF;">
                    ¥{nav:.4f}
                </div>
                <div style="font-size: 13px; color: {profit_color}; margin-top: 4px;">
                    {profit_sign}{profit_rate:.2f}%
                </div>
            </div>
        </div>
        
        <!-- 第二行：份额、市值、成本 -->
        <div style="
            display: flex; 
            justify-content: space-between; 
            margin-top: 12px; 
            padding-top: 12px; 
            border-top: 1px solid #444;
        ">
            <div>
                <div style="font-size: 11px; color: #666;">持有份额</div>
                <div style="font-size: 14px; color: #CCC;">{shares:,.2f}</div>
            </div>
            <div>
                <div style="font-size: 11px; color: #666;">持仓市值</div>
                <div style="font-size: 14px; color: #CCC;">¥{market_value:,.2f}</div>
            </div>
            <div>
                <div style="font-size: 11px; color: #666;">持仓成本</div>
                <div style="font-size: 14px; color: #CCC;">¥{cost:.4f}</div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # === 操作按钮（放在markdown外部，避免DOM冲突） ===
    if show_actions:
        col1, col2, col3 = st.columns(3)
        with col1:
            st.button(
                "📈 加仓", 
                key=_make_key("buy", fund_code, unique_id),
                use_container_width=True,
                help=f"加仓 {fund_name}"
            )
        with col2:
            st.button(
                "📉 减仓", 
                key=_make_key("sell", fund_code, unique_id),
                use_container_width=True,
                help=f"减仓 {fund_name}"
            )
        with col3:
            st.button(
                "📊 分析", 
                key=_make_key("analyze", fund_code, unique_id),
                use_container_width=True,
                help=f"AI分析 {fund_name}"
            )


def render_fund_grid(funds: List[Dict], columns: int = 2, context: str = "grid"):
    """
    以网格形式渲染多个基金卡片
    
    Args:
        funds: 基金信息列表
        columns: 每行列数（默认2）
        context: 上下文标识，用于生成唯一key（如 "portfolio", "search", "dashboard"）
    
    示例:
        render_fund_grid(my_funds, columns=3, context="portfolio")
    """
    if not funds:
        st.info("📭 暂无基金数据")
        return
    
    cols = st.columns(columns)
    for idx, fund in enumerate(funds):
        with cols[idx % columns]:
            # 传入 context + idx 确保每个卡片 key 唯一
            render_fund_card(
                fund, 
                show_actions=True,
                unique_id=f"{context}_{idx}"
            )
"""
侧边栏组件
左侧导航 + 持仓概览 + 市场指数

使用方式:
    from ui_components.sidebar import render_sidebar
    
    # 在 web_agent.py 或 app.py 中调用（通常放在 st.sidebar 区域）
    render_sidebar()

注意事项:
    - 调用方应确保 st.session_state.page 已初始化
    - 导航按钮使用唯一 key（nav_xxx），避免 Streamlit 组件冲突
    - 大盘指数和热门板块加载失败不阻塞侧边栏
    - 所有异常通过 logging 记录，不静默忽略
"""
import streamlit as st
import logging

logger = logging.getLogger(__name__)

from ui_components.holdings_card import render_holdings_card
from ui_components.market_indicator import render_market_index, render_hot_sectors


def render_sidebar():
    """
    渲染侧边栏（品牌区 + 持仓概览 + 导航菜单 + 大盘指数 + 热门板块）
    
    数据流:
        render_holdings_card(compact=True)   -> data.database.load_funds()
        render_market_index()                -> data.market_api
        render_hot_sectors()                 -> data.sentiment_api
    
    DOM安全:
        - 所有 st.button 使用 nav_xxx 唯一 key
        - 无 markdown 嵌套 Streamlit 组件
    
    错误处理:
        - 指数/板块加载失败：记录 logger.warning，不阻塞侧边栏
        - 持仓加载失败：由 render_holdings_card 内部处理，显示友好提示
    """
    # ========== 品牌区 ==========
    st.sidebar.markdown("## 💰 基金小助手")
    st.sidebar.caption("🤖 AI 已配置 | 数据已就绪")
    st.sidebar.markdown("---")

    # ========== 持仓概览（使用可复用组件） ==========
    render_holdings_card(compact=True)
    st.sidebar.markdown("---")

    # ========== 功能导航 ==========
    nav_groups = {
        "📋 我的资产": [
            ("📊 资产总览", "dashboard"),
            ("💼 我的持仓", "portfolio"),
            ("📝 交易记录", "diary"),
            ("💰 定投管理", "dingtou"),
        ],
        "📈 行情分析": [
            ("🔍 基金搜索", "fund_search"),
            ("📉 市场行情", "market"),
            ("📊 对比分析", "compare"),
        ],
        "🧰 投资工具": [
            ("📐 定投计算器", "dingtou_calc"),
            ("⚡ 回测工具", "backtest"),
        ],
        "🤖 AI 助手": [
            ("💬 AI 对话", "ai_chat"),
            ("📋 持仓分析", "analysis"),
        ],
        "📱 股票专区": [
            ("⭐ 自选股", "watchlist"),
            ("🔎 股票搜索", "stock_search"),
            ("📋 持仓股票", "stock_holdings"),
        ],
        "🔬 智能分析": [
            ("🩺 综合诊断", "stock_diagnosis"),
            ("🌡️ 市场全景", "stock_market_overview"),
            ("🧰 投资工具箱", "stock_tools"),
        ],
        "⚙️ 系统": [
            ("🔔 预警设置", "alert"),
            ("👤 个人中心", "profile"),
            ("⚙️ 系统设置", "settings"),
        ],
    }

    for group_name, items in nav_groups.items():
        st.sidebar.markdown("**{}**".format(group_name))
        for btn_label, page_key in items:
            if st.sidebar.button(btn_label, key="nav_{}".format(page_key), use_container_width=True):
                st.session_state.page = page_key
                st.rerun()
        st.sidebar.markdown("")

    st.sidebar.markdown("---")

    # ========== 大盘指数（使用可复用组件） ==========
    try:
        render_market_index()
        st.sidebar.markdown("---")
    except Exception as e:
        logger.warning("渲染大盘指数失败: {}".format(e))

    # ========== 热门板块（使用可复用组件） ==========
    try:
        render_hot_sectors()
    except Exception as e:
        logger.warning("渲染热门板块失败: {}".format(e))

    # ========== 底部 ==========
    st.sidebar.markdown("---")
    st.sidebar.caption("v2.0 | 基金小助手 © 2024")
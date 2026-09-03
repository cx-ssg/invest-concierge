# -*- coding: utf-8 -*-
"""
侧边栏组件（双轨导航 v1.0）
主内容区顶部右侧双轨切换 + 左侧导航（按轨道渲染）+ 持仓概览 + 市场指数

轨道归属结构（21 页全量，同时是 ROADMAP 生成源）与渲染解耦：
    渲染与否由 PAGE_META 中各页的 live 标志决定（live=true 才进导航渲染）。
    占位页只隐藏不删除——live=false 一律不进导航（不渲染 = 不存在）。

v1.0 渲染集（live=true，只渲染真实可跑页）:
    - 基金轨 3 页：dashboard(资产总览) / portfolio(我的持仓) / diary(投资日记)
    - 股票轨 1 页：stock_diagnosis(综合诊断)
    - 通用 2 页：ai_chat(AI 对话) / settings(系统设置)
    - profile / alert live=false 不渲染（进 v1.1）

定案依据：D:/Vault/Handoff/fund_agent-融合版计划-给zcode-20260830.md §6（用户 2026-08-30 拍板）。

使用方式:
    from ui_components.sidebar import render_sidebar
    render_sidebar()  # 在 web_agent.py 或 app.py 中调用

注意事项:
    - 调用方应确保 st.session_state.page 已初始化
    - 顶部轨道切换使用 st.segmented_control（Streamlit 1.58 实测支持）：
      切换仅更新 st.session_state.track 并 st.rerun()，侧边栏据此按轨渲染
    - 导航按钮使用唯一 key（nav_xxx），避免 Streamlit 组件冲突
    - 大盘指数和热门板块加载失败不阻塞侧边栏
    - 所有异常通过 logging 记录，不静默忽略
"""
import logging

import streamlit as st

from config import API_KEY
from data.market_api import get_market_index, get_hot_sectors
from services.nav_service import (
    PAGE_META,
    TRACK_OPTIONS,
    get_live_pages,
    get_live_page_keys,
)
from services.nav_service import _TRACK_LABELS  # 侧栏轨道切换的显示名（唯一权威在 nav_service）
from ui_components.holdings_card import render_holdings_card
from ui_components.market_indicator import render_market_index, render_hot_sectors
from utils.common import fetch_all_with_timeout

logger = logging.getLogger(__name__)

# ==================== 轨道归属结构（21 页全量 = ROADMAP 生成源） ====================
# M0：PAGE_META/TRACK_OPTIONS/get_live_pages 已抽到 services/nav_service.py（唯一权威，
# 服务端可独立 import）；本模块 re-export 保持旧引用兼容。

# 侧边栏市场数据的最大等待秒数（超时即降级跳过，不阻塞整页——
# 数据源被代理拦截/弱网时，否则首屏可能白等 1-2 分钟）
SIDEBAR_FETCH_TIMEOUT = 8


# ==================== 顶部双轨切换（主内容区右上角） ====================

def render_track_switcher():
    """
    双轨切换（📊 基金 / 📈 股票）——固定悬浮在屏幕最右上角。

    Streamlit 给组件容器打 st-key-track_switcher 类名（1.58+），
    styles.py 用它把整个切换器 fixed 到视口右上角，不占内容区行位。
    切换仅更新 st.session_state.track 并 st.rerun()，侧边栏据此按轨渲染。
    """
    if "track" not in st.session_state:
        st.session_state.track = "fund"
    current = st.session_state.track

    selected = st.segmented_control(
        "投资轨道",
        options=list(TRACK_OPTIONS),
        format_func=lambda k: _TRACK_LABELS[k],
        default=current,
        key="track_switcher",
        label_visibility="collapsed",
        help="切换「📊 基金」/「📈 股票」专栏",
    )

    if selected is not None and selected != current:
        st.session_state.track = selected
        st.rerun()


# ==================== 侧边栏按轨导航 ====================

def _render_nav_item(label, page_key, active=False):
    """渲染单个导航按钮；点击后写入 session_state.page 并 rerun。

    active=True 走 primary 变体（CSS 在侧边栏作用域内渲染为金色选中态）。
    """
    btn_type = "primary" if active else "secondary"
    if st.sidebar.button(label, key="nav_{}".format(page_key), use_container_width=True, type=btn_type):
        st.session_state.page = page_key
        st.rerun()


def render_navigation():
    """按当前轨道渲染导航：当前轨道专区（live 页）+ 通用专区（两轨共享 live 页）。

    占位页（live=false）一律不渲染 = 不进导航。profile/alert 在结构里但 live=false。
    当前页对应按钮走 active 金色态（纯展示，不影响路由逻辑）。
    ai_chat 自身不进导航（它是默认首页，且页面内已提供会话/返回入口）。
    """
    track = st.session_state.get("track", "fund")
    current = st.session_state.get("page")

    # 当前轨道专区（分组标签：去掉轨道 emoji，纯文字小标签）
    st.sidebar.markdown(
        '<div class="nav-group">{}专区</div>'.format(
            PAGE_META[track]["label"].split(" ", 1)[-1]),
        unsafe_allow_html=True)
    for key, label in get_live_pages(track):
        if key == "ai_chat":
            continue
        _render_nav_item(label, key, active=(key == current))

    # 通用专区（两轨共享：ai_chat 已在上方跳过，这里只出 settings）
    common_pages = [(k, l) for k, l in get_live_pages("common") if k != "ai_chat"]
    if common_pages:
        st.sidebar.markdown('<div class="nav-group">通 用</div>', unsafe_allow_html=True)
        for key, label in common_pages:
            _render_nav_item(label, key, active=(key == current))


def render_sidebar():
    """
    渲染侧边栏。

    对话中心（ai_chat）：左栏 = 品牌 + 状态 + 会话历史（会话由 pages/ai_chat 渲染），
    对齐参考图「会话在左」；品牌/导航/持仓/行情不进入，导航改由页面右栏功能面板承载。

    其余页面：品牌区 + 持仓概览 + 按轨导航 + 大盘指数 + 热门板块（原布局不变）。

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
    # ========== 顶部轨道切换（CSS 固定到屏幕最右上角，全页共用） ==========
    render_track_switcher()

    # ========== 品牌区 ==========
    st.sidebar.markdown(
        '<div class="brand-row"><div class="brand-seal">私</div>'
        '<div><div class="brand-name">投资私人管家</div>'
        '<div class="brand-en">INVEST&nbsp;CONCIERGE</div></div></div>',
        unsafe_allow_html=True)
    if API_KEY:
        ai_status = '<span><span class="status-dot"></span>AI 已配置</span>'
    else:
        ai_status = '<span><span class="status-dot off"></span>AI 未配置</span>'
    st.sidebar.markdown(
        '<div class="status-row">{}'
        '<span><span class="status-dot"></span>数据源已连接</span></div>'.format(ai_status),
        unsafe_allow_html=True)

    # ========== 对话中心：左栏到此为止，会话历史由 ai_chat 接续渲染 ==========
    if st.session_state.get("page") == "ai_chat":
        st.sidebar.markdown("---")
        return

    # ========== 持仓概览（使用可复用组件；显式进入侧边栏上下文，
    # 否则组件内部的 st.* 会渲染到主区） ==========
    with st.sidebar:
        render_holdings_card(compact=True)
    st.sidebar.markdown("---")

    # ========== 功能导航（按轨道渲染） ==========
    render_navigation()
    st.sidebar.markdown("---")

    # ========== 大盘指数 + 热门板块（并发预取，总等待上限 8s；失败/超时快速降级） ==========
    idx_data, sector_data = fetch_all_with_timeout(
        [get_market_index, get_hot_sectors], timeout=SIDEBAR_FETCH_TIMEOUT)
    try:
        with st.sidebar:
            if idx_data:
                render_market_index(data=idx_data)
            else:
                st.caption("📈 行情加载慢或不可用，已跳过")
        st.sidebar.markdown("---")
    except Exception as e:
        logger.warning("渲染大盘指数失败: {}".format(e))

    # ========== 热门板块（同上） ==========
    try:
        with st.sidebar:
            if sector_data:
                render_hot_sectors(data=sector_data)
            else:
                st.caption("🔥 板块数据加载慢或不可用，已跳过")
    except Exception as e:
        logger.warning("渲染热门板块失败: {}".format(e))

    # ========== 底部 ==========
    st.sidebar.markdown("---")
    st.sidebar.caption("v1.0 · invest-concierge © 2026")
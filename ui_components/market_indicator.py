"""
市场指标组件
展示大盘指数、板块热度等市场信息

颜色口径：A股习惯 红涨绿跌（涨 = var(--up) 红 / 跌 = var(--down) 绿），
与 utils/common.get_color_by_change 保持一致。
"""
import streamlit as st
from typing import List, Dict
from data.market_api import get_market_index, get_hot_sectors

_UP = "var(--up)"      # 涨·红
_DOWN = "var(--down)"  # 跌·绿


def render_market_index():
    """渲染大盘指数列表（行式布局，红涨绿跌）"""
    indices = get_market_index()

    if not indices:
        st.warning("无法获取市场指数")
        return

    st.markdown("### 📈 大盘指数")

    # 兼容 list 或 DataFrame 输入
    items = indices if isinstance(indices, list) else (indices.to_dict('records') if hasattr(indices, 'to_dict') else [])

    for idx in items:
        change = idx.get('change', 0)
        change_pct = idx.get('change_pct', idx.get('change_percent', 0))
        # A股口径：红涨绿跌
        color = _UP if change >= 0 else _DOWN
        sign = "+" if change >= 0 else ""

        st.markdown("""
        <div class="row-item">
            <div>
                <div style="font-size:12.5px;color:var(--text-2);">{name}</div>
                <div style="font-size:10.5px;color:var(--text-3);margin-top:1px;">{code}</div>
            </div>
            <div style="text-align:right;">
                <div style="font-size:13px;font-weight:600;color:var(--text-1);" class="num">{price:,.2f}</div>
                <div style="font-size:11px;margin-top:1px;color:{color};" class="num">{sign}{change:,.2f}（{sign}{change_pct:.2f}%）</div>
            </div>
        </div>
        """.format(
            name=idx.get('name', ''),
            code=idx.get('code', ''),
            price=idx.get('price', 0),
            color=color,
            sign=sign,
            change=change,
            change_pct=change_pct,
        ), unsafe_allow_html=True)


def render_hot_sectors():
    """渲染热门板块（红涨绿跌）"""
    sectors = get_hot_sectors()

    if not sectors:
        return

    st.markdown("### 🔥 热门板块")

    items = sectors if isinstance(sectors, list) else (sectors.to_dict('records') if hasattr(sectors, 'to_dict') else [])

    for sector in items[:5]:
        change_pct = sector.get('change_pct', sector.get('change', 0))
        # A股口径：红涨绿跌
        color = _UP if change_pct >= 0 else _DOWN
        sign = "+" if change_pct >= 0 else ""

        st.markdown("""
        <div class="row-item" style="padding:7px 8px;">
            <span style="font-size:12.5px;color:var(--text-2);">{name}</span>
            <span style="font-size:12.5px;font-weight:600;color:{color};" class="num">{sign}{change_pct:.2f}%</span>
        </div>
        """.format(
            name=sector.get('name', ''),
            color=color,
            sign=sign,
            change_pct=change_pct,
        ), unsafe_allow_html=True)


def render_market_overview():
    """渲染市场概览（完整版）"""
    col1, col2 = st.columns(2)

    with col1:
        render_market_index()

    with col2:
        render_hot_sectors()

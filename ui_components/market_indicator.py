"""
市场指标组件
展示大盘指数、板块热度等市场信息
"""
import streamlit as st
from typing import List, Dict
from data.market_api import get_market_index, get_hot_sectors


def render_market_index():
    """渲染大盘指数卡片"""
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
        # A股习惯：涨绿跌红
        color = "#2ecc71" if change >= 0 else "#e74c3c"
        sign = "+" if change >= 0 else ""
        
        st.markdown(f"""
        <div style="display: flex; justify-content: space-between; align-items: center;
                    padding: 10px; margin: 4px 0; background: #2A2A2A; border-radius: 8px;">
            <div>
                <div style="font-size: 14px; color: #CCC;">{idx.get('name', '')}</div>
                <div style="font-size: 11px; color: #666;">{idx.get('code', '')}</div>
            </div>
            <div style="text-align: right;">
                <div style="font-size: 16px; font-weight: bold; color: #FFF;">
                    {idx.get('price', 0):,.2f}
                </div>
                <div style="font-size: 12px; color: {color};">
                    {sign}{change:,.2f} ({sign}{change_pct:.2f}%)
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)


def render_hot_sectors():
    """渲染热门板块"""
    sectors = get_hot_sectors()
    
    if not sectors:
        return
    
    st.markdown("### 🔥 热门板块")
    
    items = sectors if isinstance(sectors, list) else (sectors.to_dict('records') if hasattr(sectors, 'to_dict') else [])
    
    for sector in items[:5]:
        change_pct = sector.get('change_pct', sector.get('change', 0))
        # A股习惯：涨绿跌红
        color = "#2ecc71" if change_pct >= 0 else "#e74c3c"
        sign = "+" if change_pct >= 0 else ""
        
        st.markdown(f"""
        <div style="display: flex; justify-content: space-between; align-items: center;
                    padding: 8px; margin: 2px 0; border-radius: 6px;
                    background: #2A2A2A;">
            <span style="font-size: 13px; color: #CCC;">{sector.get('name', '')}</span>
            <span style="font-size: 13px; font-weight: bold; color: {color};">
                {sign}{change_pct:.2f}%
            </span>
        </div>
        """, unsafe_allow_html=True)


def render_market_overview():
    """渲染市场概览（完整版）"""
    col1, col2 = st.columns(2)
    
    with col1:
        render_market_index()
    
    with col2:
        render_hot_sectors()
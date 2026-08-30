# -*- coding: utf-8 -*-
"""
资产总览页面
"""
import streamlit as st
from ui_components.holdings_card import render_holdings_card


def main():
    st.title("📊 资产总览")
    st.markdown("### 基金资产概览")
    
    # 使用已有的持仓卡片组件
    render_holdings_card(compact=False)
    
    # 风险提示
    st.markdown("---")
    st.caption("⚠️ 风险提示：本工具仅供个人投资参考，不构成投资建议。")


if __name__ == "__main__":
    main()
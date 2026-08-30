# -*- coding: utf-8 -*-
"""
我的持仓页面 - 基金持仓管理
"""
import streamlit as st
from ui_components.holdings_card import render_holdings_card


def main():
    st.title("💼 我的持仓")
    st.markdown("### 基金持仓明细")
    
    render_holdings_card(compact=False)
    
    st.markdown("---")
    st.caption("⚠️ 风险提示：本工具仅供个人投资参考，不构成投资建议。")


if __name__ == "__main__":
    main()
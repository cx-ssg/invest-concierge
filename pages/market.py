# -*- coding: utf-8 -*-
"""
市场概览页面 - 基金市场行情
"""
import streamlit as st
from data.market_api import get_market_index


def main():
    st.title("📈 市场概览")
    st.markdown("### 大盘指数行情")
    st.info("市场数据加载中，敬请期待！")
    st.markdown("---")
    st.caption("⚠️ 风险提示：本工具仅供个人投资参考，不构成投资建议。")


if __name__ == "__main__":
    main()
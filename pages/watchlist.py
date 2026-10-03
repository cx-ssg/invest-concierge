# -*- coding: utf-8 -*-
"""
自选股页面（Streamlit 遗留入口）。

H5（2026-10-04）起自选股由 React 前端承载（桌面壳/浏览器同源 /stock/watchlist，
含「自选股 / 持仓股票」两个 tab，复用库层 watchlist / stock_holdings CRUD）；
本页仅保留旧 Streamlit 入口的路由兼容。
"""
import streamlit as st


def main():
    st.title("⭐ 自选股")
    st.markdown("### 自选股 / 持仓股票")
    st.info("自选股已在新界面（桌面版/网页版）上线：请在左侧导航打开「自选股」页，"
            "支持按代码/名称搜索加入、实时行情、持仓记录与浮动盈亏。")
    st.markdown("---")
    st.caption("⚠️ 风险提示：本工具仅供个人投资参考，不构成投资建议。")


if __name__ == "__main__":
    main()

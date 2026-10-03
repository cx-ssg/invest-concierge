# -*- coding: utf-8 -*-
"""
市场行情页（Streamlit 遗留入口）。

H5（2026-10-04）起市场行情由 React 前端承载（桌面壳/浏览器同源 /fund/market，
含指数/板块/情绪/资金/估值五个 tab）；本页仅保留旧 Streamlit 入口的路由兼容。
"""
import streamlit as st


def main():
    st.title("📉 市场行情")
    st.markdown("### 指数 / 板块 / 情绪 / 资金 / 估值")
    st.info("市场行情已在新界面（桌面版/网页版）上线：请在左侧导航打开「市场行情」页，"
            "五个 tab 都是真实行情（与 AI 对话里的工具同一批数据源）。")
    st.markdown("---")
    st.caption("⚠️ 风险提示：本工具仅供个人投资参考，不构成投资建议。")


if __name__ == "__main__":
    main()

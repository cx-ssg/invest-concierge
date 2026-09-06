# -*- coding: utf-8 -*-
"""
预警页面（Streamlit 遗留入口）。

v1.1 起预警功能由 React 前端「价格预警」页承载（桌面壳/浏览器同源 /alerts）；
本页仅保留旧 Streamlit 入口的路由兼容，指向新界面。
"""
import streamlit as st


def main():
    st.title("🔔 价格预警")
    st.markdown("### 投资预警通知管理")
    st.info("预警功能已在新界面（桌面版/网页版）上线：请在左侧导航打开「价格预警」页设置规则，"
            "桌面壳触发时会弹托盘提醒。")
    st.markdown("---")
    st.caption("⚠️ 风险提示：本工具仅供个人投资参考，不构成投资建议。")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
import streamlit as st

# Streamlit 页面配置必须在第一行执行
st.set_page_config(
    page_title="基金/股票智能Agent",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

from web_agent import main  # 导入主路由逻辑

if __name__ == "__main__":
    main()
# -*- coding: utf-8 -*-
"""
系统设置页面
"""
import streamlit as st
from config import API_KEY, DEEPSEEK_API_BASE, DEEPSEEK_MODEL


def main():
    st.title("⚙️ 系统设置")
    st.markdown("### 应用配置")
    
    # API 配置
    with st.container():
        st.markdown("#### 🤖 AI 配置")
        api_key_status = "✅ 已配置" if API_KEY else "❌ 未配置"
        st.text_input("DeepSeek API Key", value=API_KEY or "", type="password", disabled=True,
                      help="当前状态：" + api_key_status)
        st.text_input("API 地址", value=DEEPSEEK_API_BASE, disabled=True)
        st.text_input("模型名称", value=DEEPSEEK_MODEL, disabled=True)
    
    st.markdown("---")
    
    # 数据管理
    with st.container():
        st.markdown("#### 📊 数据管理")
        st.info("数据管理功能即将上线，敬请期待！")
    
    st.markdown("---")
    st.caption("⚠️ 风险提示：本工具仅供个人投资参考，不构成投资建议。")


if __name__ == "__main__":
    main()
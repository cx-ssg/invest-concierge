# -*- coding: utf-8 -*-
"""
AI 对话页面 - 智能投资助手
"""
import streamlit as st
from utils.ai_helper import call_llm


def main():
    st.title("💬 AI 对话")
    st.markdown("### 智能投资助手")
    
    # 初始化聊天历史
    if "messages" not in st.session_state:
        st.session_state.messages = []
    
    # 显示历史消息
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
    
    # 输入框
    prompt = st.chat_input("请输入您的问题")
    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        
        with st.chat_message("assistant"):
            with st.spinner("思考中..."):
                try:
                    result = call_llm(prompt)
                    response = result.get("content", "")
                    st.markdown(response)
                    st.session_state.messages.append({"role": "assistant", "content": response})
                except Exception as e:
                    st.error("AI 响应失败：{}".format(e))
    
    st.markdown("---")
    st.caption("⚠️ 风险提示：AI 分析仅供参考，不构成投资建议。")


if __name__ == "__main__":
    main()
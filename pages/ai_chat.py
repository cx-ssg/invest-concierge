# -*- coding: utf-8 -*-
"""
AI 对话页面 - 智能投资助手（v2）
- 接入工具调用：查基金 get_fund_info / 查大盘 get_market_index / 查持仓 load_funds
- 无 Key 降级：显示「配置 Key 即可 AI 分析」引导卡，不崩溃
- 演示模式：侧边栏开关预置示例持仓（纯内存 seed，不写数据库、不改表结构）
"""
import json

import streamlit as st

from config import API_KEY
from utils.ai_helper import AI_TOOLS, build_system_prompt, chat_with_tools

EXAMPLE_QUESTIONS = [
    "我的持仓怎么样？",
    "帮我查一下 161725 这只基金",
    "今天大盘表现如何？",
]


def _init_chat_history():
    if "messages" not in st.session_state:
        st.session_state.messages = []


def _system_message():
    """构建系统提示词（按演示模式缓存两份，切换后下次发送自动重建）"""
    cache_key = "ai_sys_prompt_demo" if st.session_state.get("use_demo_funds") else "ai_sys_prompt_real"
    if cache_key not in st.session_state:
        with st.spinner("正在准备 AI 上下文…"):
            st.session_state[cache_key] = build_system_prompt()
    return {"role": "system", "content": st.session_state[cache_key]}


def _build_messages():
    return [_system_message()] + list(st.session_state.messages)


def _render_messages():
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])


def _render_no_key_guide():
    """无 Key 降级：引导卡（不崩溃，聊天框禁用）"""
    st.markdown(
        '<div class="card-gold"><div class="card-title">🔑 配置 Key 即可 AI 分析</div>'
        '<div style="font-size:13.5px;color:var(--text-2);line-height:1.9;">'
        '接入 DeepSeek 后，AI 能<strong>调用工具</strong>实时查询基金行情、大盘指数和你的持仓，'
        '再给出更有数据支撑的分析。</div></div>',
        unsafe_allow_html=True,
    )
    st.markdown("#### 如何开启 AI 分析")
    st.markdown(
        "1. 在项目目录复制 `local_env.bat.example` 为 `local_env.bat`，填入你的 DeepSeek API Key\n"
        "2. 重新启动应用（`python -m streamlit run app.py`）\n"
        "3. 回到本页即可开始对话"
    )
    if st.button("前往系统设置查看 Key 状态", key="ai_goto_settings"):
        st.session_state.page = "settings"
        st.rerun()
    st.markdown("#### 💡 示例问题（配置后即可提问）")
    for q in EXAMPLE_QUESTIONS:
        st.markdown("- " + q)
    st.chat_input("请先配置 DeepSeek API Key 后再提问", disabled=True)


def _handle_prompt(prompt):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("AI 思考中…"):
            try:
                result = chat_with_tools(_build_messages(), tools=AI_TOOLS)
            except Exception as e:
                st.error("AI 响应失败：{}".format(e))
                return

        # 工具调用过程展示（可折叠）
        tool_trace = result.get("tool_trace") or []
        if tool_trace:
            with st.expander("🔧 本次调用工具 {} 次".format(len(tool_trace)), expanded=False):
                for t in tool_trace:
                    args_text = json.dumps(t.get("arguments") or {}, ensure_ascii=False)
                    st.markdown("**{}**　`{}`".format(t.get("name", ""), args_text))
                    st.code(str(t.get("output", ""))[:800], language="json")

        response = result.get("content") or ""
        if response:
            st.markdown(response)
            st.session_state.messages.append({"role": "assistant", "content": response})


def main():
    st.title("💬 AI 对话")
    st.markdown("### 智能投资助手")

    # 演示模式：内置示例持仓（纯内存 seed，点击即生效，不写数据库）
    with st.sidebar:
        st.markdown("#### 🧪 演示模式")
        st.checkbox(
            "用示例持仓做 AI 分析",
            key="use_demo_funds",
            help="开启后 AI 将基于内置示例持仓（白酒/消费/沪深300）回答问题，"
                 "适合还没有持仓数据的新人体验；不会写入真实数据库。",
        )

    _init_chat_history()

    if not API_KEY:
        _render_no_key_guide()
        st.markdown("---")
        st.caption("⚠️ 风险提示：AI 分析仅供参考，不构成投资建议。")
        return

    _render_messages()

    prompt = st.chat_input("请输入您的问题，例如：我的持仓怎么样？")
    if prompt:
        _handle_prompt(prompt)

    st.markdown("---")
    st.caption("⚠️ 风险提示：AI 分析仅供参考，不构成投资建议。")


if __name__ == "__main__":
    main()
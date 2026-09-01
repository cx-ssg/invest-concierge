# -*- coding: utf-8 -*-
"""
Agent 对话中心（Agent-first 主页面 · 打开即对话）
布局：中央对话主体 + 左侧会话历史（侧边栏）+ 右侧数据面板（持仓/指数/记忆）

- 对话走 agent_run（11 工具规划循环 + 记忆落库 + 追问链）
- 会话历史来自 agent_sessions（SQLite），点击续聊 = 从 agent_messages 重建
- 无 Key 降级：引导卡 + 聊天框禁用，不崩溃
- 演示模式：预置示例持仓（纯内存 seed）
"""
import json

import streamlit as st

from config import API_KEY
from utils.agent_core import agent_run
from utils.agent_memory import list_agent_sessions, build_memory_context
from ui_components.holdings_card import render_holdings_card
from ui_components.market_indicator import render_market_index

EXAMPLE_QUESTIONS = [
    "我的持仓怎么样",
    "帮我诊断一下 600519",
    "今天大盘表现如何",
]


def _init_state():
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "agent_session_id" not in st.session_state:
        st.session_state.agent_session_id = None
    if "agent_view" not in st.session_state:
        st.session_state.agent_view = []  # 当前视口消息（可能来自历史会话）


# ==================== 会话历史（侧边栏） ====================


def _render_session_sidebar():
    """侧边栏：新建对话 + 最近会话列表（点击续聊）"""
    st.sidebar.markdown('<div class="nav-group">会 话 历 史</div>', unsafe_allow_html=True)
    if st.sidebar.button("＋ 新建对话", key="agent_new_session", use_container_width=True,
                         type="primary" if st.session_state.get("agent_session_id") is None else "secondary"):
        st.session_state.agent_session_id = None
        st.session_state.agent_view = []
        st.session_state.messages = []
        st.rerun()

    sessions = list_agent_sessions(limit=8)
    for s in sessions:
        title = (s.get("title") or "未命名会话")[:16]
        sid = s.get("id")
        current = st.session_state.get("agent_session_id") == sid
        label = ("🟡 " if current else "💬 ") + title
        if st.sidebar.button(label, key="agent_sess_{}".format(sid), use_container_width=True):
            st.session_state.agent_session_id = sid
            msgs = []
            for m in _session_display_messages(sid):
                msgs.append({"role": m["role"], "content": m["content"]})
            st.session_state.agent_view = msgs
            st.session_state.messages = msgs
            st.rerun()
    if not sessions:
        st.sidebar.caption("还没有会话记录")


def _session_display_messages(session_id, limit=40):
    """从 SQLite 重建会话的可展示消息（只取 user/assistant 文本，跳过 tool 审计行）"""
    from utils.agent_memory import get_agent_messages
    raw = get_agent_messages(session_id, limit=limit)
    out = []
    for m in raw:
        role = m.get("role")
        content = str(m.get("content") or "")
        if role in ("user", "assistant") and content:
            out.append({"role": role, "content": content})
    return out


# ==================== 右侧数据面板 ====================


def _render_right_panel():
    """数据右栏：持仓概览 / 大盘指数 / 会话记忆（小标签 + 紧凑）"""
    st.markdown('<div class="panel-label">持仓概览</div>', unsafe_allow_html=True)
    render_holdings_card(compact=True)
    st.markdown('<div class="panel-label">大盘指数</div>', unsafe_allow_html=True)
    try:
        render_market_index()
    except Exception:
        st.caption("暂无数据")
    st.markdown('<div class="panel-label">会话记忆</div>', unsafe_allow_html=True)
    ctx = build_memory_context()
    if ctx:
        st.caption(ctx.replace("最近会话记忆：\n", ""))
    else:
        st.caption("暂无记忆——对话满 8 轮后自动生成摘要")


# ==================== 消息渲染 ====================


def _render_view():
    view = st.session_state.get("agent_view") or st.session_state.get("messages") or []
    for msg in view:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])


def _render_no_key_guide():
    """无 Key 降级：引导卡（不崩溃，聊天框禁用）"""
    st.markdown(
        '<div class="card-gold"><div class="card-title">🔑 配置 Key 即可唤醒 Agent</div>'
        '<div style="font-size:13.5px;color:var(--text-2);line-height:1.9;">'
        '接入 DeepSeek 后，Agent 能<strong>自主调用 11 个数据工具</strong>——查持仓、诊断个股、'
        '看大盘、读日记——多步规划后给出有数据支撑的结论。</div></div>',
        unsafe_allow_html=True,
    )
    st.markdown("#### 如何开启 AI 分析")
    st.markdown(
        "1. 在项目目录复制 `local_env.bat.example` 为 `local_env.bat`，填入你的 DeepSeek API Key\n"
        "2. 重新启动应用（`python -m streamlit run app.py`）\n"
        "3. 回到本页即可开始对话"
    )
    if st.button("前往系统设置查看 Key 状态", key="agent_goto_settings"):
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
        with st.spinner("Agent 规划与调用工具中…（多步查询约 30-90 秒）"):
            try:
                result = agent_run(
                    prompt,
                    memory=True,
                    session_id=st.session_state.get("agent_session_id"),
                    continue_question=True,
                )
            except Exception as e:
                st.error("AI 响应失败：{}".format(e))
                return
            if result.get("session_id"):
                st.session_state.agent_session_id = result["session_id"]

            # 工具调用时间线（内联在回复上方；步数少时默认展开）
            tool_trace = result.get("tool_trace") or []
            if tool_trace:
                with st.expander(
                        "🔧 Agent 自主调用了 {} 次工具".format(len(tool_trace)),
                        expanded=len(tool_trace) <= 3):
                    for t in tool_trace:
                        args_text = json.dumps(t.get("arguments") or {}, ensure_ascii=False)
                        st.markdown("**{}**　`{}`".format(t.get("name", ""), args_text))
                        st.code(str(t.get("output", ""))[:800], language="json")

            response = result.get("content") or ""
            if response:
                st.markdown(response)
                st.session_state.messages.append({"role": "assistant", "content": response})
                if st.session_state.get("agent_view") is not None:
                    st.session_state.agent_view = list(st.session_state.messages)


# ==================== 主流程 ====================


def main():
    # 演示模式开关 + 会话历史（侧边栏）
    with st.sidebar:
        st.markdown("#### 🧪 演示模式")
        st.checkbox(
            "用示例持仓做 AI 分析",
            key="use_demo_funds",
            help="开启后 Agent 将基于内置示例持仓（白酒/消费/沪深300）回答问题，"
                 "适合还没有持仓数据的新人体验；不会写入真实数据库。",
        )
    _render_session_sidebar()

    _init_state()

    if not API_KEY:
        _render_no_key_guide()
        st.caption("⚠️ 风险提示：AI 分析仅供参考，不构成投资建议。")
        return

    # 中央对话主体 + 右侧数据面板
    col_main, col_side = st.columns([3, 1.1], gap="medium")

    with col_main:
        view = st.session_state.get("agent_view") or []
        if not view:
            # 新会话空状态：一行主文案 + 横排紧凑建议 chips（点击即问）
            st.markdown(
                '<div class="empty-state" style="padding:34px 10px 16px;">'
                '<div class="es-icon">🤖</div>'
                '<div class="es-title">有什么可以帮你？</div>'
                '<div class="es-hint">查持仓 · 诊断个股 · 看大盘 · 读日记</div></div>',
                unsafe_allow_html=True,
            )
            c1, c2, c3 = st.columns(3, gap="small")
            for col, q, key in ((c1, EXAMPLE_QUESTIONS[0], "chip_a"),
                                (c2, EXAMPLE_QUESTIONS[1], "chip_b"),
                                (c3, EXAMPLE_QUESTIONS[2], "chip_c")):
                with col:
                    if st.button(q, key=key, use_container_width=True):
                        _handle_prompt(q)
                        st.rerun()
        else:
            _render_view()

    with col_side:
        _render_right_panel()

    prompt = st.chat_input("请输入问题，例如：帮我诊断一下 600519")
    if prompt:
        _handle_prompt(prompt)
        st.rerun()

    st.caption("⚠️ 风险提示：AI 分析仅供参考，不构成投资建议。工具数据来自 AkShare，可能存在延迟。")


if __name__ == "__main__":
    main()

# -*- coding: utf-8 -*-
"""
Agent 对话中心（Agent-first 主页面 · 打开即对话）
布局模仿 Reasonix 式 agent 产品：中央对话主体 + 左侧会话列表 + 右侧功能面板

- 对话走 agent_run（11 工具规划循环 + 记忆落库 + 追问链）
- 会话历史来自 agent_sessions（SQLite），点击续聊 = 从 agent_messages 重建
- 左栏（st.sidebar）：新建对话 + 会话列表（本页独占，导航在右栏功能面板）
- 右栏：功能导航（当前轨道页面入口）+ 持仓/指数/记忆数据面板
- 无 Key 降级：引导卡 + 聊天框禁用，不崩溃
- 演示模式：预置示例持仓（纯内存 seed）
"""
import html as html_lib
import json

import streamlit as st

from config import API_KEY
from utils.agent_core import agent_run
from utils.agent_memory import list_agent_sessions, build_memory_context
from utils.common import fetch_with_timeout
from ui_components.holdings_card import render_holdings_card
from ui_components.market_indicator import render_market_index
from data.market_api import get_market_index
from ui_components.sidebar import get_live_pages

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
    """左栏（本页独占）：新建对话 + 最近会话列表（点击续聊），模仿 agent 产品的会话侧栏"""
    if st.sidebar.button("＋ 新建对话", key="agent_new_session", use_container_width=True,
                         type="primary" if st.session_state.get("agent_session_id") is None else "secondary"):
        st.session_state.agent_session_id = None
        st.session_state.agent_view = []
        st.session_state.messages = []
        st.rerun()

    st.sidebar.markdown('<div class="nav-group">会 话 历 史</div>', unsafe_allow_html=True)
    sessions = list_agent_sessions(limit=8)
    for s in sessions:
        title = (s.get("title") or "未命名会话")[:16]
        sid = s.get("id")
        current = st.session_state.get("agent_session_id") == sid
        label = ("🟡 " if current else "") + title
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

    # 底部：演示模式开关（低调节奏，不抢会话列表的视觉主体）
    st.sidebar.markdown("---")
    st.sidebar.checkbox(
        "🧪 演示模式（示例持仓做 AI 分析）",
        key="use_demo_funds",
        help="开启后 Agent 将基于内置示例持仓（白酒/消费/沪深300）回答问题，"
             "适合还没有持仓数据的新人体验；不会写入真实数据库。",
    )
    st.sidebar.caption("v1.0 · invest-concierge © 2026")


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


# ==================== 右侧功能面板 ====================


def _render_right_panel():
    """右栏（功能在右，模仿参考图）：功能导航 + 持仓概览 + 大盘指数 + 会话记忆

    行情走 fetch_with_timeout 预取（上限 8s）：无参直连会在代理拦截数据源时
    把每次 rerun 都拖成 30s+ 重试等待，整页交互卡顿的主因之一。
    """
    # 顶部让位：右上角 fixed 的基金/股票切换胶囊悬浮在本栏上方（44px 防压盖）
    st.markdown('<div style="height:44px;"></div>', unsafe_allow_html=True)
    # 功能导航：当前轨道的 live 页面入口（ai_chat 自身排除），两列小格压缩纵向高度
    st.markdown('<div class="panel-label">功 能</div>', unsafe_allow_html=True)
    track = st.session_state.get("track", "fund")
    nav_pages = [(k, l) for k, l in get_live_pages(track) if k != "ai_chat"]
    common_pages = [(k, l) for k, l in get_live_pages("common") if k != "ai_chat"]
    page_list = nav_pages + common_pages
    grid_cols = st.columns(2)
    for i, (key, label) in enumerate(page_list):
        with grid_cols[i % 2]:
            if st.button(label, key="fn_nav_{}".format(key), use_container_width=True):
                st.session_state.page = key
                st.rerun()

    st.markdown('<div class="panel-label">持仓概览</div>', unsafe_allow_html=True)
    render_holdings_card(compact=True)
    st.markdown('<div class="panel-label">大盘指数</div>', unsafe_allow_html=True)
    idx_data = fetch_with_timeout(get_market_index)
    if idx_data:
        render_market_index(data=idx_data, limit=3)
    else:
        st.caption("暂无数据")
    st.markdown('<div class="panel-label">会话记忆</div>', unsafe_allow_html=True)
    ctx = build_memory_context()
    if ctx:
        txt = ctx.replace("最近会话记忆：\n", "")
        if len(txt) > 90:
            txt = txt[:90] + "…"
        st.caption(txt)
    else:
        st.caption("暂无记忆——对话满 8 轮后自动生成摘要")


# ==================== 消息渲染 ====================


def _md_to_html(text):
    """Markdown → HTML（agent 回复渲染进气泡用）；库缺失时降级纯文本"""
    try:
        import markdown
        return markdown.markdown(text or "", extensions=["fenced_code", "tables"])
    except Exception:  # noqa: BLE001 - 渲染降级，不阻塞
        return html_lib.escape(text or "").replace("\n", "<br/>")


def _render_msg(role, content):
    """消息气泡：user 右对齐（金色调）/ assistant 左对齐——无头像的聊天布局"""
    content = str(content or "")
    if role == "user":
        st.markdown(
            '<div class="msg-row msg-row-user"><div class="msg-bubble msg-user">{}</div></div>'.format(
                html_lib.escape(content)),
            unsafe_allow_html=True)
    else:
        st.markdown(
            '<div class="msg-row msg-row-agent"><div class="msg-bubble msg-agent">{}</div></div>'.format(
                _md_to_html(content)),
            unsafe_allow_html=True)


def _render_trace(payload):
    """思考链折叠块（完成后持久保留）：模型原生思考流 + 工具调用记录"""
    events = payload.get("events") or []
    trace = payload.get("trace") or []
    with st.expander("💭 思考过程（{} 段思考 · {} 次工具）".format(
            sum(1 for k, _ in events if k == "reasoning"), len(trace)), expanded=False):
        for kind, text in events:
            if kind == "reasoning":
                st.markdown(
                    '<div class="think-flow">{}</div>'.format(
                        html_lib.escape(str(text)).replace("\n", "<br/>")),
                    unsafe_allow_html=True)
            elif kind == "tool":
                st.markdown("🔧 `{}`".format(text))
        if trace:
            st.markdown("---")
            for t in trace:
                args_text = json.dumps(t.get("arguments") or {}, ensure_ascii=False)
                st.markdown("**{}**　`{}`".format(t.get("name", ""), args_text))
                st.code(str(t.get("output", ""))[:500], language="json")


def _render_view():
    view = st.session_state.get("agent_view") or st.session_state.get("messages") or []
    for msg in view:
        if msg.get("role") == "trace":
            _render_trace(msg.get("content") or {})
        else:
            _render_msg(msg["role"], msg["content"])


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
    _render_msg("user", prompt)

    # 实时思考链：st.status 逐事件更新——reasoning=模型原生思考流（reasoner），
    # tool=工具调用；不再显示合成进度标签
    events = []

    def _on_progress(stage, detail):
        if stage == "reasoning":
            events.append(("reasoning", detail))
        elif stage == "tool":
            events.append(("tool", detail))
        else:  # writing
            events.append(("act", detail))
        html_parts = []
        for kind, text in events:
            if kind == "reasoning":
                html_parts.append(
                    '<div class="think-flow">{}</div>'.format(
                        html_lib.escape(str(text)).replace("\n", "<br/>")))
            elif kind == "tool":
                html_parts.append(
                    '<div class="think-evt">🔧 <code>{}</code></div>'.format(html_lib.escape(str(text))))
            else:
                html_parts.append('<div class="think-evt">{}</div>'.format(html_lib.escape(str(text))))
        try:
            status.update(label="Agent 思考中…", state="running", expanded=True)
        except Exception:  # noqa: BLE001 - 进度展示失败不影响主流程
            pass
        log.markdown(
            '<div class="think-chain">{}</div>'.format("".join(html_parts)),
            unsafe_allow_html=True)

    try:
        with st.status("Agent 思考中…", expanded=True) as status:
            log = st.empty()
            result = agent_run(
                prompt,
                memory=True,
                session_id=st.session_state.get("agent_session_id"),
                continue_question=True,
                on_progress=_on_progress,
            )
            if result.get("session_id"):
                st.session_state.agent_session_id = result["session_id"]
            status.update(label="✅ 完成 · {} 次工具调用".format(
                len(result.get("tool_trace") or [])),
                state="complete", expanded=False)
    except Exception as e:
        st.error("AI 响应失败：{}".format(e))
        return

    # Agent 回复 + 思考链归档：trace 折叠块在气泡之前，完成后随视图持久保留
    response = result.get("content") or ""
    tool_trace = result.get("tool_trace") or []
    if response:
        trace_msg = {"role": "trace", "content": {"events": list(events), "trace": tool_trace}}
        st.session_state.messages.append(trace_msg)
        st.session_state.messages.append({"role": "assistant", "content": response})
        if st.session_state.get("agent_view") is not None:
            st.session_state.agent_view = list(st.session_state.messages)
        _render_trace(trace_msg["content"])
        _render_msg("assistant", response)


# ==================== 主流程 ====================


def main():
    # 左栏独占：新建对话 + 会话历史 + 底部演示模式（参考图式 agent 会话侧栏）
    _render_session_sidebar()

    _init_state()

    if not API_KEY:
        _render_no_key_guide()
        st.caption("⚠️ 风险提示：AI 分析仅供参考，不构成投资建议。")
        return

    # 中央对话主体 + 右侧功能面板（功能在右）
    # 思考链/气泡必须渲染在 col_main 内（Agent 侧·左）。chat_input 顶层提交后
    # 暂存 pending_prompt 再 rerun，由本列在既有消息之后渲染：
    # - 顶层直接处理 → 状态卡渲染到页面底部全宽（首个 bug）
    # - 列内调 chat_input → 输入框内联卡在页面中部不固定底部（第二个坑）
    pending = st.session_state.pop("pending_prompt", None)

    col_main, col_side = st.columns([5, 1.15], gap="medium")

    with col_main:
        view = st.session_state.get("agent_view") or []
        if not view and not pending:
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
                        st.session_state.pending_prompt = q
                        st.rerun()
        else:
            _render_view()

        if pending:
            _handle_prompt(pending)

    with col_side:
        _render_right_panel()

    # chat_input 顶层调用 = 固定在页面底部（聊天应用标准形态）
    prompt = st.chat_input("请输入问题，例如：帮我诊断一下 600519")
    if prompt:
        st.session_state.pending_prompt = prompt
        st.rerun()

    st.caption("⚠️ 风险提示：AI 分析仅供参考，不构成投资建议。工具数据来自 AkShare，可能存在延迟。")


if __name__ == "__main__":
    main()

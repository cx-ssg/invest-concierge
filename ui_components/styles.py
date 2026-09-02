# -*- coding: utf-8 -*-
"""
全局样式（v2 ·「夜航蓝 × 香槟金」设计系统）
设计文档：UI 升级设计文档 v1（2026-08-30 拍板）

原则：
  1. 唯一强调色 = 香槟金（品牌/选中态/主按钮/激活 tab/focus ring），其余全部中性分层
  2. 层次靠明度分层 + 发丝线边框，静态零阴影
  3. 数字是主角：全局 tabular-nums；A股口径 红涨绿跌（--up 红 / --down 绿）
  4. 动效克制：仅颜色/边框/透明度过渡 160ms；prefers-reduced-motion 全关
"""
from typing import Optional, Dict, List  # 3.7 显式导入
import streamlit as st


def inject_global_css() -> None:
    """注入全局设计系统 CSS（web_agent.main 每次渲染调用）"""
    st.markdown(
        """
<style>
    /* ===== Design Tokens ===== */
    :root {
        --bg: #0C111E;
        --bg-sidebar: #0A0F1A;
        --surface: #121A2B;
        --surface-2: #182338;
        --line: rgba(151,169,199,.14);
        --line-strong: rgba(151,169,199,.28);
        --text-1: #E9EEF7;
        --text-2: #A8B3C7;
        --text-3: #66738C;
        --gold: #D6B36A;
        --gold-hover: #E5C784;
        --gold-soft: rgba(214,179,106,.10);
        --gold-line: rgba(214,179,106,.35);
        --up: #F04438;    /* 涨·红（A股口径） */
        --down: #2DBE64;  /* 跌·绿 */
        --info: #6AA5F8;
        --warn: #E8B34B;
        --danger: #EF4444;
        --font-stack: -apple-system, "Segoe UI", "Microsoft YaHei UI", "PingFang SC", "HarmonyOS Sans SC", sans-serif;
        --ease: cubic-bezier(.2,.6,.3,1);
    }

    /* ===== 全局基础 ===== */
    .stApp, [class*="css"], button, input, textarea, select {
        font-family: var(--font-stack);
    }
    .stApp {
        background-color: var(--bg);
        color: var(--text-1);
    }
    ::selection { background: rgba(214,179,106,.28); }

    .main .block-container {
        max-width: 1200px;
        padding: 1.6rem 2.2rem 3rem;
        margin: 0 auto;
    }
    /* ai_chat 对话中心页由 st.columns([5,1.15]) 自然分流中栏/右栏，走默认满幅流式布局 */

    /* 隐藏 Streamlit 默认元素 */
    #MainMenu { visibility: hidden; }
    footer { visibility: hidden; }
    header { visibility: hidden; }
    [data-testid="stHeader"] { background: transparent; height: 0px; }

    /* ===== 滚动条 ===== */
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb {
        background: rgba(151,169,199,.25);
        border-radius: 3px;
    }
    ::-webkit-scrollbar-thumb:hover { background: rgba(151,169,199,.4); }

    /* ===== 标题层级 ===== */
    h1 { font-size: 26px; font-weight: 700; color: var(--text-1); margin-bottom: .4rem; }
    h2 { font-size: 20px; font-weight: 600; color: var(--text-1); margin-top: 1.5rem; margin-bottom: .9rem; }
    h3 { font-size: 16px; font-weight: 600; color: var(--text-1); margin-top: 1.1rem; margin-bottom: .7rem; }
    h4 { font-size: 14.5px; font-weight: 600; color: var(--text-2); margin-top: 1rem; margin-bottom: .6rem; }

    /* ===== 分割线 ===== */
    hr {
        margin: 1.4rem 0;
        border: none;
        border-top: 1px solid var(--line);
    }
    .stMarkdown hr { border-color: var(--line) !important; }

    /* ===== 卡片 ===== */
    .card {
        background: var(--surface);
        border: 1px solid var(--line);
        border-radius: 14px;
        padding: 20px;
        margin-bottom: 16px;
        transition: border-color .16s var(--ease);
    }
    .card:hover { border-color: var(--line-strong); }
    .card-title {
        font-size: 15px; font-weight: 600; color: var(--text-1);
        margin-bottom: 10px;
    }
    .card-subtitle { font-size: 12.5px; color: var(--text-3); margin-bottom: 12px; }

    /* 金色引导卡（AI 未配置 / 演示模式等引导场景） */
    .card-gold {
        background: var(--gold-soft);
        border: 1px solid var(--gold-line);
        border-radius: 14px;
        padding: 18px 20px;
        margin-bottom: 16px;
    }
    .card-gold .card-title { color: var(--gold); }

    /* ===== 徽章 ===== */
    .badge {
        display: inline-flex; align-items: center; gap: 5px;
        font-size: 12px; font-weight: 600;
        padding: 4px 12px; border-radius: 999px;
        border: 1px solid var(--line);
        color: var(--text-2); background: rgba(151,169,199,.08);
    }
    .badge-gold  { color: var(--gold);   background: var(--gold-soft);           border-color: var(--gold-line); }
    .badge-red   { color: var(--danger);background: rgba(239,68,68,.10);        border-color: rgba(239,68,68,.35); }
    .badge-amber { color: var(--warn);  background: rgba(232,179,75,.10);       border-color: rgba(232,179,75,.35); }
    .badge-orange{ color: #FF922B;      background: rgba(255,146,43,.10);       border-color: rgba(255,146,43,.35); }
    .badge-green { color: var(--down);  background: rgba(45,190,100,.10);       border-color: rgba(45,190,100,.35); }
    .badge-gray  { color: var(--text-3);background: rgba(151,169,199,.08);      border-color: var(--line); }

    /* ===== 侧边栏分组标签（sidebar.py 用） ===== */
    .nav-group {
        font-size: 11px; letter-spacing: 1.5px;
        color: var(--text-3);
        padding: 14px 8px 6px;
    }

    /* 右侧数据面板小标签 */
    .panel-label {
        font-size: 11px; letter-spacing: 1.5px;
        color: var(--text-3);
        margin: 16px 0 6px;
    }

    /* ===== 侧边栏 ===== */
    section[data-testid="stSidebar"] {
        background-color: var(--bg-sidebar);
        border-right: 1px solid var(--line);
    }
    section[data-testid="stSidebar"] .block-container { padding-top: 1.2rem; }

    /* 品牌区 */
    .brand-row { display: flex; align-items: center; gap: 10px; padding: 2px 6px 0; }
    .brand-seal {
        width: 34px; height: 34px; flex: none;
        border-radius: 9px;
        background: var(--gold-soft);
        border: 1px solid var(--gold-line);
        display: flex; align-items: center; justify-content: center;
        color: var(--gold); font-size: 17px; font-weight: 700;
    }
    .brand-name { font-size: 16px; font-weight: 700; color: var(--text-1); letter-spacing: .5px; line-height: 1.25; }
    .brand-en { font-size: 9px; letter-spacing: 2.2px; color: var(--text-3); margin-top: 3px; }
    .status-row {
        display: flex; gap: 14px;
        padding: 10px 6px 0;
        font-size: 11.5px; color: var(--text-3);
    }
    .status-dot {
        display: inline-block; width: 6px; height: 6px; border-radius: 50%;
        margin-right: 5px; vertical-align: 1px;
        background: var(--down);
    }
    .status-dot.off { background: var(--text-3); }

    /* 侧边栏导航按钮（active 页 = primary 按钮走金色态） */
    section[data-testid="stSidebar"] .stButton button {
        width: 100%;
        text-align: left;
        background: transparent;
        border: none;
        border-radius: 10px;
        color: var(--text-2);
        padding: .48rem .85rem;
        font-size: 13.5px;
        font-weight: 500;
        transition: background .16s var(--ease), color .16s var(--ease);
        box-shadow: none !important;
    }
    section[data-testid="stSidebar"] .stButton button:hover {
        background: var(--surface-2);
        color: var(--text-1);
    }
    section[data-testid="stSidebar"] .stButton button p { font-size: 13.5px; }
    /* active 导航项（primary 变体在侧边栏内 = 金色选中态） */
    section[data-testid="stSidebar"] .stButton button[data-testid="baseButton-primary"],
    section[data-testid="stSidebar"] .stButton button[kind="primary"] {
        background: var(--gold-soft) !important;
        color: var(--gold) !important;
        box-shadow: inset 2px 0 0 var(--gold) !important;
        border: none;
    }

    section[data-testid="stSidebar"] .stCaption, section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] {
        color: var(--text-3) !important;
    }

    /* ===== 隐藏 Streamlit 自动多页面导航 ===== */
    div[data-testid="stSidebarNav"] { display: none !important; }
    ul[data-testid="stSidebarNavItems"] { display: none !important; }

    /* ===== 双轨切换（segmented control）
       1.58: button[kind/data-testid=segmented_control*]
       ≥1.6x: React Aria 重构 → button[data-variant=segmented_control*][aria-checked] ===== */
    div[data-testid="stButtonGroup"]:has(button[data-testid*="segmented_control"]),
    div[data-testid="stButtonGroup"]:has(button[data-variant*="segmented_control"]) {
        display: flex !important;
        flex-direction: row !important;
        flex-wrap: nowrap !important;
        align-items: center;
        background: var(--surface-2) !important;
        border: 1px solid var(--line) !important;
        border-radius: 999px !important;
        padding: 3px !important;
        gap: 2px !important;
        width: fit-content !important;
    }
    /* 中间包装层透明化，让按钮直接成为组的 flex 子项（否则竖排） */
    div[data-testid="stButtonGroup"]:has(button[data-testid*="segmented_control"]) > div,
    div[data-testid="stButtonGroup"]:has(button[data-variant*="segmented_control"]) > div {
        display: contents !important;
    }
    .stApp button[data-variant="segmented_control"] {
        display: inline-flex !important;
        flex-direction: row !important;
        align-items: center;
        width: auto !important;
        background: transparent !important;
        color: var(--text-2) !important;
        border: 1px solid transparent !important;
        border-radius: 999px !important;
        box-shadow: none !important;
        transition: background .16s var(--ease), color .16s var(--ease);
    }
    .stApp button[data-variant="segmented_control"] p,
    .stApp button[data-variant="segmented_control"] span:not([data-testid="stIconEmoji"]) {
        color: var(--text-2) !important;
    }
    .stApp button[data-variant="segmented_control"]:hover {
        color: var(--text-1) !important;
    }
    /* 高特异度：1.6x 的 emotion 样式动态插在文档流后部，需更长选择器压过 */
    .stApp div[data-testid="stElementContainer"] div[data-testid="stButtonGroup"] button[data-variant="segmented_control"][data-selected="true"],
    .stApp div[data-testid="stElementContainer"] div[data-testid="stButtonGroup"] button[data-variant="segmented_control"][aria-checked="true"] {
        background: var(--gold-soft) !important;
        color: var(--gold) !important;
        border: 1px solid var(--gold-line) !important;
        border-radius: 999px !important;
        box-shadow: none !important;
    }
    .stApp div[data-testid="stElementContainer"] div[data-testid="stButtonGroup"] button[data-variant="segmented_control"][data-selected="true"] *,
    .stApp div[data-testid="stElementContainer"] div[data-testid="stButtonGroup"] button[data-variant="segmented_control"][aria-checked="true"] * {
        color: var(--gold) !important;
    }
    .stApp button[kind="segmented_control"],
    .stApp button[data-testid="stBaseButton-segmented_control"] {
        display: inline-flex !important;
        flex-direction: row !important;
        align-items: center;
        width: auto !important;
        background: transparent !important;
        color: var(--text-2) !important;
        border: 1px solid transparent !important;
        border-radius: 999px !important;
        box-shadow: none !important;
        transition: background .16s var(--ease), color .16s var(--ease);
    }
    .stApp button[kind="segmented_control"] p,
    .stApp button[kind="segmented_control"] span:not([data-testid="stIconEmoji"]) {
        color: var(--text-2) !important;
    }
    .stApp button[kind="segmented_control"]:hover,
    .stApp button[data-testid="stBaseButton-segmented_control"]:hover {
        color: var(--text-1) !important;
    }
    .stApp button[kind="segmented_controlActive"],
    .stApp button[data-testid="stBaseButton-segmented_controlActive"] {
        display: inline-flex !important;
        flex-direction: row !important;
        align-items: center;
        width: auto !important;
        background: var(--gold-soft) !important;
        color: var(--gold) !important;
        border: 1px solid var(--gold-line) !important;
        border-radius: 999px !important;
        box-shadow: none !important;
    }
    .stApp button[kind="segmented_controlActive"] p,
    .stApp button[kind="segmented_controlActive"] span,
    .stApp button[data-testid="stBaseButton-segmented_controlActive"] p,
    .stApp button[data-testid="stBaseButton-segmented_controlActive"] span {
        color: var(--gold) !important;
    }

    /* ===== 指标卡 st.metric ===== */
    div[data-testid="stMetric"], .stMetric {
        background-color: var(--surface) !important;
        padding: 15px 17px;
        border-radius: 14px;
        border: 1px solid var(--line);
        box-shadow: none !important;
        transition: border-color .16s var(--ease);
    }
    div[data-testid="stMetric"]:hover { border-color: var(--line-strong); }
    div[data-testid="stMetric"] [data-testid="stMetricLabel"] p,
    div[data-testid="stMetric"] label p {
        color: var(--text-3) !important;
        font-size: 12px !important;
        font-weight: 500 !important;
    }
    div[data-testid="stMetric"] [data-testid="stMetricValue"] p {
        color: var(--text-1) !important;
        font-size: 25px !important;
        font-weight: 650 !important;
        font-variant-numeric: tabular-nums;
    }
    div[data-testid="stMetric"] [data-testid="stMetricDelta"] p,
    div[data-testid="stMetric"] [data-testid="stMetricDelta"] {
        font-size: 12px !important;
        font-weight: 500 !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stMetric"] {
        background-color: var(--surface) !important;
        border-color: var(--line) !important;
    }

    /* ===== 按钮 ===== */
    .stButton button {
        border-radius: 10px;
        font-weight: 600;
        font-size: 13.5px;
        padding: .45rem 1.15rem;
        transition: background .16s var(--ease), color .16s var(--ease), border-color .16s var(--ease);
        border: 1px solid transparent;
        box-shadow: none !important;
    }
    .stButton button:hover { transform: none; }
    /* 主按钮 = 金底深字 */
    .stButton button[data-testid="baseButton-primary"],
    .stButton button[kind="primary"] {
        background: var(--gold);
        color: #1A2233;
        border: none;
    }
    .stButton button[data-testid="baseButton-primary"]:hover,
    .stButton button[kind="primary"]:hover {
        background: var(--gold-hover);
        color: #1A2233;
    }
    /* 次/默认按钮 = 幽灵态 */
    .stButton button[data-testid="baseButton-secondary"],
    .stButton button[data-testid="baseButton-secondaryFormSubmit"],
    .stButton button[kind="secondary"],
    .stButton button[kind="secondaryFormSubmit"] {
        background: transparent;
        color: var(--text-2);
        border: 1px solid var(--line-strong);
    }
    .stButton button[data-testid="baseButton-secondary"]:hover,
    .stButton button[kind="secondary"]:hover {
        background: var(--surface-2);
        color: var(--text-1);
    }

    /* ===== 标签页 ===== */
    .stTabs [data-baseweb="tab-list"] {
        gap: 26px;
        background: transparent;
        padding: 0;
        border-radius: 0;
        border-bottom: 1px solid var(--line);
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 0;
        padding: 8px 2px 10px;
        font-size: 13.5px;
        font-weight: 500;
        color: var(--text-3);
        background: transparent;
    }
    .stTabs [aria-selected="true"] {
        color: var(--text-1) !important;
        font-weight: 600;
        background: transparent !important;
        box-shadow: none !important;
    }
    .stTabs [data-baseweb="tab-highlight"] {
        background-color: var(--gold) !important;
        height: 2px !important;
        border-radius: 2px !important;
    }
    .stTabs [data-baseweb="tab-border"] { display: none !important; }

    /* ===== 输入/选择 ===== */
    .stTextInput input, .stNumberInput input, .stTextArea textarea,
    div[data-baseweb="input"] input, div[data-baseweb="textarea"] textarea {
        border-radius: 10px;
        border: 1px solid var(--line);
        padding: .5rem .75rem;
        font-size: 13.5px;
        background-color: var(--surface-2);
        color: var(--text-1);
        transition: border-color .16s var(--ease), box-shadow .16s var(--ease);
    }
    .stTextInput input:focus, .stNumberInput input:focus, .stTextArea textarea:focus,
    div[data-baseweb="input"]:focus-within, div[data-baseweb="textarea"]:focus-within {
        border-color: var(--gold) !important;
        box-shadow: 0 0 0 2px var(--gold-soft) !important;
    }
    .stTextInput input::placeholder, .stTextArea textarea::placeholder { color: var(--text-3); }

    div[data-baseweb="select"] > div {
        background-color: var(--surface-2) !important;
        border-color: var(--line) !important;
        border-radius: 10px !important;
    }
    div[data-baseweb="select"] span { color: var(--text-1) !important; }
    div[data-baseweb="popover"] div[role="listbox"],
    div[role="listbox"] ul {
        background-color: var(--surface-2) !important;
        border: 1px solid var(--line) !important;
        border-radius: 10px !important;
    }
    div[data-baseweb="popover"] li, div[role="listbox"] li { color: var(--text-1) !important; }
    div[data-baseweb="popover"] li:hover, div[role="listbox"] li:hover {
        background-color: var(--gold-soft) !important;
    }

    div[data-baseweb="checkbox"] span { color: var(--text-2); }

    /* ===== 信息框（统一安静的容器 + 语义色仅作点缀） ===== */
    div[data-testid="stAlertContainer"], .stAlert {
        border-radius: 12px;
        border: 1px solid var(--line) !important;
        background: var(--surface-2) !important;
        color: var(--text-2) !important;
        padding: 12px 16px;
    }
    .stInfo { background: var(--surface-2); color: var(--info); border-left: 2px solid var(--info); }
    .stSuccess { background: var(--surface-2); color: var(--down); border-left: 2px solid var(--down); }
    .stWarning { background: var(--surface-2); color: var(--warn); border-left: 2px solid var(--warn); }
    .stError { background: var(--surface-2); color: var(--danger); border-left: 2px solid var(--danger); }

    /* ===== 展开器 ===== */
    [data-testid="stExpander"] {
        background: var(--surface);
        border: 1px solid var(--line) !important;
        border-radius: 12px !important;
        margin-bottom: 8px;
        overflow: hidden;
    }
    [data-testid="stExpander"] summary {
        font-weight: 500;
        font-size: 13.5px;
        color: var(--text-2);
        transition: color .16s var(--ease);
    }
    [data-testid="stExpander"] summary:hover { color: var(--text-1); }
    [data-testid="stExpander"] summary p { color: inherit; }
    [data-testid="stExpanderDetails"] {
        background: var(--surface);
        color: var(--text-2);
    }
    /* 旧版类名兜底 */
    .streamlit-expanderHeader {
        background: var(--surface); color: var(--text-2);
        border-radius: 12px; font-weight: 500; padding: 10px 14px;
    }
    .streamlit-expanderContent {
        border: 1px solid var(--line); border-top: none;
        border-radius: 0 0 12px 12px; padding: 14px; background: var(--surface);
    }

    /* ===== 聊天 ===== */
    [data-testid="stChatMessage"] {
        background: var(--surface);
        border: 1px solid var(--line);
        border-radius: 14px;
    }
    [data-testid="stChatInput"] textarea, [data-testid="stChatInput"] > div {
        background: var(--surface-2) !important;
        border: 1px solid var(--line) !important;
        border-radius: 12px !important;
        color: var(--text-1) !important;
    }

    /* ===== 表格（markdown 管道表 & dataframe） ===== */
    .stMarkdown table {
        width: 100%;
        border-collapse: collapse;
        font-size: 13px;
        font-variant-numeric: tabular-nums;
    }
    .stMarkdown thead tr th {
        background: transparent;
        color: var(--text-3);
        font-weight: 500;
        font-size: 11.5px;
        padding: 8px 10px;
        border-bottom: 1px solid var(--line-strong);
    }
    .stMarkdown tbody tr td {
        padding: 9px 10px;
        border-bottom: 1px solid var(--line);
        color: var(--text-2);
    }
    .stMarkdown tbody tr:hover td { background: var(--surface-2); }
    .stMarkdown tbody tr:last-child td { border-bottom: none; }
    .stDataFrame {
        border-radius: 12px; overflow: hidden; border: 1px solid var(--line);
    }

    /* ===== 加载/代码/链接/杂项 ===== */
    .stSpinner > div { border-color: var(--gold) !important; }
    .stCodeBlock, [data-testid="stCode"] { background-color: var(--surface-2) !important; border-radius: 10px; }
    code { color: var(--gold-hover) !important; background: var(--surface-2) !important; border-radius: 5px; padding: 1px 5px; }
    .stMarkdown a { color: var(--gold); text-decoration: none; border-bottom: 1px solid var(--gold-line); }
    .stMarkdown a:hover { color: var(--gold-hover); }
    [data-testid="stCaptionContainer"], .stCaption { color: var(--text-3) !important; font-size: 12px; }

    /* ===== 工具类（页面内联 HTML 使用） ===== */
    .num { font-variant-numeric: tabular-nums; }
    .text-up { color: var(--up) !important; }      /* 涨·红 */
    .text-down { color: var(--down) !important; }  /* 跌·绿 */
    .text-muted { color: var(--text-3) !important; font-size: 12px; }
    .text-large { font-size: 25px; font-weight: 650; font-variant-numeric: tabular-nums; }
    .text-center { text-align: center; }
    .mt-1 { margin-top: 8px; } .mt-2 { margin-top: 16px; }
    .mb-1 { margin-bottom: 8px; } .mb-2 { margin-bottom: 16px; }

    /* ===== 指标行（market_indicator / holdings_card 行式布局） ===== */
    .row-item {
        display: flex; justify-content: space-between; align-items: center;
        padding: 9px 8px;
        border-bottom: 1px solid var(--line);
        transition: background .16s var(--ease);
    }
    .row-item:hover { background: var(--surface-2); }
    .row-item:last-child { border-bottom: none; }

    /* ===== 自定义指标卡（holdings_card HTML 版） ===== */
    .metric-card {
        background: var(--surface);
        border: 1px solid var(--line);
        border-radius: 14px;
        padding: 15px 17px;
    }
    .metric-card .m-lbl { font-size: 12px; color: var(--text-3); }
    .metric-card .m-val {
        font-size: 25px; font-weight: 650; color: var(--text-1);
        margin-top: 6px; font-variant-numeric: tabular-nums;
    }
    .metric-card .m-delta { font-size: 12px; margin-top: 5px; color: var(--text-3); }

    /* ===== 空状态 ===== */
    .empty-state { text-align: center; padding: 34px 20px; }
    .empty-state .es-icon { font-size: 30px; margin-bottom: 10px; }
    .empty-state .es-title { font-size: 14.5px; color: var(--text-2); font-weight: 600; }
    .empty-state .es-hint { font-size: 12.5px; color: var(--text-3); margin-top: 6px; line-height: 1.7; }

    /* ===== 顶部固定双轨切换（最右上角） =====
       Streamlit 1.58+ 给每个组件容器打 st-key-<widget key> 类名，
       track_switcher 的容器直接 fixed 到视口右上角；
       右栏功能面板顶部已留 44px 让位条（ai_chat 页内联 spacer）。 */
    [data-testid="stMainBlockContainer"] .st-key-track_switcher {
        position: fixed;
        top: 10px;
        right: 14px;
        z-index: 1000;
        margin: 0;
        width: auto;
    }

    /* ===== ai_chat 右栏功能面板紧凑化 =====
       右栏 = 唯一含 .panel-label 的 stColumn，锚定后压缩按钮/卡片/行距，
       目标：笔记本 800px 可视高内不溢出。其他页面不受影响。 */
    div[data-testid="stColumn"]:has(.panel-label) .panel-label { margin: 10px 0 4px; }
    div[data-testid="stColumn"]:has(.panel-label) .stButton button {
        padding: .22rem .55rem !important;
        font-size: 12px !important;
        border-radius: 8px !important;
        min-height: 30px !important;
    }
    div[data-testid="stColumn"]:has(.panel-label) .stButton button p { font-size: 12px !important; }
    div[data-testid="stColumn"]:has(.panel-label) .metric-card { padding: 10px 12px; }
    div[data-testid="stColumn"]:has(.panel-label) .metric-card .m-val { font-size: 20px; }
    div[data-testid="stColumn"]:has(.panel-label) .row-item { padding: 6px 6px; }
    div[data-testid="stColumn"]:has(.panel-label) [data-testid="stCaptionContainer"],
    div[data-testid="stColumn"]:has(.panel-label) .stCaption { font-size: 11px !important; }

    /* ===== 动效关闭 ===== */
    @media (prefers-reduced-motion: reduce) {
        * { transition: none !important; animation: none !important; }
    }
</style>
""",
        unsafe_allow_html=True,
    )

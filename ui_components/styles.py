# -*- coding: utf-8 -*-
from typing import Optional, Dict, List  # 3.7 显式导入
import streamlit as st


def inject_global_css() -> None:
    """从 web_agent.py 迁移的全局 CSS（原位置剪切）"""
    st.markdown(
        """
<style>
    /* ===== 全局基础样式 ===== */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    }

    .main .block-container {
        max-width: 1200px;
        padding: 1.5rem 2rem;
        margin: 0 auto;
    }

    /* 隐藏 Streamlit 默认元素 */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}

    /* 自定义滚动条（深色） */
    ::-webkit-scrollbar {
        width: 6px;
        height: 6px;
    }
    ::-webkit-scrollbar-track {
        background: #2A2A2A;
        border-radius: 3px;
    }
    ::-webkit-scrollbar-thumb {
        background: #555;
        border-radius: 3px;
    }
    ::-webkit-scrollbar-thumb:hover {
        background: #777;
    }

    /* ===== 背景色（深色） ===== */
    .stApp {
        background-color: #1A1A1A;
    }

    /* ===== 卡片组件（深色） ===== */
    .card {
        background: #2A2A2A;
        border-radius: 12px;
        padding: 20px;
        margin-bottom: 16px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.3);
        border: 1px solid #3F3F46;
        transition: box-shadow 0.2s ease;
    }
    .card:hover {
        box-shadow: 0 4px 16px rgba(0,0,0,0.4);
    }
    .card-title {
        font-size: 16px;
        font-weight: 600;
        color: #FFFFFF;
        margin-bottom: 12px;
        padding-bottom: 8px;
        border-bottom: 2px solid #3B82F6;
        display: inline-block;
    }
    .card-subtitle {
        font-size: 13px;
        color: #9CA3AF;
        margin-bottom: 12px;
    }

    /* ===== 指标卡片（深色） ===== */
    div[data-testid="stMetric"],
    .stMetric {
        background-color: #2A2A2A !important;
        padding: 16px;
        border-radius: 12px;
        border: 1px solid #3F3F46;
        box-shadow: 0 2px 8px rgba(0,0,0,0.2);
    }
    div[data-testid="stMetric"] label,
    div[data-testid="stMetric"] label p,
    div[data-testid="stMetric"] [data-testid="stMetricLabel"] p,
    .stMetric label,
    .stMetric label p {
        color: #9CA3AF !important;
        font-size: 13px !important;
        font-weight: 500 !important;
    }
    div[data-testid="stMetric"] [data-testid="stMetricValue"] p,
    .stMetric [data-testid="stMetricValue"] p {
        color: #FFFFFF !important;
        font-size: 24px !important;
        font-weight: 700 !important;
    }
    div[data-testid="stMetric"] [data-testid="stMetricDelta"] p,
    .stMetric [data-testid="stMetricDelta"] p {
        font-size: 13px !important;
        font-weight: 600 !important;
    }

    /* ===== 按钮样式（深色） ===== */
    .stButton button {
        border-radius: 8px;
        font-weight: 500;
        font-size: 14px;
        padding: 0.5rem 1.2rem;
        transition: all 0.2s ease;
        border: 1px solid transparent;
    }
    .stButton button:hover {
        transform: translateY(-1px);
        box-shadow: 0 4px 12px rgba(59, 130, 246, 0.3);
    }
    .stButton button:active {
        transform: translateY(0);
    }
    /* 主按钮 */
    .stButton button[data-testid="baseButton-primary"] {
        background: linear-gradient(135deg, #3B82F6 0%, #60A5FA 100%);
        color: white;
        border: none;
    }
    /* 次按钮 */
    .stButton button:not([data-testid="baseButton-primary"]) {
        background: #2A2A2A;
        color: #3B82F6;
        border: 1px solid #3B82F6;
    }
    .stButton button:not([data-testid="baseButton-primary"]):hover {
        background: #1E3A5F;
        color: #60A5FA;
    }

    /* ===== 隐藏 Streamlit 自动多页面导航（与自定义侧边栏冲突） ===== */
    div[data-testid="stSidebarNav"] {
        display: none !important;
    }
    ul[data-testid="stSidebarNavItems"] {
        display: none !important;
    }

    /* ===== 侧边栏样式（深色） ===== */
    section[data-testid="stSidebar"] {
        background-color: #1A1A1A;
        border-right: 1px solid #3F3F46;
    }
    section[data-testid="stSidebar"] .stButton button {
        width: 100%;
        text-align: left;
        background: transparent;
        border: none;
        color: #E5E7EB;
        padding: 0.6rem 1rem;
        border-radius: 8px;
        font-size: 14px;
        transition: all 0.2s;
    }
    section[data-testid="stSidebar"] .stButton button:hover {
        background: #2A2A2A;
        color: #3B82F6;
    }
    section[data-testid="stSidebar"] .sidebar-item {
        color: #E5E7EB;
        margin-bottom: 6px;
        padding: 8px 12px;
        border-radius: 8px;
        transition: background 0.2s;
    }
    section[data-testid="stSidebar"] .sidebar-item:hover {
        background: #2A2A2A;
    }
    section[data-testid="stSidebar"] .sidebar-item-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        color: #E5E7EB;
    }

    /* ===== 分割线（深色） ===== */
    hr {
        margin: 1.5rem 0;
        border: none;
        border-top: 1px solid #3F3F46;
    }

    /* ===== 标题样式（深色） ===== */
    h1 {
        font-size: 28px;
        font-weight: 700;
        color: #FFFFFF;
        margin-bottom: 0.5rem;
    }
    h2 {
        font-size: 22px;
        font-weight: 600;
        color: #FFFFFF;
        margin-top: 1.5rem;
        margin-bottom: 1rem;
    }
    h3 {
        font-size: 18px;
        font-weight: 600;
        color: #E5E7EB;
        margin-top: 1.2rem;
        margin-bottom: 0.8rem;
    }

    /* ===== 表格美化（深色） ===== */
    .stDataFrame {
        border-radius: 12px;
        overflow: hidden;
        border: 1px solid #3F3F46;
    }
    .stDataFrame table {
        font-size: 13px;
    }
    .stDataFrame thead tr th {
        background-color: #2A2A2A;
        color: #E5E7EB;
        font-weight: 600;
        padding: 10px 12px;
        border-bottom: 2px solid #3F3F46;
    }
    .stDataFrame tbody tr:nth-child(even) {
        background-color: #222;
    }
    .stDataFrame tbody tr:hover {
        background-color: #1E3A5F;
    }

    /* ===== 输入框美化（深色） ===== */
    .stTextInput input, .stSelectbox select, .stNumberInput input {
        border-radius: 8px;
        border: 1px solid #3F3F46;
        padding: 0.5rem 0.75rem;
        font-size: 14px;
        transition: border-color 0.2s;
        background-color: #2A2A2A;
        color: #E5E7EB;
    }
    .stTextInput input:focus, .stSelectbox select:focus, .stNumberInput input:focus {
        border-color: #3B82F6;
        box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.15);
    }
    /* 输入框 placeholder */
    .stTextInput input::placeholder {
        color: #6B7280;
    }

    /* ===== 标签页美化（深色） ===== */
    .stTabs [data-baseweb="tab-list"] {
        gap: 4px;
        background: #2A2A2A;
        padding: 4px;
        border-radius: 10px;
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 8px;
        padding: 8px 16px;
        font-size: 14px;
        font-weight: 500;
        color: #9CA3AF;
    }
    .stTabs [aria-selected="true"] {
        background: #3F3F46;
        color: #3B82F6;
        box-shadow: 0 2px 4px rgba(0,0,0,0.3);
    }

    /* ===== 信息框美化（深色） ===== */
    .stAlert {
        border-radius: 10px;
        border: none;
        padding: 12px 16px;
    }
    .stInfo {
        background: #1E3A5F;
        color: #60A5FA;
    }
    .stSuccess {
        background: #1A3A2A;
        color: #10B981;
    }
    .stWarning {
        background: #3A2A1A;
        color: #FBBF24;
    }
    .stError {
        background: #3A1A1A;
        color: #EF4444;
    }

    /* ===== 展开器美化（深色） ===== */
    .streamlit-expanderHeader {
        border-radius: 8px;
        font-weight: 500;
        background: #2A2A2A;
        color: #E5E7EB;
        padding: 10px 14px;
    }
    .streamlit-expanderContent {
        border: 1px solid #3F3F46;
        border-top: none;
        border-radius: 0 0 8px 8px;
        padding: 14px;
        background: #2A2A2A;
    }

    /* ===== 加载动画（深色） ===== */
    .stSpinner > div {
        border-color: #3B82F6 !important;
    }

    /* ===== 下拉框/选择器（深色） ===== */
    div[data-baseweb="select"] > div {
        background-color: #2A2A2A !important;
        border-color: #3F3F46 !important;
    }
    div[data-baseweb="select"] span {
        color: #E5E7EB !important;
    }
    div[data-baseweb="popover"] div[role="listbox"] {
        background-color: #2A2A2A !important;
    }
    div[data-baseweb="popover"] li {
        color: #E5E7EB !important;
    }
    div[data-baseweb="popover"] li:hover {
        background-color: #1E3A5F !important;
    }

    /* ===== 多选/下拉菜单（深色） ===== */
    div[role="listbox"] ul {
        background-color: #2A2A2A !important;
    }
    div[role="listbox"] li {
        color: #E5E7EB !important;
    }

    /* ===== 文本区域（深色） ===== */
    .stTextArea textarea {
        background-color: #2A2A2A !important;
        color: #E5E7EB !important;
        border-color: #3F3F46 !important;
    }

    /* ===== 代码块（深色） ===== */
    .stCodeBlock {
        background-color: #1A1A1A !important;
    }
    code {
        color: #E5E7EB !important;
    }

    /* ===== 分割线组件 ===== */
    .stMarkdown hr {
        border-color: #3F3F46 !important;
    }

    /* ===== 自定义工具类（深色） ===== */
    .text-up { color: #EF4444 !important; }
    .text-down { color: #10B981 !important; }
    .text-muted { color: #9CA3AF !important; font-size: 13px; }
    .text-large { font-size: 24px; font-weight: 700; }
    .text-center { text-align: center; }
    .mt-1 { margin-top: 8px; }
    .mt-2 { margin-top: 16px; }
    .mb-1 { margin-bottom: 8px; }
    .mb-2 { margin-bottom: 16px; }

    /* ===== 侧边栏 radio 导航（深色） ===== */
    div[data-testid="stRadio"] label {
        color: #E5E7EB !important;
    }
    div[data-testid="stRadio"] label:hover {
        background: #2A2A2A !important;
        color: #3B82F6 !important;
    }
    div[data-testid="stRadio"] label[data-checked="true"] {
        background: #1E3A5F !important;
        color: #3B82F6 !important;
        border-left: 3px solid #3B82F6 !important;
    }

    /* ===== 侧边栏 metric（深色） ===== */
    section[data-testid="stSidebar"] div[data-testid="stMetric"] {
        background-color: #2A2A2A !important;
        border-color: #3F3F46 !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stMetric"] label p {
        color: #9CA3AF !important;
    }
    section[data-testid="stSidebar"] div[data-testid="stMetric"] [data-testid="stMetricValue"] p {
        color: #FFFFFF !important;
    }

    /* ===== 侧边栏 caption（深色） ===== */
    section[data-testid="stSidebar"] .stCaption {
        color: #9CA3AF !important;
    }

    /* ===== 侧边栏 info/warning（深色） ===== */
    section[data-testid="stSidebar"] .stInfo {
        background: #1E3A5F !important;
        color: #60A5FA !important;
    }
</style>
""",
        unsafe_allow_html=True,
    )
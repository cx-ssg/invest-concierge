"""
基金Agent Web界面 - 主入口
支持多页面路由，根据侧边栏导航切换页面
"""
import streamlit as st
from ui_components.sidebar import render_sidebar
from ui_components.styles import inject_global_css


# ==================== 页面路由 ====================

PAGES = {
    "dashboard":      "pages.dashboard",
    "portfolio":      "pages.portfolio",
    "diary":          "pages.diary",
    "dingtou":        "pages.dingtou",
    "fund_search":    "pages.fund_search",
    "market":         "pages.market",
    "compare":        "pages.compare",
    "dingtou_calc":   "pages.dingtou_calc",
    "backtest":       "pages.backtest",
    "ai_chat":        "pages.ai_chat",
    "analysis":       "pages.analysis",
    "watchlist":      "pages.watchlist",
    "stock_search":   "pages.stock_search",
    "stock_holdings": "pages.stock_holdings",
    "stock_diagnosis":"pages.stock_diagnosis",
    "stock_market_overview":"pages.stock_market_overview",
    "stock_tools":    "pages.stock_tools",
    "alert":          "pages.alert",
    "profile":        "pages.profile",
    "settings":       "pages.settings",
}


def render_page(page_key):
    """根据 page_key 渲染对应页面"""
    import importlib

    module_path = PAGES.get(page_key)
    if not module_path:
        st.error("未知页面：{}".format(page_key))
        return

    try:
        module = importlib.import_module(module_path)
        if hasattr(module, "main"):
            module.main()
        elif hasattr(module, "render"):
            module.render()
        else:
            st.error("页面模块缺少 main/render 函数：{}".format(page_key))
    except Exception as e:
        st.error("加载页面失败：{} - {}".format(page_key, e))
        st.info("页面文件可能尚未创建，请先初始化页面模块。")


def show_homepage():
    """渲染首页"""
    st.title("💰 基金Agent智能助手")
    st.markdown("### 欢迎使用基金分析与管理平台")

    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown("""
        <div class="card">
            <h3>📊 我的持仓</h3>
            <p>查看和管理您的基金持仓</p>
        </div>
        """, unsafe_allow_html=True)

    with col2:
        st.markdown("""
        <div class="card">
            <h3>🔍 基金筛选</h3>
            <p>智能筛选优质基金</p>
        </div>
        """, unsafe_allow_html=True)

    with col3:
        st.markdown("""
        <div class="card">
            <h3>🤖 AI分析</h3>
            <p>AI驱动的基金分析报告</p>
        </div>
        """, unsafe_allow_html=True)

    # 风险提示
    st.markdown("---")
    st.caption("⚠️ 风险提示：本工具仅供个人投资参考，不构成任何投资建议。数据来自 AkShare，可能存在延迟或不准确。")


def main():
    """主函数"""
    # 页面配置
    st.set_page_config(
        page_title="基金Agent助手",
        page_icon="💰",
        layout="wide",
        initial_sidebar_state="expanded"
    )

    # 注入全局CSS样式
    inject_global_css()

    # 初始化 session_state
    if "page" not in st.session_state:
        st.session_state.page = "home"

    # 渲染侧边栏
    render_sidebar()

    # 主内容区 - 根据当前页面路由
    current_page = st.session_state.get("page", "home")

    if current_page == "home":
        show_homepage()
    else:
        render_page(current_page)


if __name__ == "__main__":
    main()
# -*- coding: utf-8 -*-
"""
双轨导航 v1.0 渲染集验收测试
定案：D:/Vault/Handoff/fund_agent-融合版计划-给zcode-20260830.md §6（用户 2026-08-30 拍板）

验收标准（§6 修正版，消除空验）:
  1. v1.0 渲染集精确匹配：#基金轨 3 页（dashboard/portfolio/diary）
     + 股票轨 1 页（stock_diagnosis）+ 通用 ai_chat + settings
  2. live 页源码（pages/*.py + ui_components/*.py）`grep "敬请期待"` 零命中

范围说明：占位页按项目约束只隐藏不删除（live=false 不进导航），
其源码保留"敬请期待"占位文案属正常——本测试只断言 live 页源码 + UI 组件无占位文案。
"""
import os

from ui_components.sidebar import PAGE_META, get_live_page_keys, get_live_pages

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# v1.0 渲染集（精确匹配，按轨道顺序：基金→股票→通用）
RENDER_SET = {
    "fund": ["dashboard", "portfolio", "diary"],
    "stock": ["stock_diagnosis"],
    "common": ["ai_chat", "settings"],
}
FULL_RENDER_SET = RENDER_SET["fund"] + RENDER_SET["stock"] + RENDER_SET["common"]

# 轨道归属结构（21 页全量 = ROADMAP 生成源）
TRACK_PAGE_COUNTS = {"fund": 10, "stock": 7, "common": 4}

# 占位页：live=false，一律不进导航（不渲染 = 不存在）
PLACEHOLDER_PAGES = {
    "fund": ["fund_search", "market", "compare", "dingtou",
             "dingtou_calc", "backtest", "analysis"],
    "stock": ["stock_search", "stock_holdings", "watchlist",
              "stock_market_overview", "stock_deep_analysis", "stock_tools"],
    "common": ["profile", "alert"],
}


def _all_track_keys():
    return [p["key"] for track in PAGE_META for p in PAGE_META[track]["pages"]]


# ==================== 1. v1.0 渲染集精确匹配 ====================

def test_v1_render_set_exact_per_track():
    """每个轨道的 live 渲染集精确匹配（不多不少）"""
    for track, expected in RENDER_SET.items():
        assert get_live_page_keys(track) == expected


def test_v1_render_set_exact_overall():
    """全量渲染集精确匹配：基金3 + 股票1 + ai_chat + settings，共 6 页"""
    assert get_live_page_keys() == FULL_RENDER_SET
    assert len(get_live_page_keys()) == 6


def test_v1_render_set_labels_match():
    """live 页导航 label 与定案命名一致（资产总览/我的持仓/投资日记/综合诊断/AI对话/系统设置）"""
    labels = dict(get_live_pages("fund") + get_live_pages("stock") + get_live_pages("common"))
    assert labels == {
        "dashboard": "📊 资产总览",
        "portfolio": "💼 我的持仓",
        "diary": "📝 投资日记",
        "stock_diagnosis": "🩺 综合诊断",
        "ai_chat": "💬 AI 对话",
        "settings": "⚙️ 系统设置",
    }


# ==================== 2. 轨道归属结构（21 页全量） ====================

def test_track_structure_full_21_pages():
    """轨道归属结构 = 21 页全量（基金10/股票7/通用4），无重复 key"""
    keys = _all_track_keys()
    assert len(keys) == 21
    assert len(set(keys)) == 21
    for track, count in TRACK_PAGE_COUNTS.items():
        assert len(PAGE_META[track]["pages"]) == count


def test_placeholder_pages_live_false():
    """占位页（含 profile/alert）一律 live=false，不渲染 = 不存在"""
    live = set(FULL_RENDER_SET)
    for track in PAGE_META:
        for page in PAGE_META[track]["pages"]:
            if page["key"] in live:
                assert page["live"] is True
            else:
                assert page["live"] is False


def test_live_pages_registered_in_routes():
    """live 页必须已在 web_agent.py PAGES 路由注册（不改路由，只验证）"""
    from web_agent import PAGES
    missing = [key for key in FULL_RENDER_SET if key not in PAGES]
    assert not missing, "live 页未注册路由: {}".format(missing)


# ==================== 3. live 页源码无"敬请期待" ====================

def test_no_placeholder_text_in_live_pages_and_ui_components():
    """live 页源码 + ui_components/*.py grep "敬请期待" 零命中"""
    files = []
    pages_dir = os.path.join(PROJECT_ROOT, "pages")
    for key in FULL_RENDER_SET:
        files.append(os.path.join(pages_dir, key + ".py"))

    ui_dir = os.path.join(PROJECT_ROOT, "ui_components")
    for name in sorted(os.listdir(ui_dir)):
        if name.endswith(".py"):
            files.append(os.path.join(ui_dir, name))

    hits = []
    for path in files:
        with open(path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                if "敬请期待" in line:
                    hits.append("{}:{}".format(os.path.relpath(path, PROJECT_ROOT), lineno))
    assert not hits, "live 页源码/UI 组件发现占位文案: {}".format(hits)


def test_settings_placeholder_section_cut():
    """settings 已裁掉"数据管理即将上线"占位区"""
    src = open(os.path.join(PROJECT_ROOT, "pages", "settings.py"), encoding="utf-8").read()
    assert "敬请期待" not in src
    assert "数据管理即将上线" not in src
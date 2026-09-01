# -*- coding: utf-8 -*-
"""
综合诊断页面（英雄页） - 多标签页串联 5 大引擎：
基本面 / 排雷 / 护城河 / 估值 / 财报三表 + AI 多角色辩论

路由：web_agent.py PAGES["stock_diagnosis"] 已注册，侧边栏「🔬 智能分析 → 🩺 综合诊断」已配置。
数据来源：AkShare（东方财富/同花顺/乐咕），行情 60s 缓存、财务 24h 缓存。
实现依据：docs/DIAGNOSIS_PLAN.md §2 布局 / §3 引擎接线表 / §4 stock_data 注入 / §5.4 历史分位补算。
"""
import sys

import streamlit as st

from config import API_KEY
from data.diagnosis import build_diagnosis_payload
from data.financial_report import (
    analyze_growth_trend,
    analyze_profit_trend,
    analyze_financial_health,
)

# Windows 控制台 GBK 防护：stdout 被 Streamlit 接管时 reconfigure 可能不存在，静默跳过
if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

TAB_NAMES = ["📊 基本面", "⚠️ 排雷", "🏰 护城河", "💰 估值", "📑 财报三表", "🤖 AI 辩论"]

EXAMPLE_STOCKS = {
    "手动输入": "",
    "600519 贵州茅台": "600519",
    "300750 宁德时代": "300750",
}

RATING_COLORS = {
    "强烈推荐": "#E5C784",
    "推荐": "#D6B36A",
    "中性": "#E8B34B",
    "谨慎": "#FF922B",
    "回避": "#EF4444",
    "未评级": "#66738C",
}

HEALTH_ICONS = {"safe": "🟢", "normal": "🔵", "warning": "🟡", "danger": "🔴"}


# ==================== 工具函数 ====================


def _fmt_num(value, digits=2, suffix=""):
    """数字格式化：None / NaN → '--'"""
    if value is None:
        return "--"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "--"
    if v != v:  # NaN 判断（pandas 可能产出 NaN）
        return "--"
    return "{:.{}f}{}".format(v, digits, suffix)


def _fmt_pct(value, digits=2):
    return _fmt_num(value, digits, "%")


def _fmt_yi(value, digits=2):
    """元 → 亿 格式化"""
    if value is None:
        return "--"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "--"
    if v != v:
        return "--"
    return "{:.{}f}亿".format(v / 100000000, digits)


def _plain_level(level):
    """去掉等级文案里的 emoji 前缀，便于拼进 stock_data 纯文本"""
    if not level:
        return "--"
    for icon in ("✅ ", "🟢 ", "🟡 ", "🟠 ", "🔴 "):
        if level.startswith(icon):
            return level[len(icon):]
    return level


def _is_valid_code(code):
    """校验 6 位纯数字股票代码"""
    return len(code) == 6 and code.isdigit()


# ==================== 数据统一拉取 ====================
# 6 引擎统一拉取已搬出至 data/diagnosis.py 的 build_diagnosis_payload（含历史分位补算），
# 页面与 Agent get_stock_diagnosis 工具共用一份，规避 utils → pages 反向依赖。
# 实现依据：docs/AGENT_MVP_DESIGN.md §3 / §5 实施注意 3。


# ==================== 渲染辅助 ====================


def _render_badge(label, value, color):
    """彩色横幅卡片（左侧语义色竖条 + token 化文字）"""
    st.markdown(
        '<div class="card" style="border-left:3px solid {c};padding:14px 18px;">'
        '<span style="color:var(--text-3);font-size:12.5px;">{lbl}</span><br/>'
        '<span style="color:{c};font-size:21px;font-weight:700;">{val}</span></div>'.format(
            lbl=label, val=value, c=color),
        unsafe_allow_html=True,
    )


def _md_table(rows, headers):
    """生成 markdown 管道表格（不依赖 pyarrow / matplotlib，任何环境可渲染）"""
    if not rows:
        return "_暂无_\n\n"
    lines = ["| " + " | ".join(str(h) for h in headers) + " |",
             "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        lines.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(lines) + "\n"


def _value_color(value):
    """估值状态 → 颜色（低估绿 / 合理琥珀 / 高估橙红，与涨跌语义解耦）"""
    return {"严重低估": "#2DBE64", "低估": "#2DBE64", "合理": "#E8B34B",
            "高估": "#FF922B", "严重高估": "#EF4444"}.get(value, "#66738C")


# ==================== 页面主流程 ====================


def main():
    st.title("🩺 综合诊断")
    st.caption(
        "输入股票代码，一键生成基本面 / 排雷 / 护城河 / 估值 / 财报三表体检报告，"
        "可选 AI 多角色辩论。数据来自 AkShare（东方财富/同花顺/乐咕），"
        "行情 60s 缓存、财务 24h 缓存；仅供参考，不构成投资建议。"
    )

    _render_input_area()

    code = st.session_state.get("diag_active_code")
    if not code:
        return
    data = st.session_state.get("diag_data_{}".format(code))
    if not data:
        return

    _render_overview(data)

    tabs = st.tabs(TAB_NAMES)
    with tabs[0]:
        _render_fundamental_tab(data)
    with tabs[1]:
        _render_minefield_tab(data)
    with tabs[2]:
        _render_moat_tab(data)
    with tabs[3]:
        _render_valuation_tab(data)
    with tabs[4]:
        _render_reports_tab(data)
    with tabs[5]:
        _render_ai_tab(data)


def _render_input_area():
    c1, c2, c3 = st.columns([2, 2, 1], vertical_alignment="bottom")
    with c1:
        st.text_input("股票代码", key="diag_stock_code", placeholder="如 600519", max_chars=6)
    with c2:
        st.selectbox("快速示例", list(EXAMPLE_STOCKS.keys()), key="diag_sample")
    with c3:
        st.button("🔍 开始诊断", key="diag_start", type="primary", use_container_width=True)
    st.caption("数据源：AkShare（东方财富/同花顺/乐咕），财务数据 24h 缓存；仅供参考非投资建议。")

    # 点按「开始诊断」才拉数，避免页面加载即触发 10+ 次请求
    if not st.session_state.get("diag_start", False):
        return

    manual_code = str(st.session_state.get("diag_stock_code", "") or "").strip()
    sample_code = EXAMPLE_STOCKS.get(st.session_state.get("diag_sample"), "") or ""
    code = sample_code or manual_code

    if not _is_valid_code(code):
        st.error("请输入正确的 6 位数字股票代码（如 600519）")
        return

    with st.spinner("正在拉取 5 大引擎数据（首次约 15-40 秒，已缓存则秒回）…"):
        data = build_diagnosis_payload(code)
    st.session_state["diag_data_{}".format(code)] = data
    st.session_state["diag_active_code"] = code


def _render_overview(data):
    stock_info = data.get("stock_info") or {}
    code = data.get("code", "")
    name = stock_info.get("name", "")
    price = stock_info.get("price", 0)

    header = "{}（{}）".format(name, code) if name else code
    if stock_info and price:
        st.subheader("{}　现价 {} 元".format(header, _fmt_num(price)))
    else:
        st.subheader(header)
        st.warning("该股票可能停牌或无行情数据，以下为财务口径数据（如有）。")

    fs = data.get("fundamental_score") or {}
    rr = data.get("risk_rating") or {}
    ms = data.get("moat_scores") or {}
    val = data.get("valuation") or {}

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        total = fs.get("total_score")
        stars = fs.get("stars")
        st.metric("基本面评分",
                  "{}/100".format(total) if total is not None else "--",
                  delta="⭐ {} 星".format(stars) if stars else None)
    with c2:
        st.metric("排雷评级", rr.get("level", "--"))
    with c3:
        st.metric("护城河等级", ms.get("level", "--"))
    with c4:
        st.metric("估值状态", val.get("valuation_status", "--"))

    if data.get("errors"):
        with st.expander("部分引擎获取失败（已自动降级展示）", expanded=False):
            for err in data["errors"]:
                st.markdown("- " + str(err))


# ==================== Tab 1 基本面 ====================


def _render_fundamental_tab(data):
    fund_raw = data.get("fundamentals") or {}
    fs = data.get("fundamental_score")
    adv = data.get("adv_risks")
    latest = ((data.get("financials") or {}).get("latest", {})) or {}

    if not fund_raw and not fs:
        st.info("暂无基本面数据")
        return

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("营收（最新期）", _fmt_yi(latest.get("revenue")))
    with c2:
        st.metric("净利润（最新期）", _fmt_yi(latest.get("net_profit")))
    with c3:
        st.metric("ROE", _fmt_pct(fund_raw.get("roe")))
    with c4:
        st.metric("毛利率", _fmt_pct(fund_raw.get("gross_margin")))
    st.caption("口径：营收/净利取财报引擎最新一期；ROE/毛利率/增长率取基本面引擎最新年度（或近 3 年平均）摘要。")

    if fs:
        total = fs.get("total_score", 0)
        stars = "⭐" * (fs.get("stars") or 0)
        _render_badge("基本面综合评分", "{}/100  {}".format(total, stars),
                      fs.get("suggestion_color", "#9CA3AF"))
        st.markdown(fs.get("suggestion", ""))

        for dim_name, dim in (fs.get("details") or {}).items():
            score = dim.get("score", 0)
            max_score = dim.get("max_score", 0)
            with st.expander("{}：{}/{}".format(dim_name, score, max_score), expanded=False):
                for item_name, value, item_score, desc in dim.get("items", []):
                    st.markdown(
                        "- **{}**：{}（{}）　得分 {}/10 满分参考 — {}".format(
                            item_name, _fmt_num(value), desc, item_score, item_score_desc(item_name)))
                st.progress(min(max(score, 0), max_score) / max_score if max_score else 0.0,
                            text="维度得分率 {:.0f}%".format(score / max_score * 100) if max_score else "维度得分率 --")

    if adv:
        st.markdown("#### 💡 优势")
        if adv.get("advantages"):
            for a in adv["advantages"]:
                st.markdown("- 🟢 " + str(a))
        else:
            st.info("暂无突出优势")
        st.markdown("#### ⚠️ 风险")
        if adv.get("risks"):
            for r in adv["risks"]:
                st.markdown("- 🔴 " + str(r))
        else:
            st.info("未发现明显基本面风险")
        st.markdown("#### 📌 一句话总结")
        st.markdown("**" + str(adv.get("summary", "")) + "**")


def item_score_desc(item_name):
    """明细项满分映射（仅用于展示参考）"""
    max_map = {
        "ROE": 10, "毛利率": 8, "净利率": 7,
        "营收增长率": 12, "净利润增长率": 13,
        "资产负债率": 8, "经营现金流/净利润": 7, "有息负债率": 5,
        "市盈率(PE)": 10, "市净率(PB)": 5, "股息率": 5,
        "市值规模": 5, "行业地位": 5,
    }
    return max_map.get(item_name, 10)  # 与 stock_fundamentals 内常量一致


# ==================== Tab 2 排雷 ====================


def _render_minefield_tab(data):
    results = data.get("minefield_results")
    rr = data.get("risk_rating")
    if not results:
        st.info("暂无排雷数据")
        return

    if rr:
        _render_badge("风险评级", rr.get("level", "--"), rr.get("level_color", "#9CA3AF"))
        st.markdown("**" + str(rr.get("summary", "")) + "**")
        cc1, cc2, cc3 = st.columns(3)
        with cc1:
            st.metric("高危", rr.get("high_count", 0))
        with cc2:
            st.metric("中危", rr.get("medium_count", 0))
        with cc3:
            st.metric("低危", rr.get("low_count", 0))
        st.caption("已检查 {} 项 / 安全 {} 项 / 触发 {} 项（阈值与排雷引擎常量一致）".format(
            rr.get("total_checked", 0), rr.get("safe_count", 0), rr.get("total_risks", 0)))

    risk_items = data.get("risk_items") or []
    safe_items = data.get("safe_items") or []

    st.markdown("#### 🔴 触发风险项（{}）".format(len(risk_items)))
    if risk_items:
        for item in risk_items:
            with st.expander(
                    "{} {}（{}风险）".format(item.get("icon", ""), item.get("name", ""),
                                          item.get("level", "")),
                    expanded=False):
                st.markdown("**数据**：" + str(item.get("detail", "--")))
                st.markdown("**说明**：" + str(item.get("explanation", "")))
                st.markdown("**建议**：" + str(item.get("suggestion", "")))
    else:
        st.success("未触发任何风险项 ✅")

    st.markdown("#### 🟢 安全项（{}）".format(len(safe_items)))
    if safe_items:
        with st.expander("查看 {} 项安全项明细".format(len(safe_items)), expanded=False):
            for item in safe_items:
                st.markdown("- **{}**：{}".format(item.get("name", ""), item.get("detail", "--")))

    missing = [r for r in results if not r.get("data_available")]
    if missing:
        st.markdown("#### ⚪ 数据缺失项（{}）".format(len(missing)))
        for item in missing:
            st.markdown("- **{}**：{}".format(item.get("name", ""), item.get("detail", "数据缺失")))

    advice = data.get("minefield_advice")
    if advice:
        st.markdown("#### 📋 综合建议")
        st.markdown(str(advice.get("advice", "")))
        if advice.get("focus_items"):
            for f in advice["focus_items"]:
                st.markdown("- " + str(f))


# ==================== Tab 3 护城河 ====================


def _render_moat_tab(data):
    ms = data.get("moat_scores")
    if not ms:
        st.info("暂无护城河数据")
        return

    total = ms.get("total_score", 0)
    _render_badge("护城河总分 · {}".format(ms.get("level", "--")),
                  "{}/100".format(total), ms.get("level_color", "#9CA3AF"))
    st.markdown("**" + str(ms.get("summary", "")) + "**")

    scores = ms.get("scores") or {}
    details = ms.get("details") or {}
    for name, score in sorted(scores.items(), key=lambda x: -(x[1] or 0)):
        score = score or 0
        detail = details.get(name, {}) or {}
        with st.expander("{}：{}/10".format(name, score), expanded=False):
            st.progress(min(score, 10) / 10.0, text="评分 {} / 10".format(score))
            for di in detail.get("data_items", []) or []:
                st.markdown("- " + str(di))
            for r in detail.get("reasons", []) or []:
                st.markdown("> " + str(r))

    st.markdown("#### 🏆 优势（分项 ≥7）")
    strengths = ms.get("strengths", []) or []
    if strengths:
        for s in strengths:
            st.markdown("- **{}**（{}/10）：{}".format(s.get("name", ""), s.get("score", ""), s.get("reason", "")))
    else:
        st.info("无分项达到 7 分")

    st.markdown("#### ⚠️ 不足（分项 <4）")
    weaknesses = ms.get("weaknesses", []) or []
    if weaknesses:
        for w in weaknesses:
            st.markdown("- **{}**（{}/10）：{}".format(w.get("name", ""), w.get("score", ""), w.get("reason", "")))
    else:
        st.info("无分项低于 4 分")

    if ms.get("advice"):
        st.markdown("#### 📋 建议")
        st.markdown(str(ms["advice"]))


# ==================== Tab 4 估值 ====================


def _render_valuation_tab(data):
    val = data.get("valuation")
    if val:
        price = val.get("price", 0)
        status = val.get("valuation_status", "--")
        color = _value_color(status)
        avg = val.get("avg_fair_price", 0)

        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric("当前价格", _fmt_num(price))
        with c2:
            st.metric("综合合理价", _fmt_num(avg))
        with c3:
            st.metric("价格/合理价", "{}%".format(_fmt_num(val.get("price_ratio"), 1)))

        _render_badge(
            "估值结论",
            "{}　（安全边际 {}%）".format(status, _fmt_num(val.get("margin_of_safety"))),
            color)
        st.caption(
            "买入参考价 {} 元 / 卖出参考价 {} 元；有效估值方法 {} / {}。"
            "行情 60s 缓存，估值与东财同源、可能有延迟。".format(
                _fmt_num(val.get("buy_price")), _fmt_num(val.get("sell_price")),
                val.get("valid_methods", 0), val.get("total_methods", 5)))
    else:
        st.info("数据不足，无法估值（可能停牌或无行情数据）：估值引擎依赖实时行情 PE/PB，"
                "行情接口不可用时降级，以下历史分位为独立数据源补算。")

    # 历史分位（§5.4-A 补算列，独立于估值引擎；失败降级隐藏）
    percentile = data.get("percentile")
    st.markdown("#### 📈 历史分位（近 5 年，页面补算）")
    if percentile and (percentile.get("pe") is not None or percentile.get("pb") is not None):
        p1, p2 = st.columns(2)
        with p1:
            if percentile.get("pe") is not None:
                st.metric("PE(TTM) 历史分位", "{}%".format(percentile["pe"]))
            else:
                st.metric("PE(TTM) 历史分位", "--")
        with p2:
            if percentile.get("pb") is not None:
                st.metric("PB 历史分位", "{}%".format(percentile["pb"]))
            else:
                st.metric("PB 历史分位", "--")
        st.caption("近 5 年 {} 个交易样本；当前值百分位 = (≤现值的样本数/总数)；数据截止 {}，延迟 1 日。"
                   "数据源：乐咕乐股（stock_a_lg_indicator）或百度股市通（stock_zh_valuation_baidu，接口移除时回退）。".format(
                       percentile.get("n", 0), percentile.get("latest_date", "--")))
    else:
        st.caption("历史分位获取失败，以下各方法中的 PE 百分位为「相对合理 PE」口径"
                   "（当前 PE ÷ 合理 PE × 100），非历史分位。")

    if not val:
        return

    st.markdown("#### 🧮 五种估值方法")
    results = val.get("results", []) or []
    if not results:
        st.info("暂无有效估值方法结果（数据不足）")
        return
    for r in results:
        fair = r.get("fair_price")
        dev = r.get("deviation")
        title = "{}｜合理价 {} 元".format(r.get("method_name", ""), _fmt_num(fair))
        if dev is not None:
            title += "（偏离 {}%）".format(_fmt_num(dev))
        with st.expander(title, expanded=False):
            st.markdown("**适用范围**：" + str(r.get("applicable", "--")))
            st.markdown("**公式**：" + str(r.get("formula", "--")))
            st.markdown("**说明**：" + str(r.get("description", "--")))
            if "pe_percentile" in r:
                st.caption("PE 百分位（相对合理 PE 口径）：{}%".format(
                    _fmt_num(r.get("pe_percentile"))))


# ==================== Tab 5 财报三表 ====================


def _render_reports_tab(data):
    reports = data.get("reports")
    financials = data.get("financials")
    if not reports or reports.get("error") or not financials:
        msg = str((reports or {}).get("error", "")) if reports and reports.get("error") else ""
        st.info("暂无财报数据" + ("（" + msg + "）" if msg else ""))
        return

    latest = financials.get("latest", {}) or {}
    history = financials.get("history", {}) or {}

    labels = history.get("labels", []) or []
    if labels and labels[0] and str(labels[0]) != "0":
        st.caption("最新报告期：{}（财务摘要「按年度」口径）".format(labels[0]))

    st.markdown("#### 🗂️ 最新一期核心指标")
    rows = [
        ("营业收入", _fmt_yi(latest.get("revenue"))),
        ("净利润", _fmt_yi(latest.get("net_profit"))),
        ("扣非净利润", _fmt_yi(latest.get("deduct_profit"))),
        ("毛利率", _fmt_pct(latest.get("gross_margin"))),
        ("净利率", _fmt_pct(latest.get("net_margin"))),
        ("ROE", _fmt_pct(latest.get("roe"))),
        ("每股收益 EPS", _fmt_num(latest.get("eps"), 4)),
        ("资产负债率", _fmt_pct(latest.get("debt_ratio"))),
        ("经营现金流", _fmt_yi(latest.get("operating_cf"))),
        ("货币资金", _fmt_yi(latest.get("cash_equivalents"))),
        ("存货", _fmt_yi(latest.get("inventory"))),
        ("应收账款", _fmt_yi(latest.get("accounts_receivable"))),
    ]
    # 用 markdown 表格（不依赖 pyarrow；避免 numpy2 + 旧版 pyarrow 环境下 st.dataframe 崩溃）
    st.markdown(_md_table(rows, ["指标", "数值"]))

    # 勾稽检查：资产 = 负债 + 权益
    st.markdown("#### ⚖️ 勾稽检查")
    ta, tl, eq = latest.get("total_assets"), latest.get("total_liab"), latest.get("equity")
    if ta and tl is not None and eq is not None and ta != 0:
        diff = ta - (tl + eq)
        ratio = abs(diff) / abs(ta) * 100
        if ratio < 1:
            st.success("✅ 勾稽通过：资产 = 负债 + 权益（差异 {:.2f}%，属报告口径舍入误差）".format(ratio))
        else:
            st.warning("⚠️ 勾稽存在差异：资产 -（负债+权益）= {}（{:.2f}%），"
                       "可能系少数股东权益等报告口径差异所致".format(_fmt_yi(diff), ratio))
    else:
        st.caption("勾稽检查需资产/负债/权益三项齐全（当前数据缺失）")

    # 近 5 年趋势（markdown 表格展示，不依赖 pyarrow 图表）
    st.markdown("#### 📈 近 5 年趋势（营收/净利 · 毛利率/ROE）")
    labels = history.get("labels", []) or []
    rev_list = history.get("revenue", []) or []
    np_list = history.get("net_profit", []) or []
    gm_list = history.get("gross_margin", []) or []
    roe_list = history.get("roe", []) or []
    trend_rows = []
    max_len = max(len(rev_list), len(np_list), len(gm_list), len(roe_list), len(labels))
    for i in range(max_len):
        lbl = labels[i] if i < len(labels) else "第{}期".format(i + 1)
        rv = rev_list[i] / 100000000 if i < len(rev_list) and rev_list[i] else None
        np_ = np_list[i] / 100000000 if i < len(np_list) and np_list[i] else None
        gm = gm_list[i] if i < len(gm_list) else None
        ro = roe_list[i] if i < len(roe_list) else None
        trend_rows.append((
            str(lbl),
            "{:.1f}亿".format(rv) if rv else "--",
            "{:.1f}亿".format(np_) if np_ else "--",
            _fmt_num(gm, 1, "%"),
            _fmt_num(ro, 1, "%"),
        ))
    if trend_rows:
        st.markdown(_md_table(trend_rows, ["报告期", "营收(亿)", "净利(亿)", "毛利率", "ROE"]))
    else:
        st.info("暂无趋势数据")

    b1, b2, b3 = st.columns(3)
    growth_trend, growth_desc = analyze_growth_trend(financials.get("growth_rates", {}) or {})
    profit_trend, profit_desc = analyze_profit_trend(history)
    with b1:
        st.markdown("**📈 成长趋势**　" + str(growth_trend))
        st.caption(str(growth_desc))
    with b2:
        st.markdown("**💰 盈利趋势**　" + str(profit_trend))
        st.caption(str(profit_desc))
    with b3:
        st.markdown("**🛡️ 财务健康**")
        st.caption("健康度判断见下方明细")

    health, health_details, health_risks = analyze_financial_health(financials)
    _render_badge("财务健康评级", health,
                  {"健康": "#10B981", "一般": "#3B82F6",
                   "存在风险": "#FBBF24", "风险较高": "#EF4444"}.get(health, "#9CA3AF"))
    for name, value, desc, level in health_details or []:
        icon = HEALTH_ICONS.get(level, "⚪")
        st.markdown("- {} **{}**：{} — {}".format(icon, name, _fmt_num(value), desc))
    if health_risks:
        st.markdown("**风险提示**：" + "；".join(str(r) for r in health_risks))


# ==================== Tab 6 AI 辩论 ====================


def _build_stock_data(data):
    """构造 multi_agent_stock_analysis 的 stock_data（全部字符串标量，规避 NaN/DataFrame）"""
    fund_raw = data.get("fundamentals") or {}
    fs = data.get("fundamental_score") or {}
    rr = data.get("risk_rating") or {}
    ms = data.get("moat_scores") or {}
    val = data.get("valuation") or {}
    latest = ((data.get("financials") or {}).get("latest", {})) or {}
    stock_info = data.get("stock_info") or {}

    price = stock_info.get("price", 0)
    market_cap = fund_raw.get("market_cap")
    market_cap_text = "{}亿".format(_fmt_num(market_cap, 0)) if market_cap else "--"

    fundamentals_text = "ROE {}/ 毛利率 {}/ 净利率 {}/ 营收增速 {}/ 净利增速 {}".format(
        _fmt_pct(fund_raw.get("roe")), _fmt_pct(fund_raw.get("gross_margin")),
        _fmt_pct(fund_raw.get("net_margin")), _fmt_pct(fund_raw.get("revenue_growth")),
        _fmt_pct(fund_raw.get("profit_growth")))

    percentile = data.get("percentile")
    pe_pct_text = ("PE 历史分位 {}%（近5年）".format(percentile["pe"])
                   if percentile and percentile.get("pe") is not None
                   else "历史分位获取失败，PE 为相对合理 PE 口径")

    valuation_text = "PE {}/ PB {}/ 股息率 {}/ 综合估值: {}（合理价 {} 元）".format(
        _fmt_num(fund_raw.get("pe")), _fmt_num(fund_raw.get("pb")),
        _fmt_pct(fund_raw.get("dividend_rate")),
        val.get("valuation_status", "--"), _fmt_num(val.get("avg_fair_price")))

    minefield_text = "已查 {} 项，触发 {} 项，风险评级: {}".format(
        rr.get("total_checked", "--"), rr.get("total_risks", 0),
        _plain_level(rr.get("level")))

    strengths = ms.get("strengths", []) or []
    moat_text = "总分 {}/100（{}），最强: {}".format(
        ms.get("total_score", "--"), _plain_level(ms.get("level")),
        strengths[0].get("name", "--") if strengths else "--")

    reports_text = "营收 {} / 净利 {} / 负债率 {} / 经营现金流 {}".format(
        _fmt_yi(latest.get("revenue")), _fmt_yi(latest.get("net_profit")),
        _fmt_pct(latest.get("debt_ratio")), _fmt_yi(latest.get("operating_cf")))

    return {
        "现价/市值": "{} 元 / {}（行情 60s 缓存）".format(_fmt_num(price), market_cap_text),
        "基本面": fundamentals_text,
        "估值": valuation_text + "；" + pe_pct_text,
        "排雷": minefield_text,
        "护城河": moat_text,
        "财报": reports_text,
    }


def _render_ai_tab(data):
    code = data.get("code", "")
    name = ((data.get("stock_info") or {}).get("name")) or ""

    if not API_KEY:
        st.markdown(
            '<div class="card-gold"><div class="card-title">🔑 AI 辩论未配置</div>'
            '<div style="font-size:13.5px;color:var(--text-2);line-height:1.9;">'
            '还没有配置 DeepSeek API Key，AI 辩论暂时不可用。<br/>'
            '1. 在项目目录创建 <code>local_env.bat</code>，写入：<code>set DEEPSEEK_API_KEY=你的key</code><br/>'
            '2. 设置系统环境变量 <code>DEEPSEEK_API_KEY</code> 后重启终端</div></div>',
            unsafe_allow_html=True,
        )
        return

    st.markdown(
        "点击按钮后，4 位分析师（基本面📊/技术📈/情绪💬/风控🛡️）将基于上方 "
        "5 大引擎数据**依次**分析并辩论（约 30-60 秒），本页不做并行流式展示。")
    if st.button("🤖 开始多智能体辩论", key="diag_ai_start", type="primary"):
        with st.spinner("4 位分析师辩论中（约 30-60 秒）…"):
            from utils.ai_helper import multi_agent_stock_analysis
            stock_data = _build_stock_data(data)
            result = multi_agent_stock_analysis(code, name, stock_data)
        if not result or not result.get("success"):
            st.error("AI 辩论失败，请稍后重试")
            return
        _render_ai_result(result)

    # ---- Agent 化 MVP：基于这份诊断追问（带当前股票 + 会话记忆摘要 进 agent_run）----
    st.markdown("---")
    st.markdown("#### 💬 基于这份诊断追问")
    st.caption(
        "结合上方 5 大引擎诊断数据 + 跨页会话记忆，向 AI 提出进一步问题"
        "（例如：结合排雷和估值，这只股票目前的主要风险是什么？）。"
        "AI 会先规划步骤，再自动调用诊断/估值/排雷等工具，给出有数据支撑的回答。")
    follow_text = st.text_input(
        "追问内容", key="diag_follow_text",
        placeholder="例如：最大的风险点是什么？现在值得关注吗？")
    if st.button("基于这份诊断追问 →", key="diag_follow_go", type="primary"):
        follow = str(follow_text or "").strip()
        if not follow:
            st.warning("请先输入追问内容")
        else:
            _run_diag_follow_up(code, name, data, follow)


def _run_diag_follow_up(code, name, data, question):
    """当前股票诊断 + 最近 3 条会话记忆摘要 → agent_run 追问（记忆续写同一会话）。"""
    import json as _json

    from utils.agent_core import agent_run
    from utils.agent_memory import build_memory_context

    stock_data = _build_stock_data(data)
    memory_ctx = build_memory_context()
    context = [
        "当前股票：{}（{}）".format(name, code) if name else "当前股票：{}".format(code),
        "当前诊断数据（页面 5 大引擎已拉取，AI 也可按需再调工具核实）：",
        _json.dumps(stock_data, ensure_ascii=False),
    ]
    if memory_ctx:
        context.append(memory_ctx)

    session_id = st.session_state.get("diag_agent_session_id")
    with st.spinner("AI 正在基于诊断思考并调用工具（首次约 15-40 秒）…"):
        try:
            result = agent_run(
                question, context=context, memory=True,
                session_id=session_id, continue_question=True,
            )
        except Exception as e:  # noqa: BLE001 - 页面降级
            st.error("AI 追问失败：{}".format(e))
            return

    if result.get("session_id"):
        st.session_state["diag_agent_session_id"] = result["session_id"]

    tool_trace = result.get("tool_trace") or []
    if tool_trace:
        with st.expander("🔧 本次调用工具 {} 次".format(len(tool_trace)), expanded=False):
            for t in tool_trace:
                st.markdown("**{}**　`{}`".format(
                    t.get("name", ""),
                    _json.dumps(t.get("arguments") or {}, ensure_ascii=False)))
                st.code(str(t.get("output", ""))[:800], language="json")

    content = result.get("content") or ""
    if content:
        st.markdown(content)


def _render_ai_result(result):
    analyst_reports = result.get("analyst_reports") or {}
    for _role_key, rep in analyst_reports.items():
        st.markdown("#### {} {}".format(rep.get("icon", ""), rep.get("name", "")))
        st.markdown(str(rep.get("report", "")))
        st.markdown("---")

    st.markdown("#### ⚖️ 主席辩论 + 综合判断")
    st.markdown(str(result.get("debate", "")))

    rating = result.get("rating", "未评级")
    color = RATING_COLORS.get(rating, "#9CA3AF")
    _render_badge("综合评级", rating, color)


if __name__ == "__main__":
    main()
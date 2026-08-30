# 综合诊断（diagnosis 尖刀页）实现方案

> 项目：invest-concierge（投资私人管家）｜文件：`docs/DIAGNOSIS_PLAN.md`
> 阶段：第一阶段 · 只做规划（本轮未修改任何产品代码）
> 依据：`fund_agent-融合版计划-给zcode-20260830.md` §5④（diagnosis 验收清单）、§2 定案#5（多 Agent 辩论）、§6（UI 架构决策）+ 5 个数据引擎 + `utils/ai_helper.py` 实读核实
> 核实方式：读码 + 本机 `D:\work\AN\python.exe` 导入冒烟（5 引擎 + pandas/akshare/streamlit 版本）+ `pytest tests/ -q`（36 用例全绿）

---

## 0. 结论摘要（TL;DR）

- 路由零改动：`web_agent.py` PAGES 已注册 `"stock_diagnosis": "pages.stock_diagnosis"`（web_agent.py:27），`render_page` 调用 `module.main()`（web_agent.py:46-51）；侧边栏「🔬 智能分析 → 🩺 综合诊断」已有（sidebar.py:79）。**唯一交付物是重写 `pages/stock_diagnosis.py`**，§6 双轨导航重构（W2①）只要求 key 保持 `stock_diagnosis` 不变。
- 5 引擎真实导出已逐一核实（见 §3 接线表），全部可 import；`multi_agent` 的 TypeError bug 已在 8/31 修复（`call_llm` + `result["type"]`，ai_helper.py:365/397），现代码正确。
- **发现 2 个与 §5④ 验收锚点的真实冲突**（详见 §5.4 / §6），需在抽查时记录或提前拍板：
  1. 护城河引擎评分模型下茅台预计总分约 50-55 分（落「较宽护城河」档），**不满足"总分落最高档（≥80）"锚点** —— 品牌/规模分项高（≥7 分）、网络/资源分项≈0，天然结构性偏低；
  2. 估值引擎 `pe_percentile` 是"当前 PE / 合理 PE ×100"（stock_valuation.py:134），**不是历史分位**，不满足"输出含历史分位"锚点 —— 需页面补一个轻量历史分位（用 `ak.stock_a_lg_indicator` 近 N 年 PE/PB 序列计算），失败则降级并标注口径。
- 环境注意：本机 `D:\work\AN\python.exe`（numpy 2.4.6 / pandas 3.0.3 / akshare 1.18.64 / streamlit 1.58.0），pyarrow/numexpr/bottleneck 有 `_ARRAY_API` 不兼容告警但导入可用、pandas 实测可用、pytest 36 绿 —— 见 §6-B。

---

## 1. 现状核实（读码结论）

| 项 | 结论 | 证据 |
|---|---|---|
| 页面占位 | `pages/stock_diagnosis.py` 仅 17 行，`main()` 输出一句 "即将上线" info | 实读 |
| 路由 | 已注册、可被 `render_page` 加载（需有 `main`） | web_agent.py:27 / 46-51 |
| 侧边栏 | 「综合诊断」按钮已存在，`nav_stock_diagnosis` → `st.session_state.page` | sidebar.py:79 / 93-95 |
| 入口链 | `app.py` → `web_agent.main()` → `render_sidebar()` + `render_page()` | app.py:12-15 |
| multi_agent | `multi_agent_stock_analysis(stock_code, stock_name="", stock_data=None)`，内部 4 分析师 + 1 主席顺序调 `call_llm`，返回 `{success, analyst_reports{}, debate, rating}`；`result["type"]` 判断已正确 | ai_helper.py:335-413 |
| 基线 | `pytest tests/ -q` = 36 用例全绿（本轮实测）；5 引擎 import 冒烟零报错 | 本轮执行 |
| 引擎导出 | 见 §3 接线表（真实签名 + 返回结构 + 缓存 TTL） | 本轮 dir() 核实 |

**分层约束**（fund-agent-architecture 技能）：页面只做编排与展示；所有数据获取走 `data/` 层函数；AI 调用走 `utils/ai_helper.py`；页面禁止直接 SQL。

---

## 2. 页面布局设计

### 2.1 页面骨架（单个 `main()` 编排）

```
main()
├─ st.title("🩺 综合诊断") + 风险提示 caption（保留现值占位页风格）
├─ 输入区（st.columns(4, vertical_alignment="bottom")）：
│   ├─ st.text_input("股票代码", key="diag_stock_code", placeholder="如 600519", max_chars=6)
│   ├─ st.selectbox("示例", ["手动输入", "600519 贵州茅台", "300750 宁德时代"])  ← 快速抽查入口（可选）
│   ├─ st.button("🔍 开始诊断", key="diag_start", type="primary")
│   └─ st.caption("数据源：AkShare（东方财富/同花顺/乐咕），财务数据 24h 缓存；仅供参考非投资建议")
├─ 校验：6 位纯数字；非法 → st.error 并 return
├─ 统一拉取阶段：st.spinner("正在拉取 5 引擎数据，首次约 15-40s，请稍候…")
│   └─ 一次性聚合 5 引擎结果 → st.session_state["diag_data_{code}"]（见 2.2 数据流）
├─ 停牌/无行情分支：stock_info is None 或 price<=0 → 顶部 st.warning，tabs 仍渲染（见 §6-D）
├─ 总览带（4 张 st.metric 卡片，一行）：
│   ├─ 基本面评分（xx/100 · ⭐x）
│   ├─ 排雷评级（安全/低/中/较高/高风险 · 颜色）
│   ├─ 护城河等级（超级/宽阔/…）
│   └─ 估值状态（低估/合理/高估 · 价格 vs 合理价）
└─ tabs = st.tabs(["📊 基本面", "⚠️ 排雷", "🏰 护城河", "💰 估值", "📑 财报三表", "🤖 AI 辩论"])
    每个 tab 一个独立渲染函数 _render_xxx(diag_data)；数据缺失 → st.info("暂无数据")，异常 → st.error(友好提示)
```

### 2.2 数据流与缓存编排（关键决策）

- **一次性统一拉取**：进入页面后点「开始诊断」才拉数（避免页面加载即触发 10+ 次 AkShare 请求）。
- 结果存 `st.session_state["diag_data_{code}"]`（dict，含 5 引擎子结果 + stock_info），**切 tab / rerun 不重拉**；代码变更时清 key。
- **为什么统一拉取而非懒加载**：§5④「基本面」锚点要求展示**营收/净利绝对值**，而 `get_stock_financial_data()` 只返回比率（无营收/净利字段，已核实）——营收/净利只能从 `financial_report.extract_core_financials().latest` 取。即**基本面 tab 复用财报引擎数据**，两 tab 共享一份数据，统一拉取可避免重复请求。
- 超时降级：若总耗时 > 阈值（如 45s），退化为「逐 tab 首次激活懒加载 + session_state 缓存」（见 §6-E 时间盒）。
- `@cached` 均为**进程内内存缓存**：财务/排雷/护城河/估值 24h，行情 60s（data/cache.py:11-41）。同一天抽查反复跑会命中缓存，抽查时需看数据日期。

### 2.3 UI 规范对齐（streamlit-ui 技能）

- 深色卡片体系直接复用现有 `ui_components/styles.py` 注入的 `.card` / stMetric / stTabs 样式（web_agent.main 已 `inject_global_css()`），**无需新增 CSS**。
- 涨跌色遵循 A 股约定：涨绿 `#10B981` / 跌红 `#EF4444`（styles.py 已有 `.text-up/.text-down`）。
- 状态处理：拉数据 `st.spinner`；引擎单点失败 → 该 tab/card `st.info("暂无数据")`；整页异常 → `st.error("数据获取失败，请稍后重试")`，不回显 traceback。
- 表格：优先 `st.dataframe`（样式已美化）；组件库 `ui_components/data_table.py` 的 `render_data_table` 可复用于 >10 行清单（如排雷明细）。

### 2.4 各 tab 内容设计（与引擎返回结构一一对应）

| Tab | 内容块 | 数据来源（引擎真实函数） |
|---|---|---|
| 📊 基本面 | 顶部 4 metric（营收/净利/ROE/毛利率）；维度得分卡（盈利能力/成长性/财务健康/估值水平/行业地位，各 `score/max_score` + 明细 items）；优势/风险/一句话总结 | `get_stock_financial_data` → `calculate_fundamental_score` → `get_advantages_and_risks`；营收/净利取 `extract_core_financials` |
| ⚠️ 排雷 | 风险评级横幅（level+颜色+summary）；风险项列表（`get_risk_items`，level 色点 + detail 实数 + explanation + suggestion）；安全项折叠（`get_safe_items`）；综合建议 | `get_financial_minefield_data` → `check_minefields` → `calculate_risk_rating` / `get_risk_items` / `get_safe_items` / `get_comprehensive_advice` |
| 🏰 护城河 | 总分 + 等级横幅；6 类型评分条（scores dict，每类 reasons/data_items 进 expander）；strengths / weaknesses / advice | `get_moat_analysis_data` → `calculate_moat_scores` |
| 💰 估值 | 综合结论（估值状态/合理价/买入价/卖出价/安全边际）；5 种方法结果卡（`results[]`，各含 fair_price/deviation/formula）；**历史分位列（补算，见 §5.4-B）**；注明数据延迟 | `calculate_comprehensive_valuation`（内部 `get_valuation_data` + 5 个 `calculate_xxx_valuation`） |
| 📑 财报三表 | 三表核心指标表（latest：营收/净利/扣非/毛利率/净利率/ROE/EPS/资产负债率/经营现金流…）；**勾稽检查"资产=负债+权益"**；近 5 年趋势线（营收/净利/毛利率/ROE，`history` + `growth_rates`）；成长趋势/盈利趋势/财务健康三块结论（各自 trend+描述） | `get_financial_reports` → `extract_core_financials` → `analyze_growth_trend` / `analyze_profit_trend` / `analyze_financial_health` |
| 🤖 AI 辩论 | **按钮触发**（"🤖 开始多智能体辩论"，避免页面加载即烧 5 次 LLM）；无 key 时显示引导卡（复用 `utils/common.ai_not_configured_message`）；有 key → `st.spinner` → 4 分析师报告卡（icon+name+markdown）+ 主席辩论卡 + 评级徽章（强烈推荐/推荐/中性/谨慎/回避 五色） | `multi_agent_stock_analysis(code, name, stock_data)`（stock_data 见 §4） |

---

## 3. 引擎接线表（真实导出，已 import 核实）

> 调用约定：全部为模块级函数，直接 `from data.xxx import foo`。`@cached` 为内存缓存，TTL 见 data/cache.py。

### 3.1 基本面 `data/stock_fundamentals.py`

| 函数 | 签名 | 返回 | 备注 |
|---|---|---|---|
| `get_stock_financial_data` | `(stock_code) -> dict` | `{roe, gross_margin, net_margin, revenue_growth, profit_growth, debt_ratio, cashflow_ratio, interest_debt_ratio, pe, pb, dividend_rate, market_cap(亿), industry, industry_rank, stock_name}`，缺数据为 None | `@cached(CACHE_FUNDAMENTALS=24h)`；内部多接口 + `time.sleep(0.3)`×7 ≈ 3-6s；**无营收/净利绝对值字段** |
| `calculate_fundamental_score` | `(financial_data) -> dict` | `{total_score(100), stars, suggestion, suggestion_color, dimensions{5维}, details{维度:{score,max_score,items[(名,值,得分,描述)]}}}` | 纯函数，无 I/O |
| `get_advantages_and_risks` | `(score_result, financial_data) -> dict` | `{advantages[], risks[], summary}` | 得分率 ≥80% 算优势、<30% 算风险 |

### 3.2 排雷 `data/financial_minefield.py`（8 大雷区）

| 函数 | 签名 | 返回 | 备注 |
|---|---|---|---|
| `get_financial_minefield_data` | `(stock_code) -> dict` | 存贷/商誉/现金流/应收/质押/审计/毛利率史/存货 + `_raw_data`，缺项为 None | `@cached(CACHE_MINEFIELD=24h)`；内部 5 接口直接 `ak.xxx`（部分未包 retry），异常仅 print 不抛 |
| `check_minefields` | `(data) -> list[8]` | 每项 `{id,name,level,level_color,icon,is_risk,data_available,detail,explanation,suggestion}` | 纯函数 |
| `calculate_risk_rating` | `(results) -> dict` | `{level,level_color,summary,high_count,medium_count,low_count,total_risks,safe_count,total_checked}` | 安全/低/中/较高/高风险五档 |
| `get_risk_items` / `get_safe_items` | `(results) -> list` | 过滤触发项/安全项 | |
| `get_comprehensive_advice` | `(risk_rating, risk_items) -> dict` | `{advice, focus_items[]}` | |

**阈值核对（读码，与 §5④"阈值与引擎常量一致"对齐）**：存贷双高 = 货币资金/总资产>30% 且 有息负债/总资产>30%；商誉>30% 净资产；经营现金流/净利<50%；质押>50%；毛利率 3 年波动>10pct；存货增速>2×营收增速且有营收(或营收下滑仍增存货)；应收账款同理；审计意见非标准无保留。— 文件内常量即 UI 展示值，勿在页面重写阈值。

### 3.3 护城河 `data/moat_analysis.py`（6 大护城河）

| 函数 | 签名 | 返回 | 备注 |
|---|---|---|---|
| `get_moat_analysis_data` | `(stock_code) -> dict` | 基础财务 + 行业对比 `{industry_avg_gross_margin, industry_avg_net_margin}` 等 | `@cached(CACHE_MOAT=24h)`；**慢**：`_get_industry_data` 遍历同行业前 20 家公司循环拉财务摘要（0.1s sleep ×20 + 网络）≈ 10-25s |
| `calculate_moat_scores` | `(data) -> dict` | `{scores{品牌/技术/规模/网络/转换成本/资源 各0-10}, details(每类reasons,data_items), total_score(100), level, level_color, summary, strengths[], weaknesses[], advice}` | 纯函数；档次：≥80 超级 / ≥60 宽阔 / ≥40 较宽 / ≥20 微弱 / else 无 |

### 3.4 估值 `data/stock_valuation.py`

| 函数 | 签名 | 返回 | 备注 |
|---|---|---|---|
| `get_valuation_data` | `(stock_code) -> dict \| None` | `{stock_info, financial_data, eps, bvps, dps, profit_growth, revenue_growth, pe, pb, roe, dividend_rate, market_cap, industry, stock_name, price}` | `@cached(CACHE_VALUATION=24h)`；**`get_stock_info` 失败直接返回 None**（停牌/无行情分支入口） |
| `calculate_pe_valuation` / `calculate_pb_valuation` / `calculate_peg_valuation` / `calculate_dividend_valuation` / `calculate_dcf_valuation` | `(data) -> dict` | `{valid, method_name, fair_price, deviation, formula, ...}` 或 `{valid:False, reason}` | 纯函数；已由 tests/test_valuation.py 锚定（13 例） |
| `calculate_comprehensive_valuation` | `(stock_code) -> dict \| None` | `{data, price, results[], avg_fair_price, median_fair_price, price_ratio, valuation_status, valuation_color, suggestion, buy_price, sell_price, margin_of_safety, valid_methods, total_methods}` | `@cached`；内部调 `get_valuation_data`，None 时返回 None |

⚠️ 口径提示：`pe_percentile`（PE 法结果内）= **当前 PE/合理 PE ×100**（stock_valuation.py:134），非历史分位；`base_pe/base_pb` 由 ROE+行业映射决定（白酒 PE 基准 30、PB 基准 5.0，已被单测锚定），**页面展示时必须保留 formula 行，勿把 pe_percentile 标注为"历史分位"**。

### 3.5 财报三表 `data/financial_report.py`

| 函数 | 签名 | 返回 | 备注 |
|---|---|---|---|
| `get_financial_reports` | `(stock_code) -> dict` | `{profit_sheet, balance_sheet, cashflow_sheet, financial_abstract(均为 DataFrame), stock_name, error}` | **无 @cached**（模块内未装饰）——页面侧务必只调一次并缓存；内部 3 个 0.3s sleep ≈ 2-3s |
| `extract_core_financials` | `(reports) -> dict` | `{latest{revenue, net_profit, gross_margin, net_margin, total_assets, total_liab, equity, parent_equity, cash_equivalents, inventory, debt_ratio, eps, operating_cf, ...}, history{labels, revenue[], net_profit[], gross_margin[], roe[], debt_ratio[], operating_cf[]}, growth_rates{...}}` | 纯函数；**勾稽三要素 total_assets/total_liab/equity 都在 latest** |
| `analyze_growth_trend` | `(growth_rates) -> (trend, desc)` | 正向增长/稳定/减速/波动/负增长 | 纯函数 |
| `analyze_profit_trend` | `(history) -> (trend, desc)` | 提升/稳定/下降/波动 | 纯函数 |
| `analyze_financial_health` | `(financials) -> (health, details[], risks[])` | 健康/一般/存在风险/风险较高 | 纯函数 |
| `build_financial_summary_text` | `(financials) -> str` | 拟好的人类可读摘要 | 可直接作为 AI 上下文（可选） |
| `format_number` | `(value, unit='') -> str` | 自动转 万/亿 | 展示复用 |

---

## 4. multi_agent 接线与 stock_data 注入

### 4.1 调用方式（ai_helper.py:335）

```python
from utils.ai_helper import multi_agent_stock_analysis
result = multi_agent_stock_analysis(stock_code, stock_name, stock_data)
# result = {
#   "success": True, "stock_code":..., "stock_name":...,
#   "analyst_reports": {"fundamentals": {"name","icon","report"}, "technical":..., "sentiment":..., "risk":...},
#   "debate": "...", "rating": "强烈推荐|推荐|中性|谨慎|回避|未评级",
# }
```

- 内部流程：4 分析师**顺序**调 `call_llm`（非并行）→ 拼接报告 → 主席辩论 1 次 = **共 5 次 LLM 调用**；评级由关键词抽取（ai_helper.py:400-404）。
- 已修复：`call_llm` 返回 `{"type":"text"|"tool_call", "content":...}`，按 `result["type"]` 判断（ai_helper.py:365/397）——8/31 已修，tests/test_multi_agent.py 锚定"5 次调用顺序 + 降级行为"（2 例，本轮全绿）。
- **流式**：`multi_agent_stock_analysis` 本身无流式；W2② 的"流式结论卡"（≤1 人天）不在本页本轮范围。本页先用 `st.spinner("4 位分析师辩论中（约 30-60s）…")` 一次性出结果。

### 4.2 stock_data 构造（页面侧聚合 5 引擎 → 注入）

`stock_data` 会被逐条 `str(key) + ": " + str(value)` 拼进每个分析师的 system prompt（ai_helper.py:350-355）。**约束：值必须是可 str 的标量；严禁传 DataFrame / NaN（会显示为 "nan"）**。建议构造为**字符串化摘要 dict**（页面侧格式化为定值，天然规避 NaN）：

```python
stock_data = {
    "现价/市值": "1498.00 元 / 18828 亿（行情 60s 缓存）",
    "基本面": "ROE 30.1% / 毛利率 91.0% / 净利率 52.3% / 营收增速 15.7% / 净利增速 15.4%",
    "估值": "PE 25.3 / PB 8.2 / 股息率 3.2% / 综合估值: 合理（合理价 xx 元）",       # 历史分位失败时注明口径
    "排雷": "8 项已查，触发 0 项，风险评级: 安全",
    "护城河": "总分 52/100（较宽护城河），品牌 10/规模 9 最强",                     # 如实传引擎输出
    "财报": "营收 1505.6 亿 / 净利 747.3 亿 / 负债率 22.1% / 勾稽通过",
}
```

- 注入时机：仅 AI 辩论 tab 被用户点「开始辩论」按钮时构造并调用；**无 key（`config.API_KEY` 为空）时不构造、不调用**，直接渲染引导卡（见 §6-A）。
- 历史分位若已补算成功，把"PE 历史分位 45%（近 5 年）"并入估值行。

---

## 5. 3 样本股抽查计划（对齐 §5④ 锚点）

### 5.0 前置门（先于一切抽查）

1. `python -c "import pandas, akshare; print('OK')"` 冒烟（当前已通过；若在别机跑，见 §6-B 环境告警说明）。
2. `pytest tests/ -q` 全绿（当前 36 用例已绿）。
3. 5 引擎逐个 `import` 冒烟（当前已通过）。

### 5.1 A · 贵州茅台 600519

| 引擎 | 抽查动作 | 对齐锚点（§5④ 原文） | 备注（引擎预判） |
|---|---|---|---|
| 基本面 | 页面展示 ROE/毛利率/净利率/营收/净利 vs 东财 F10 同报告期 | 毛利率 **~91% 量级**、ROE **~30% 量级** | `stock_financial_abstract_ths("按年度")` 取最新年度；营收/净利来自财报引擎 latest，与 F10 营收/归母净利对照同报告期 |
| 排雷 | 8 项 detail 逐项核对实数 | 触发雷区 **≤1** | 预判 0-1 触发（最可能为"存货异常增长"低危：茅台基酒库存逐年增，增速或超营收增速；其余 7 项结构上安全/无质押数据） |
| 护城河 | 总分、等级、分项排序 | 总分落**最高档**；**品牌/定价权分项不低于其他维度** | ⚠️ **预估冲突**：品牌 10、规模 9-10、转换成本 ~5、技术 ~3-4、网络 ~2、资源 0 → 总分约 50-55（较宽护城河，非 ≥80 超级档）→ **见 §6-F 决策**；品牌分项排名第一（≥7 分）部分可满足 |
| 估值 | 页面 PE/PB vs 东财实时值偏差 ≤5%；输出含历史分位 | 偏差 **≤5%**（延迟注明）；**输出含历史分位** | PE/PB 与东财同源（`stock_zh_a_spot_em`），偏差≈0；历史分位为补算列，口径见 §5.4-B |
| 财报三表 | 勾稽校验 + 货币资金/存货量级 | **资产=负债+权益** 自动通过；货币资金/存货量级与年报一致 | latest 三项齐全即校验；茅台货币资金千亿级、存货约几百亿级（以最近年报为准） |
| AI 辩论 | 有 key：5 次调用出评级；无 key：引导卡 | 无 key 数据标签照常、AI 位引导卡 | 见 §6-A |

### 5.2 B · 宁德时代 300750

| 引擎 | 锚点/期望 | 备注 |
|---|---|---|
| 基本面 | 与东财 F10 同报告期一致（ROE ≈ 20%±、毛利率 ≈ 20-25%，以实际报告期为准，**不为锚点硬编码**） | 报告期对齐注意：spot PE 动态口径 vs 年度 ROE 口径不同，抽查时注明口径 |
| 排雷 | 记录实际触发项（预判 1-2 个：应收账款/存货增长、现金流比值），每项 detail 附实数 | |
| 护城河 | 技术/规模分项应较高（研发费用占营收 ~5-7% → 技术 5-6 分；市值/营收规模大 → 规模 8-10 分） | 行业"电池/电力设备" → 转出成本 map 可能命中 |
| 估值 | 偏差 ≤5%；历史分位可用 | 新能源行业 PE 基准 25 |
| 三表 | 勾稽通过；记录货币资金/存货量级 | |
| AI | 同上 | |

### 5.3 C · 问题股 = 执行日现筛 ST（§5④ 原文：不预设代码）

- 筛法：`ak.stock_zh_a_spot_em()` 全市场行情 → 名称含 "ST"（含 *ST）→ 按"成交额"倒序取 top 1-2 → 手工确认代码后填入页面抽查。
- 排雷：**触发 ≥2 且每项附实数依据**（预期命中的雷区：审计意见非标（重点）、存贷双高、现金流差、大股东质押高 —— 视个股而定）；任何 `data_available=False` 项也要截图记录（开源可信度证据链）。
- 其余 4 引擎 + AI：允许数据缺失/评级低，**不得 traceback**；整页友好提示可用。

### 5.4 两个锚点缺口的补算/处置方案

**A. 历史分位（估值 tab）**
- 补算：页面侧工具函数用 `ak.stock_a_lg_indicator(symbol=code)`（乐咕乐股，茅台可查多年 PE/PB/股息率序列，后复权口径）取近 N 年（默认 5 年）交易日 PE(TTM)/PB 序列 → 当前值百分位 `(<=当前值的样本数/总数)`。展示为"PE 历史分位 xx%（近 5 年，乐咕口径，延迟 1 日）"。
- 失败降级：接口挂/序列不足 → 隐藏历史分位列，改在 caption 注明"历史分位获取失败，已显示相对合理 PE 的百分位"（引用引擎 `pe_percentile`，标注"相对合理 PE"），不阻断页面。
- 该函数放 `pages/stock_diagnosis.py` 内部工具即可（页面级展示工具，不污染 data 层；若执行阶段觉得值得复用再上移 data/）。

**B. 护城河档位与"最高档"锚点冲突**
- 事实：茅台受引擎 6 维模型结构性约束（品牌/规模高、网络/资源≈0），预估总分 50-55 → 「较宽护城河」（≥40 档），**达不到"最高档（≥80）"**。
- 处置选项（执行阶段须先与用户确认，二选一）：
  1. **不改引擎**：抽查记录真实输出，verification.md 注明"锚点与引擎评分模型冲突，茅台落「较宽」档系模型设计使然（品牌/规模分项排名第一且 ≥7 分）"，验收口径改为"品牌分项不低于其他维度 ✓"；
  2. **小改引擎**：`_score_network_moat` / `_score_resource_moat` 行业映射补"白酒/酿酒"维度的合理归属（如转换成本 map 增加'白酒' → 提 2-3 分，且品牌高毛利率联动网络分），预计总分可拉到 ≥60（宽阔档），但仍难到 ≥80 —— **"最高档"是硬指标的话必须重设计评分权重，超出本页范围，需单独立项**。
- 本方案默认按**选项 1**（不改引擎、如实记录、verification.md 留痕），并把选项 2 列为待确认。

### 5.5 抽查产出

- 每引擎四步自检（壳→抽查→修→回归）落 `docs/verification.md`（§5④ 整页门明确要求"抽查结论写入 docs/verification.md"）。
- 整页门：3 样本全 6 tab 零 traceback；无数据/停牌友好提示；`streamlit run app.py` 手动全流程 1 遍（录屏素材留给 W2 截图）。
- 时间盒：每引擎 ≤0.5 人天；护城河行业对比慢 → 抽查时预判超时，降级"核心 3 指标"（§5④ 末条）。

---

## 6. 风险点与降级策略

### A. 无 key 降级（验收硬项）
- 检测注入点：`from config import API_KEY`（config.py:34，读 `DEEPSEEK_API_KEY` 环境变量，空串视为未配置）。
- AI tab 未配置 → 直接渲染引导卡（复用 `utils/common.ai_not_configured_message()`，说明 local_env.bat / 环境变量两种配置法），**不调用 multi_agent**——否则无 key 时 `call_llm` 会立即返回同一条"请先配置 API Key"文本 ×5 次（ai_helper.py:40-41），产出 5 份假报告 + 1 份主席垃圾文，既浪费又难看。
- 5 个数据 tab 完全不受 AI key 影响，照常渲染（§5④：无 key 数据标签照常出）。

### B. AkShare / 环境波动
1. **本机依赖栈**：`D:\work\AN\python.exe` 为 numpy 2.4.6 + pandas 3.0.3 + akshare 1.18.64 + streamlit 1.58.0；pyarrow/numexpr/bottleneck 存 `_ARRAY_API` 不兼容告警（编译期 NumPy 1.x），但本轮实测 pandas/akshare 导入与 DataFrame 运算可用、pytest 36 绿。**执行阶段第一步 = 复跑前置门**；若换机/CI 出现 `_ARRAY_API not found` 崩于运行期 → `pip install "numpy<2"` 或升级受影响包，并把结论写回 verification.md。
2. **接口级容错**：引擎内部多数接口带 `try/except` + `call_akshare_with_retry`（部分 minefield/report 内直接 `ak.xxx` 未包 retry，异常仅 print 不抛）→ 失败结果置 None → **页面必须对 None 全字段防御**（格式化函数统一 `None → "--"`/`st.info("暂无数据")`），这是"整页门零 traceback"的关键。
3. **网络/限频**：`stock_zh_a_spot_em` 全市场行情（约 5000 行）单次 2-5s；护城河行业对比 20 家循环 10-25s；排雷/财报内部多接口 2-4s——整页首拉 15-40s 属正常，spinner 文案要诚实提示。
4. **缓存时效**：行情 60s / 财务 24h 内存缓存 → 盘中抽查 PE/PB 可能与东财网页差一个缓存窗口；页面 caption 固定注明"行情 60s 缓存、财务 24h 缓存"。跨 rerun 缓存存活（进程内），代码变更后重启 Streamlit 才能清。
5. **报告期口径**：财务摘要"按年度"最新一期 vs 东财 F10"最新报告期（可季报）"可能不同 → 基本面/三表 tab 显示报告期标签（`history.labels[0]`、财务摘要行首列），抽查以同报告期对比为准。

### C. 停牌/异常代码分支
- 触发条件：`get_stock_info` 返回 None（代码不在全市场列表：退市/未上市/代码错）或 `price<=0`（停牌中，spot 里最新价为 0）。
- 处置：顶部 `st.warning("该股票可能停牌或无行情数据，以下为财务口径数据（如有）")`；tabs 仍渲染；`get_valuation_data` 内部 `if not stock_info: return None`（stock_valuation.py:37-38）→ `calculate_comprehensive_valuation` 返回 None → 估值 tab 显示"数据不足，无法估值"，**不崩**；其余 4 引擎可继续出财务数据（AkShare 财务接口不依赖 spot）。
- 代码校验：页面输入 6 位数字 + 首位 ∈ {0,3,6,8,9} 提示（不强制阻断）。

### D. 数据缺失分级展示
- 整引擎失败 → 该 tab `st.error` + "点击重试"（重试即重跑该引擎并覆盖 session_state 子项）。
- 单项缺失 → 表格单元格 "--"，不整卡隐藏；排雷 `data_available=False` 项仍显示并标注"数据缺失"。

### E. 时间盒与范围管控（§4 硬性约束）
- 每引擎壳 2h + 抽查 1-2h；护城河行业对比超时 → **降级为只展示核心 4 维（品牌/技术/规模/转换成本）**，不拖累 9/5 线。
- 本页交付面仅 `pages/stock_diagnosis.py`（+ `docs/verification.md` 抽查结论）；**不改 web_agent.py / sidebar.py / 任何 data 引擎**（除 §6-F 用户拍板的小改）。

### F. 待确认决策清单（执行阶段开始前）
1. 护城河"最高档"锚点冲突：默认不改引擎如实记录（选项 1）？还是授权小改行业映射（选项 2）？
2. 历史分位补算：是否同意页面侧用 `ak.stock_a_lg_indicator` 补近 5 年分位列（失败即降级隐藏）？
3. 抽查脚本形态：手工核对 + verification.md 记录（默认），还是额外写 `tools/verify_diagnosis_samples.py` 自动化脚本？
4. AI tab 的 4 分析师是否要在本页做并行/逐步展示（默认不做，留给 W2② 流式改造）？

---

## 7. 执行阶段步骤预览（阶段二启用，本阶段未实施）

1. **前置门**：5 引擎 import 冒烟 + `pytest tests/ -q` 全绿（基线已确认）。
2. **骨架**：重写 `pages/stock_diagnosis.py` —— 输入区 + 校验 + session_state 编排 + 6 tabs 骨架 + 停牌分支（先跑通 `streamlit run app.py` 空壳零报错）。
3. **逐引擎接线（按 基本面 → 排雷 → 护城河 → 估值 → 三表 顺序，每接一个即用 3 样本抽查）**：
   - 接基本面/三表（共享一份财报数据）→ 抽查 A/B/C 锚点；
   - 接排雷 → 抽查（重点 C 样本 ST ≥2 触发）；
   - 接护城河 → 抽查 + 记录档位冲突（§6-F-1）；
   - 接估值 → 抽查偏差 ≤5% + 历史分位列；
   - 期间核对 §5④ 时间盒，超盒降级核心 3 指标。
4. **AI tab**：无 key 引导卡 → 有 key `multi_agent_stock_analysis` + stock_data 注入 → 4 分析师卡 + 主席卡 + 评级徽章。
5. **整页门**：3 样本全 6 tab 零 traceback + 无 key 全站可用 + 手动 `streamlit run app.py` 全流程 1 遍。
6. **留痕**：抽查结论写入 `docs/verification.md`（对齐 §5④ 清单逐项打勾 + 差异记录）。

---

## 8. 验收映射（§5④ 全程对照）

| §5④ 验收项 | 方案落点 |
|---|---|
| 前置门：5 引擎 import 冒烟零报错 + pytest 全绿 | §5.0（已实测通过） |
| 基本面：营收/净利/ROE/毛利率 vs F10；锚点 91%/30% | §3.1+§3.5 共享数据、§5.1-A |
| 排雷：600519 ≤1、ST ≥2 且附实数、阈值与引擎一致 | §3.2 阈值核对、§5.1/5.3 |
| 护城河：600519 总分最高档；品牌分项不低 | §3.3、§5.1-A、§6-F-1（冲突记录） |
| 估值：偏差 ≤5%、延迟注明、输出含历史分位 | §3.4、§5.4-A（补算列+降级） |
| 财报三表：勾稽自动通过；货币资金/存货量级 | §3.5、§5.1-A |
| 整页门：3 样本零 traceback、无数据/停牌提示、无 key 数据照常 + AI 引导卡、注册 PAGES + 侧边栏可见、手动全流程 1 遍、抽查结论入 verification.md | §2 布局/§6、§5.5（路由已注册零改动） |
| 时间盒：每引擎 0.5 人天，超盒降级核心 3 指标 | §6-E |
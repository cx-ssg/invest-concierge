# 21 个功能的能力归属设计 —— Agent 优先，页面只做它做不了的

> 2026-09-04 · 待拍板
> 起因：用户洞察——「21 个功能都是给 agent 赋能的，agent 能自主调用。要不要个别功能拎出来？」
> 本文逐个回答：哪些只做工具不做页面、哪些必须拎出来做页面、哪些两者都要。

## 0. 一句话结论

**方向反转为「Agent 优先」：15 个隐藏功能里，12 个的正确归宿是接成 agent 工具（对话即功能，零新页面）；只有 3 类拎出来做页面——需要"持续看"的（行情）、需要"反复改"的（自选/预警）、数据量大到文字装不下的（K 线）。**

核心理由：这些功能本来就是"引擎函数"，它们最大的价值不是变成 12 个新页面（导航更拥挤、每页都要打磨、发布日期后移），而是**让 AI 对话的回答更有数据支撑**——「我的持仓怎么样」能自动查行情，「这基金值得定投吗」能自动跑回测。这正是本项目对外的第一卖点。

## 1. 事实盘点（实查代码，非印象）

### 1.1 agent 现在就能调的 11 个工具（TOOL_REGISTRY）

| 工具 | 能力 | 覆盖的隐藏页 |
|---|---|---|
| get_fund_info | 单基金净值/涨跌 | — |
| get_market_index | 大盘指数 | 市场行情（部分） |
| load_funds | 持仓列表 | 持仓分析（部分） |
| get_stock_diagnosis | 6 引擎打包 | 深度分析（部分） |
| get_stock_valuation | 估值明细 | — |
| get_stock_minefield | 8 雷区排雷 | — |
| get_stock_moat | 护城河 6 维 | — |
| compare_funds | 双基金对比（Markdown） | 对比分析（部分） |
| get_market_sentiment | 涨跌停/连板/涨跌家数 | 市场全景（部分） |
| add_diary / get_diary | 日记写/读 | — |

**注意：compare_funds 的工具描述里明确写着"返回 Markdown 文本不是 JSON"——M0 已做 `compare_funds_structured()` 纯函数版，工具应切过去。**

### 1.2 引擎就绪但 agent 调不到的（改 nav_service 不如改 TOOL_REGISTRY）

| 引擎函数 | 文件 | 能力 | 对应隐藏页 |
|---|---|---|---|
| search_fund(keyword) | fund_api | 名称/代码/拼音搜基金 | 基金搜索 |
| search_stock(keyword) | stock_api | 股票搜索 | 股票搜索 |
| get_stock_info(code) | stock_api | 个股行情 | 深度分析 |
| get_stock_kline(code,days,ktype) | stock_api | K 线 OHLC | — |
| get_fund_history(code,days) | fund_api | 基金历史净值 | — |
| calc_fund_metrics(code) | fund_api | 收益率/回撤/夏普 | 持仓分析 |
| backtest_strategy / dca_result | fund_api | 定投/一次性回测 | 回测工具/定投计算器 |
| get_hot_sectors() | market_api | 板块涨跌 | 市场全景 |
| get_valuation_data() | market_api | 指数估值分位 | 市场行情 |
| get_market_moneyflow / get_sector_moneyflow | moneyflow_api | 大盘/板块资金 | 市场全景 |
| get_north_moneyflow_details | moneyflow_api | 北向资金 | 市场全景 |
| get_stock_moneyflow(code) | stock_api | 个股资金 | 深度分析 |
| get_limit_up_stocks / get_limit_up_review_data | limit_up_api | 涨停复盘 | 市场全景 |
| identify_dragon_stocks 等 12 个 | dragon_api | 打板/龙头/情绪周期 | 投资工具箱 |
| watchlist CRUD | database | 自选股增删 | 自选股 |
| load_alert_settings | database | 预警配置读写 | 预警设置 |

## 2. 融合分析：一页不增，功能全在

用户问的"有没有融合的地方"——**有，而且幅度很大**。15 个隐藏页按"用户与这个数据的交互方式"重新归并，只剩 4 种真实需求：

| 真实需求 | 吞掉的原页面 | 载体 |
|---|---|---|
| **问出来**（低频、要解释） | 基金搜索、股票搜索、对比分析、定投计算器、回测工具、持仓分析、深度分析、工具箱 | agent 工具，不需要页面 |
| **盯出来**（高频、开着看） | 市场行情、市场全景 | 1 个页面：市场行情（指数/板块/情绪/资金/估值分 tab） |
| **攒出来**（结构化列表） | 自选股、持仓股票、预警设置 | 并入现有页面：自选股并入持仓页 tab；预警并入设置页 |
| **还没想好** | 个人中心、定投管理 | 定投管理其实是"回测+日记"组合，agent 能做；个人中心砍掉（Roadmap 表态即可） |

净效果：**15 页 → 1 个新页面 + 2 处现有页面扩展 + 12 个 agent 工具**。导航从 21 项继续收敛。

## 3. 要不要个别拎出来？（逐个判决）

### 3.1 拎出来做页面的（3 个，理由是 agent 做不好）

| 功能 | 为什么 agent 做不了/做不好 |
|---|---|
| **市场行情** | 行情是"开着看"的：盘中小窗常驻、扫一眼涨跌红绿。让用户每天问 agent"今天大盘怎么样"是倒退。且 React 已有侧栏 3 行指数的底子 |
| **自选股** | 列表是"攒"出来的：增删改查要表格 UI。agent 语音式交互做自选股管理效率低 |
| **预警设置** | 表单类交互（阈值/开关），页面向；且预警本质是后台任务（cron 轮询），页面只是配置入口 |

### 3.2 不拎出来、只做工具的（12 个，理由：对话体验 ≥ 页面体验）

- **基金搜索/股票搜索**：用户对 agent 说"帮我找白酒相关的基金"→ search_fund 返回 → agent 拼上下文直接给推荐候选。页面版还得自己输入关键词、看表格、复制代码——对话版更快。**这正是搜索类功能的终局形态**。
- **对比分析**：说"161725 和 110022 哪个好"天然就是对话。页面版反而要填两个代码框。工具切 `compare_funds_structured`。
- **定投计算器/回测工具**：说"161725 每月 1000 定投 3 年能赚多少"→ dca_result 返回 dict → agent 给结论+风险提示。页面版要填 3 个表单字段，且结论没人帮你解读。
- **持仓分析**：说"我的持仓怎么样"（已有 load_funds）→ 增强为 load_funds + calc_fund_metrics 组合，agent 给配置点评。这个最有演示杀伤力。
- **深度分析**：个股追问已由诊断工具族覆盖（get_stock_info/kline/moneyflow 补全最后缺口）。
- **市场全景**：说"今天板块资金流向谁最强""北向在买什么""涨停复盘"——工具化（moneyflow/limit_up/dragon_api）。
- **投资工具箱**：dragon_api 12 个函数全部工具化（打板/龙头/阶段判定），这类"游资向"功能做成独立页面反而敏感（荐股观感），藏在 agent 工具里由用户主动问，边界更干净。
- **定投管理**：= 回测工具 + add_diary（"帮我把这次定投记到日记"），无需独立页面。

### 3.3 合并掉的（不做任何东西）

- **个人中心**：无真实用户价值（本地单机没有画像可聚合），Roadmap 标"待评估"即可，不实现。

## 4. 分期计划（发布后节奏，不阻塞 9/15）

### P1「对话能力升级」（发布后第一周，约 2-3 天 AI 辅助）
**只加工具，不加页面——发布后第一波传播素材就是它**：

新增 TOOL_REGISTRY 12 个工具（全部现成函数，晚绑定零改动 agent_core）：
1. `search_fund`（关键词搜基金） 
2. `search_stock`（关键词搜股票）
3. `get_fund_metrics`（收益/回撤/夏普——calc_fund_metrics 包装）
4. `get_fund_history`（历史净值）
5. `backtest_dca`（dca_result，dict 直返）
6. `get_stock_info`（个股行情）
7. `get_stock_kline`（K 线，让 agent 能说趋势）
8. `get_stock_moneyflow`（个股资金）
9. `get_market_moneyflow`（大盘/板块资金 + 北向）
10. `get_hot_sectors`（板块）
11. `get_limit_up_review`（涨停复盘）
12. `get_index_valuation`（指数估值分位）

顺手修两刀：compare_funds 工具切 `compare_funds_structured`；load_funds 升级为带 metrics 的持仓快照。
**验收**：对话问 12 个典型问题（见附录）每个都能触发对应工具且回答有数据；pytest 全绿。

**✅ P1 已交付（2026-09-04）**，验收实录：
- pytest **123 全绿**（110 存量 + 13 新增 `tests/test_p1_tools.py`）；
- 12 问真实 SSE（DeepSeek reasoner + 真实行情源）：**工具命中 12/12，且每问首调工具即命中预期**——23 工具规模下 flash 的选择准确率没有出现拍板时担心的下降；
- 无幻觉守则经受住实测：当日东财实时系接口对本机 IP 触发临时反爬封禁（82.push2/push2his 均即断、push2delay 与非东财源正常，自愈型环境问题），涉及 4 问的引擎返回空，Agent 全部如实说明"数据不可得"并给出交叉验证尝试，**零编造**——比"全绿"更有说服力的护栏证据；
- 观察项（拍板留弦）：上线后持续观察"该调没调/调错"比率，若明显再走工具描述分层优化，不砍工具。

### P2「市场行情页」（约 2-3 天）
唯一新页面：指数 + 板块 + 情绪 + 资金 + 估值 五 tab（全部 P1 已工具化的同一批 API，前端接 `/api/market/*`）。`nav_service` 一行 `live: True`。
**验收**：CDP 断言 tab 切换 + 真实行情数据渲染 + 涨跌色正确。

### P3「自选股 + 预警」（约 2-3 天，可与 P2 并行）
- 自选股：持仓页加 tab（复用 DataTable + watchlist CRUD API）
- 预警：设置页加区块（表单 + alert_settings API）；后台轮询做 v1.1（只存配置，不启动任务）

### P4（远期）：龙虎/打板工具族（dragon_api 12 个函数）看社区反馈再定

## 5. 明确不做

- ❌ 不做 12 个独立功能页（导航保持克制，6→7 页为止）
- ❌ 不做荐股式输出（工具描述与 system prompt 延续合规守则：只给分析依据）
- ❌ 个人中心不做
- ❌ 不为工具做"函数级"页面兜底（引擎输出 dict→to_jsonable→agent 组织语言，已验证路径）

## 6. 待拍板（3 个决策点）

1. **P1 的 12 个工具全上还是先上 8 个核心？**（建议全上——全是晚绑定现成函数，风险低，演示密度高）
2. **市场行情页进不进 9/15 首发？**（建议不进——首发 6 页版本，行情页当发布后第一周更新，给仓库持续活跃的信号；但如果传播素材想要行情截图，可以赶）
3. **dragon_api（打板/龙头）要不要在 P1 一起上？**（建议不上——敏感度 + 调试成本，先放 P4 观察）

## 附录：P1 验收 12 问（每问对应一个新工具）

1. "帮我找白酒指数基金" → search_fund
2. "搜一下宁德时代" → search_stock
3. "161725 最近一年表现怎么样" → get_fund_metrics
4. "161725 净值走势如何" → get_fund_history
5. "161725 每月 1000 定投三年能赚多少" → backtest_dca
6. "600519 现在多少钱" → get_stock_info
7. "600519 最近 60 天走势是什么形态" → get_stock_kline
8. "600519 主力资金在流入还是流出" → get_stock_moneyflow
9. "今天北向资金买了什么" → get_market_moneyflow
10. "今天哪个板块最强" → get_hot_sectors
11. "今天涨停复盘" → get_limit_up_review
12. "沪深300 现在估值贵不贵" → get_index_valuation

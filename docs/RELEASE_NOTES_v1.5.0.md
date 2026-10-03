# v1.5.0 —— 龙虎/打板工具族接入（12 个函数 → 7 个 agent 工具，24 → 31）

> 本版为 **minor 版**：把「投资工具箱」那条线上的**游资向数据能力**接入 AI 对话。

## 🆕 新增 7 个 agent 工具（注册表 24 → **31**）

`data/dragon_api.py` 的 12 个业务函数按**数据访问形态**合并为 7 个工具：

| 工具 | 覆盖的原函数 |
|---|---|
| `get_limit_up_pool` | 涨停池（含板块归属） |
| `get_limit_up_detail` | 单票涨停明细 |
| `get_lhb_stats` | 龙虎榜统计 |
| `get_dragon_stocks` | 龙头股（**含阶段判定**；合并了 `calc_board_score` / `judge_stage`） |
| `get_board_list` | 板块列表（含涨停数） |
| `get_board_members` | 板块成分 |
| `get_stock_boards` | 个股所属板块（含概念） |

**实现方式**：只在 `data/dragon_api.py` **尾部加薄适配层**；
`utils/agent_core.py` **结构零改动**（沿用**声明式注册表 + 晚绑定 importlib**）。
**合并依据**见 `report-H6.md §V2`（五条，均可证伪）。

## 🛡️ 边界（按设计原意执行）

`docs/AGENT_TOOLS_PLAN.md §3.2` 的原话是：这类「**游资向**」功能
「做成独立页面反而**敏感（荐股观感）**，**藏在 agent 工具里由用户主动问，边界更干净**」。

本版**严格按此执行**：

- ❌ **不加页面 / 不加导航 / 不在 UI 上主动推荐**
  —— 并由**测试锁住**：`pages/` 与 `frontend/src` 对相关关键字 **0 命中**
- ✅ **每个返回体带 `source` + `risk_note`**（含「**不构成投资建议**」），7 个工具的 description 同款标注

## ✅ 验收

- `python -m pytest -q` → **674 passed**（v1.4.0 时 644）
- `npx tsc -b` exit 0；`npm run build` exit 0
- **真实数据**：`get_limit_up_pool` **52 只** / `get_lhb_stats` **493 行** / `get_dragon_stocks` **52 只**
- ⚠️ **数据源不可达时如实报错**：东财 `push2` / `datacenter` 不可达的 **4 个工具返回 `NOT_FOUND`**，**零假数据**
- golden set **+8 用例**；`MIN_TOOL_COVERAGE = 31`

## ⚠️ 已知边界（如实列出）

- 短线/游资类数据**时效性极强**，且**免费公开接口可能不稳定**
  （本版实测即有 4 个工具因源不可达返回 `NOT_FOUND`）
- 涨停 / 龙虎榜数据**不构成任何投资建议**
- 其余指标口径限制同 **v1.3.2 / v1.4.0**（`prod` 为 oracle 上界、A3a 经 holdout 二次标定等）

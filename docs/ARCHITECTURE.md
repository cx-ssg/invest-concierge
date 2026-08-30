# 架构详解 · invest-concierge

> 生成：2026-08-31（重写自旧 architecture_report.md，旧文档目录树已过时）

## 一、分层结构

```
app.py                      入口（Streamlit 启动点）
 └─ web_agent.py            路由层：PAGES 字典 + 动态 importlib 加载
     ├─ pages/              页面层（20 页，双轨导航渲染 live 集合）
     ├─ ui_components/      UI 组件（sidebar 双轨 / holdings_card / data_table / styles）
     ├─ utils/              工具（ai_helper AI 引擎 / common 安全 / chart / fund_utils）
     └─ data/               数据层（14 模块：行情/财报/估值/资金/情绪/排雷/护城河）
```

## 二、数据流

```
用户输入 → pages/ 页面 → data/ 模块 → (AkShare/新浪/天天基金 免费接口) → DataFrame
                        ↓ 缓存
                 SQLite (database.py) 持久化：持仓/自选/日记/预警
```

- 所有数据模块统一带**内存缓存**（cache.py TTL）与 **fallback**（接口失败降级），页面无原始接口调用。
- 网络失败 → 显示友好提示而非崩溃（`is_safe_public_url` / `is_safe_write_path` 安全函数保证 URL/路径安全）。

## 三、AI 引擎（utils/ai_helper.py）

- `call_llm(prompt, tools, model)`：统一 LLM 入口，OpenAI SDK 兼容 DeepSeek API；支持工具调用；错误分级返回（timeout/connection/401/429）。
- `multi_agent_stock_analysis(code, name, stock_data)`：**核心故事点**——
  1. 4 位分析师独立分析（基本面📊 / 技术📈 / 情绪💬 / 风控🛡️）
  2. 交易决策委员会主席组织辩论
  3. 评级关键词抽取（强烈推荐/推荐/中性/谨慎/回避）
- 无 Key → 页面显示引导卡，不调用（避免空转）。
- 输入 stock_data 为**字符串化摘要 dict**（规避 DataFrame/NaN 兼容问题）。

## 四、双轨导航（ui_components/sidebar.py）

- `PAGE_META`：21 页**轨道归属**（基金轨10 / 股票轨7 / 通用4）——与渲染**解耦**，是 Roadmap 生成源。
- 每页 `live` 标志决定是否进 **v1.0 渲染集**（基金3 + 股票1 + ai_chat + settings；占位页不渲染=不存在）。
- 主页面右上角 `st.segmented_control`（📊基金/📈股票）切换 `session_state.track` → `st.rerun()`。
- 侧边栏按轨渲染：当前轨 live 页 + ⚙️通用专区。

## 五、测试与质量

- `pytest`：44+ 用例（common 安全 / minefield 排雷 / valuation 估值 / multi_agent AI / navigation 双轨 / ai_tools）。
- CI：GitHub Actions 双矩阵（Python 3.9 / 3.11），push 自动跑。
- 发布前 `gitleaks detect` 防 key 泄露（`.env` / `local_env.bat` 已 gitignore）。

## 六、数据源说明

| 数据源 | 用途 | 性质 |
|---|---|---|
| AkShare | A 股行情/财报/资金/情绪 | 免费公开 |
| 天天基金 | 基金净值/估值 | 免费公开 |
| 新浪财经 | 股票行情 | 免费公开 |
| DeepSeek API | AI 对话/辩论（可选） | 需 Key |

*备注：akshare 接口可能随时间变动（如 v1.18.64 移除部分旧接口），页面已做优雅降级显示「数据缺失」，详见 docs/verification.md。*
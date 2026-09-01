# Agent 化 MVP 设计文档 v2.0（pi 底座版 · 2026-08-31）

> **⛔ 状态：已评估 · 否决（2026-08-31 留档，不执行）**
> PoC 4/4 通过（导入/provider/AgentTool/mock seam 均实测），但可持续性核实否决：
> 作者为个人账号（Kisjjw/pi-AgentLoop-py，非 earendil-works 官方移植），1★、发布后即停更——不作发布基座。
> 其 API 设计（follow_up/steer/abort/subscribe/resume）作为参考，已部分吸收进 v1.1 Addendum 的 v1.2 路线。
> **最终路线见 AGENT_MVP_DESIGN.md 的 Addendum（自写基座 + pi 设计参考）。**

> 替代 v1.1：底座从「自写 agent_core」换成「**pi-agent-loop**（@earendil-works/pi-ai 官方 Python 移植）」
> 用户拍板：换 pi 底座 + Python 升 3.10+
> 执行者：zcode（用户拍板；DSH 已交付 v1.1 成果保留，**复用其 Memory/工具/晚绑定**，不推翻）

---

## 0. 版本变更（v1.1 → v2.0）

| 项 | v1.1（自写） | v2.0（pi 底座） |
|---|---|---|
| Agent 循环 | 自写 ~100 行（DSH 已交付，84 测试绿） | **pi-agent-loop 内置**（`await agent.prompt()`） |
| 工具执行 | 自写 execute_ai_tool_v2 | pi 的 **AgentTool 类**（pydantic 参数校验自动容错） |
| 多轮历史 | 自写 Memory（DSH 已交付：agent_sessions/messages） | pi `state.messages` **自动累积**（免 Memory 自写循环层） |
| 事件订阅 | 手写 | pi `subscribe(on_event)`（流式/工具进度/用量） |
| 轮数上限 | max_rounds=8 | pi `stop_after_max_turns()` |
| **保留（不推翻 DSH 成果）** | 11 工具注册/晚绑定/Memory SQLite/_load_diag_data 搬出/diagnosis 追问/28 新测试 | 全部复用 |

**why pi**：基于 pi-ai 的开源 Agent 循环库（`pi-agent-loop`，PyPI 0.1.0）。**非 earendil-works 官方，是个人账号 Kisjjw 的第三方移植**（PyPI 实测，诚实表述）；但 API 质量高（完整类型注解/事件流/steer-follow_up/abort），且 zcode PoC 已当场验证 4/4（依赖导入 / provider / 工具循环 / mock seam）。

**⚠️ PoC 结果（zcode 执行，2026-08-31）**：
| PoC 项 | 结果 | 关键 |
|---|---|---|
| ① 依赖 | ✅ | `pi-agent-loop==0.1.0`+`pi-ai-client==0.1.0`；导入名 `pi_ai`（注意包名与导入名不同） |
| ② Provider/DeepSeek | ✅ | `pi_ai.create_provider(base_url=...)` 原生支持自定义 base_url + ApiKeyAuth——接 DeepSeek 一行配置 |
| ③ Agent 循环/工具 | ✅ | `Agent(model, tools, system_prompt)` + `AgentTool`（pydantic）+ `should_stop_after_turn`（轮数）+ `run_agent_loop_continue`（追问链原生） |
| ④ Mock seam | ✅ | `Agent(stream_fn=...)` 就是模型注入缝——测试传假 StreamFn 跑确定性断言，不用碰 httpx |

**决策门（发布周硬门，zcode 第 3 节）**：PoC 已 4/4 过 → 允许迁移。若迁移中发现阻碍 → **立即回退 v1.1 自写版（84 测试绿）**，pi 顺延 v1.2。Python 3.10 升级与 pi 绑定：**不上 pi 不动 CI/README 矩阵**。

## 1. 技术决策

**Python 3.10+**（pi-agent-loop 硬要求；本机 3.11 ✅）
- 项目声明：README/requirements/CI 矩阵 3.9/3.11 → **3.10/3.11**
- 存量代码兼容：3.10+ 允许 `X | None`，但存量 3.9 代码不动（兼容 3.10 即可跑）

**依赖**：`pip install pi-agent-loop`（自带 pi-ai-client；发布名 pi-agent-loop、导入名 pi_agent_loop）

**LLM Provider 接 DeepSeek**：
- pi-ai 不装厂商 SDK，走 httpx 直连 → **自定义 provider 指向 DeepSeek 官方 / opencode-go 网关**
- `build_model()`（Kisjjw 示例）支持接中转站/自建网关——写一个 `deepseek_provider()` 包装，base_url 可配（DeepSeek 官方 or Go 网关），key 读环境变量（复用 local_env.bat 机制）

## 2. 目标架构（pi 底座版）

```
用户输入（ai_chat 对话 / 诊断页 AI 追问）
        │
        ▼
┌───────────────────────────────────────────────┐
│  pi-agent-loop（底座：循环/历史/容错/事件流）    │
│  Agent(prompt) ── AgentTool[](finit)         │
│    └─ state.messages（多轮自动累积，跨 prompt） │
└──────────────────┬────────────────────────────┘
                   │ 工具调用
┌──────────────────▼────────────────────────────┐
│  Tools 层（复用 DSH 成果 + 适配 AgentTool）     │
│  Tool Registry（11 工具，晚绑定已交付）          │
│  → 包装成 pi 的 AgentTool 类（pydantic param）  │
└──────────────────┬────────────────────────────┘
                   │
┌──────────────────▼────────────────────────────┐
│  持久化（复用 DSH 成果）                        │
│  Memory：agent_sessions/agent_messages + 摘要   │
│  data/ 14 模块 + SQLite                       │
└───────────────────────────────────────────────┘
```

### 3 层复用/新增

**① pi 底座（新）**：`utils/pi_bridge.py`（+ 依赖 pi-agent-loop）
- `build_deepseek_provider()`：DeepSeek/Go 网关自定义 provider
- `create_investment_agent(tools, memory)`：组装 pi Agent（system_prompt 含合规守则"只分析不给买卖指令"+防幻觉"数据不可得更不许编造"）
- `on_event` 订阅：流式输出 → Streamlit 渲染；工具进度 → 时间线

**② 工具适配（复用 DSH 注册表 + 适配 AgentTool）**
- DSH 已交付的 11 工具（晚绑定，`module.fn` 引用 → resolve 调用）
- **适配层**：把注册表条目包装成 `AgentTool[Params, Result]`（pydantic 声明参数——pi 的 AgentTool 需要 pydantic model，这是与 DSH execute_ai_tool_v2 的差异点）
- 保留晚绑定：AgentTool.execute 内动态 resolve（不绑死函数引用，patch 可见）

**③ 持久化（DSH 已交付，原样保留）**
- agent_sessions/agent_messages + init_db + 摘要机制 + 最近 3 条注入规则
- `_load_diag_data`→`data/diagnosis.py`、minefield/moat/情绪打包链、add_diary 封装——全复用

## 3. 工具清单（复用 DSH，适配 AgentTool）

| 工具 | 实现来源 | 适配点 |
|---|---|---|
| get_fund_info / get_market_index / load_funds | DSH 已交付（晚绑定） | AgentTool 包装 + pydantic param |
| get_stock_diagnosis / valuation / minefield / moat | DSH 已交付（打包链） | 同上 |
| compare_funds / get_market_sentiment / add_diary / get_diary | DSH 已交付 | 同上 |

## 4. 诊断页 AI 追问（DSH 已交付，微调）

- 按钮已有（基于这份诊断追问→）
- 微调：`agent_run(memory=True)` → `agent.prompt(question)`（pi 调用）+ `build_memory_context()` 注入最近 3 条摘要

## 5. Docker/依赖（新增注意事项）

- requirements.txt 加 `pi-agent-loop>=0.1.0`
- CI：Python 3.9 矩阵 → 3.10/3.11；`pip install pi-agent-loop` 需网络（CI runner 有）

## 6. 测试（DSH 28 新测复用 + pi 配套）

| 测试 | 断言 |
|---|---|
| test_tool_registry（复用） | ≥10 工具；schema 合法；未知工具不崩 |
| **test_pi_bridge** | build_deepseek_provider 返回可用 provider；Agent 组装成功；on_event 事件触发 |
| **test_agent_pi** | pi Agent + 11 AgentTool：mock 模型 → tool_trace≥3 顺序正确；工具 error → 模型收到 ToolError 且最终"数据不可得"；跨 prompt 多轮历史累积（state.messages 增长） |
| test_memory（复用 DSH） | 落库/摘要/最近3条注入 |
| test_ai_tools（复用，兼容） | 存量 12 用例全绿（晚绑定保证 patch 生效） |

## 7. 验收标准（确定性可测）

- [ ] `pip show pi-agent-loop` → 0.1.0；`python -c "from pi_agent_loop import Agent"` 无错
- [ ] `python -c "from utils.pi_bridge import build_deepseek_provider; assert build_deepseek_provider()"` → pass
- [ ] 确定性（test_agent_pi）：mock 模型 → 单任务 tool_trace ≥3、顺序正确；全工具 error → 最终含"数据不可得"不编造；两次 `agent.prompt()` 后 `state.messages` 增长（历史自动累积）
- [ ] 记忆落库（复用 DSH）：agent_sessions/messages 可查；诊断追问 system prompt 注入最近 3 条摘要
- [ ] 诊断页「AI 追问」→ 带当前股票 + 记忆进 pi Agent
- [ ] `pytest tests/` 全绿（存量 56 + DSH 新增 28 + 新增 pi 配套）
- [ ] 无 Key：全链路不崩提示配置
- [ ] 前端验收：ai_chat 输入"帮我诊断600519的估值" → 看到工具时间线 + 流式输出 + 数据依据结论

## 8. 实施注意（zcode 必读）

1. **pi 与 DSH 成果的桥**：DSH 的 execute_ai_tool_v2（注册表晚绑定）→ 包成 AgentTool 时，`execute` 方法内动态 resolve，**不要**在 AgentTool 构造时绑函数（否则 mock patch 失效——沿用 DSH 晚绑定教训）
2. **AgentTool 参数：动态生成，别手写 11 个模型**（zcode ⑤）：DSH 注册表已有 OpenAI function schema → `pydantic.create_model()` 动态生成（string/number/integer/boolean→Python 类型，enum→Literal），适配层 ~30 行；**单一事实源仍是 DSH 注册表**
3. **Provider 自定义（范围收缩，zcode ⑦）**：MVP 只接 **DeepSeek 官方**（base_url=api.deepseek.com + key 环境变量），"opencode-go 网关"砍到 v1.2；参考 pi-agent 作者自己的 build_model() 示例（**当参考不当权威**）
4. **⚠️ async × Streamlit 桥接（zcode ①，必炸点）**：`await agent.prompt()` 是 async，Streamlit 是同步逐轮 rerun。**每轮重构 provider/agent（不复用 async 对象）**——否则 `asyncio.run()` 新建事件循环绑死旧对象，"attached to a different loop"必炸。MVP 事件收集→完成后渲染工具时间线，真流式（st.write_stream 接 async 生成器）放 v1.2
5. **⚠️ 双记忆源同步（zcode ②，核心卖点）**：pi `state.messages` 是**进程内**，Streamlit rerun 即丢且无可靠会话结束钩子。**写死：每次 `agent.prompt()` 完成后立即把本轮消息 append 进 `agent_messages`（flush-per-prompt），SQLite 是唯一事实源，pi state 只是工作集**；新会话从 DB 重建历史——否则"越用越懂"漏掉
6. **⚠️ 超时（zcode ⑧）**：`agent.prompt()` 包 120s 超时（Agent 卡住不能转天荒地老）；工具级沿用现有 timeout
7. **依赖钉死（zcode ④）**：`pi-agent-loop==0.1.0` + `pi-ai-client<0.2`（0.1.x minor 都可能破 API）
8. **import 失败自动回退（zcode ⑥）**：`try: import pi_agent_loop` 失败 → `chat_with_tools` 自动走 DSH 自写循环；回滚从"git revert"降级为"pip uninstall"；加 `AGENT_BACKEND=auto|pi|builtin` 环境变量
9. **mock seam（zcode ③，已消）**：`Agent(stream_fn=...)` 注入假 StreamFn 即可测试，不加 model_override（避免走 httpx 层 mock）
10. **不回退**：当前工作树已有 DSH 完整自写版（84 测试绿）——**先 commit 保底**再动 pi 迁移；pi 失败可回滚（v1.1 仍可用）
11. **Python 3.10 升级**：需求文件/CI/README 一起改，别漏；**不上 pi 就不动 3.10 矩阵**（决策门）

## 9. 风险

| 风险 | 应对 |
|---|---|
| pi-agent-loop 0.1.0 太新（本周发布）可能 bug | **保底：DSH 自写版先 commit**；pi PoC 验证 3 个核心（provider/Agent/工具循环）再全量迁 |
| 工具数 11 的 pydantic 适配工作量 | 描述复用，只加 pydantic 声明层 |
| 3.10 升级影响 CI | 矩阵改 3.10/3.11，本地实测 |

---

**执行者**：zcode
**前置**：①先 commit 当前 DSH 成果（保底）②装 pi-agent-loop ③PoC 验证 3 核心 ④全量迁移
**确认后**：zcode 按此实现 + 自测 → Reasonix 验收
# Agent 化 MVP 设计文档（Web 版 · 2026-08-31）

> 基于：豆包设计 v0.1（Tool Registry / Agent Core / Memory）→ 裁剪为 Web 版可执行
> 不做的：桌面 GUI（v0.2，砍）、主动定时产出（v0.3，后置）
> 目标：**"越用越懂的投资私人顾问"** —— AI 能调用全部数据能力 + 记住你的持仓/偏好

---

## 0. 一句话

把现在「3 个硬编码工具 + 单轮问答」升级为「**10-12 个声明式工具 + 带规划的 Agent 循环 + 跨页会话记忆**」——AI 从"能聊"变成"能干活"。

## 1. 现状（代码事实，非推断）

| 组件 | 现状 | 位置 |
|---|---|---|
| 工具 | `AI_TOOLS` 只有 3 个（get_fund_info / get_market_index / load_funds） | utils/ai_helper.py:457 |
| 执行 | `execute_ai_tool` 用 **if/elif 硬编码**分派（加工具=改代码） | ai_helper.py:494 |
| 循环 | `chat_with_tools` 多轮循环，**max_tool_rounds=4** | ai_helper.py:515 |
| 记忆 | 仅 `st.session_state`（刷新即丢，对话/诊断互不相通） | pages/ai_chat.py |
| 数据能力 | **14 个 data 模块 + database 5 表** 已就绪，90% 未暴露给 LLM | data/ |

**本质缺口**：数据能力强（14 模块）、单点 AI 有（对话+辩论），但**没有把它们连起来的调度层**——这层就是 Agent。

## 2. 目标架构（Web 版，与 UI 解耦可测）

```
用户输入（ai_chat 对话 or 诊断页"AI 追问"）
        │
        ▼
┌─────────────────────────────────────────────┐
│  Agent Core（新层 utils/agent_core.py，UI 无关）│
│  ┌────────────┐   ┌─────────────┐           │
│  │ Planner    │   │ Memory      │           │
│  │ 规划/多步   │   │ SQLite 持久化│           │
│  └─────┬──────┘   └──────┬──────┘           │
│        ▼                 │                  │
│  ┌────────────────────┐  │                  │
│  │ Tool Registry 注册表 │◄─┘                  │
│  └─────────┬──────────┘                     │
└────────────┼────────────────────────────────┘
             ▼ 调用
┌─────────────────────────────────────────────┐
│  Tools 层（现有业务函数，仅注册暴露）           │
│  data/ 14 模块 + database 5 表               │
└─────────────────────────────────────────────┘
```

### ① Tool Registry（核心改造）

**声明式注册**替代 if/elif：

```python
# utils/agent_core.py
def _schema(name, description, params, required=()):  # 帮手机
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": params, "required": list(required)}}}

TOOL_REGISTRY = {
    # ⚠️ 晚绑定（zcode 评审强制）：fn 存"模块.函数名"字符串，调用时 import 解析。
    #   原因：存量 test_ai_tools.py 用 patch.object(ai_helper, "get_fund_info", ...) 修改模块属性；
    #   若 import 时绑定函数引用，patch 不生效 → 存量测试全挂。
    "get_fund_info":      ToolDef(module="utils.ai_helper", fn="get_fund_info", schema=...),      # 已有
    "get_market_index":   ToolDef(module="utils.ai_helper", fn="get_market_index", schema=...),   # 已有
    "load_funds":         ToolDef(module="utils.ai_helper", fn="_load_funds_from_store", schema=...),  # 已有
    "get_stock_diagnosis": ToolDef(module="data.diagnosis", fn="build_diagnosis_payload", schema=...),  # P0 6引擎打包（从 pages 搬出）
    "get_stock_valuation": ToolDef(module="data.stock_valuation", fn="get_valuation_data", schema=...),  # P0 真名
    "get_stock_minefield": ToolDef(module="data.financial_minefield", fn="minefield_pipeline", schema=...),  # P0 五连链打包
    "get_stock_moat":      ToolDef(module="data.moat_analysis", fn="moat_pipeline", schema=...),  # P1 两步链打包
    "compare_funds":       ToolDef(module="data.fund_api", fn="compare_funds", schema=...),  # P1 注意返回是字符串
    "get_market_sentiment":ToolDef(module="utils.market_sentiment_merged", fn="get_market_sentiment", schema=...),  # P1 三函数打包
    "add_diary":           ToolDef(module="utils.diary_tool", fn="add_diary", schema=...),  # P2 load→append→save 封装
    "get_diary":           ToolDef(module="data.database", fn="load_diary", schema=...),  # P2
    # 首批 11 个；新增工具 = 加一行，不改分派
}

def resolve(fn_ref):
    """晚绑定解析：'module.fn' → 调用时动态查模块属性（patch 可见）"""
    mod_name, fn_name = fn_ref.rsplit(".", 1)
    mod = importlib.import_module(mod_name)
    return getattr(mod, fn_name)

def execute_ai_tool_v2(tool_name, args):
    entry = TOOL_REGISTRY.get(tool_name)
    if not entry: return json.dumps({"error": f"未知工具 {tool_name}"})
    try:
        fn = resolve(entry.fn_ref)
        result = fn(**{k: v for k, v in args.items() if k in entry.param_names})
        return json.dumps(_truncate(result), ensure_ascii=False, default=str)  # 结果截断防撑爆
    except Exception as e:
        return json.dumps({"error": f"工具 {tool_name} 出错: {e}"}, ensure_ascii=False)

def _truncate(result, max_len=8000, list_top_n=20):
    """返回值截断（zcode 评审 §5.2）：长列表 top-N，超长截断，防 prompt 撑爆"""
    ...
```

**兼容**：`AI_TOOLS = [t.schema for t in TOOL_REGISTRY.values()]`（对外 shape 不变，ai_chat 无感知）。

### ② Agent Core（规划循环）

`chat_with_tools` 升级为 `agent_run(task, tools, memory)`：
- **第一轮多一步"计划"**：system prompt 引导 LLM 先简述执行计划（做什么、调哪些工具）→ 再执行
- 逐步执行，每步结果回填 role="tool"
- `max_tool_rounds` 4 → **8**（诊断类需要多步）
- **防呆**：工具返回 `{"error":...}` 时，Agent 必须明说"数据不可得"，不编造（防幻觉守则入 system prompt）
- 支持 `continue_question`（追问链：基于上轮结果再问），供诊断页"AI 追问"复用

### ③ Memory（越用越懂的关键）

- 新表（复用 database.py 模式，SQLite）：
  - `agent_sessions(id, created_at, title, summary)` —— 会话 + 摘要
  - `agent_messages(id, session_id, role, content, created_at)` —— 消息
- **摘要机制**：每会话满 N 轮（如 8）后，让 LLM 生成一句话摘要存 `summary`（防 token 爆）
- **跨页复用**：ai_chat 聊的持仓/偏好 → 诊断页"AI 追问"时读取该会话摘要进上下文
- 兼容无 key：无 key 时 Memory 仍记录（本地存储），AI 生成摘要降级为"最近消息末尾截取"

### ④ 诊断页"AI 追问"

- stock_diagnosis.py 的 AI tab 底部加「基于这份诊断追问 →」按钮
- 点击 → 当前股票诊断结果 + 会话记忆摘要 → 丢给 `agent_run` 追问
- 复用现有 ai_helper，不新开页面

## 3. 首批工具清单（zcode 评审修订：真名核对过）

| 工具 | 对应现有函数（**真实名**） | 优先级 | 备注 |
|---|---|---|---|
| get_fund_info / get_market_index / load_funds | utils/ai_helper（alreadys 含 `_load_funds_from_store`） | 已有 | 迁移+晚绑定 |
| **get_stock_diagnosis** | **data/diagnosis.py 新建 `build_diagnosis_payload`**（从 pages/stock_diagnosis `_load_diag_data` 搬出，6 引擎打包，纯编排无 st） | P0 | ✚ 规避 utils→pages 反向依赖 |
| **get_stock_valuation** | **data/stock_valuation.`get_valuation_data`**（真名） | P0 | ✚ 注意另有 4 个 calculate_* 是内部 |
| **get_stock_minefield** | **data/financial_minefield `minefield_pipeline`（新封装）**：五连链 data→check_minefields→calculate_risk_rating→get_risk_items/get_safe_items→get_comprehensive_advice 打包 | P0 | ✚ 只暴露原始 dict 对 LLM 无用 |
| **get_stock_moat** | **data/moat_analysis `moat_pipeline`（新封装）**：两步 get_moat_analysis_data→calculate_moat_scores | P1 | ✚ |
| **compare_funds** | data/fund_api.`compare_funds` | P1 | ⚠️**返回是字符串**（"获取基金数据失败…"或文本），schema 描述写清 |
| **get_market_sentiment** | **utils/market_sentiment_merged（新）**：打包 `get_limit_up_down_data` + `get_top_board_height` + `get_market_breadth`（三函数均 @cached，无同名主函数） | P1 | ✚ |
| **add_diary** | **utils/diary_tool（新）**：`load_diary→append→save_diary` 封装（database 只有整表 save） | P2 | ⚠️并发覆写风险 MVP 接受，文档标注 |
| **get_diary** | data/database.`load_diary` | P2 | ✚ |

> P0 = 诊断闭环；P1 = 横向对比+情绪；P2 = 锦上添花。新增工具 = 注册表加一行。

## 4. 测试（沿用 mock 风格）

| 测试 | 断言 |
|---|---|
| test_tool_registry.py | 注册表 ≥10 工具；schema 合法（name/description/parameters 齐全）；execute_v2 对未知工具返回 error 不崩 |
| test_agent_core.py | mock call_llm → agent_run 多轮调用顺序正确；error 回填后 Agent 明说"数据不可得"；追问链复用上下文 |
| test_memory.py | SQLite 写入/读取；摘要生成；跨 session 不串 |

## 5. Roadmap（Web 版内）

| 阶段 | 内容 | 进入条件 |
|---|---|---|
| **v1.1（本次）** | Tool Registry(11) + agent_run + Memory + 诊断追问 | 你确认本文档 |
| v1.2 | 主动工作流（"生成我的持仓周报"按钮） | v1.1 稳定 |
| 探索 | 持仓偏好画像（从对话历史提炼用户风格） | v1.2 |

## 6. 风险与守则

1. **合规**：Agent 只做分析辅助，不做自动交易/定时荐股，system prompt 固化"不给买卖指令，只给分析依据"（延续免责声明）
2. **防幻觉**：结论必须基于工具真实返回；工具空/失败 → 明说"数据不可得"
3. **性能**：诊断类首拉 15-40s → 用现有缓存 + 前台"思考中"
4. **token**：11 工具 schema 拉长 prompt → Memory 摘要机制做
5. **无 key 降级**：全链路无 key 不崩（记忆仍存，AI 降级为"未配置提示"）

## 7. 验收标准（zcode 评审修订：确定性可测）

- [ ] `python -c "from utils.agent_core import TOOL_REGISTRY; assert len(TOOL_REGISTRY) >= 10"` → pass
- [ ] **确定性断言（test_agent_core）**：mock call_llm，断言 `agent_run` 单任务触发 **tool_trace ≥3 步**、步骤顺序正确、error 回填后 Agent 明说"数据不可得"不编造；ai_chat 页手测仅做体验复核（CLI 壳砍掉，规避 bare 模式 st.session_state 坑）
- [ ] **Memory 落库可查（确定性）**：写入后 `agent_sessions`/`agent_messages` 表可查；诊断追问的 system prompt **注入了最近会话摘要**（mock 可断言）——注入规则定死：取**最近 3 条会话摘要**，按 session 更新倒序
- [ ] 诊断页「AI 追问」按钮 → 带当前股票上下文进 Agent
- [ ] `pytest tests/` 全绿（**56 存量含 test_ai_tools 的 execute_ai_tool 别名兼容为前提** + 新增 test_tool_registry/test_agent_core/test_memory）
- [ ] 无 Key：全链路不崩，提示配置
- [ ] ✅ 补充（zcode §5.3）：**mock 全工具返回 error → Agent 明说数据不可得且不编造**（防呆守则可测化）

### 实施注意（zcode 评审汇总，DSH 必读）

1. **晚绑定**：Registry fn 存"模块.函数名"，调用时 import 解析——否则 test_ai_tools 的 mock.patch 不生效全挂
2. **兼容旧名**：保留 `execute_ai_tool = 新执行器` 别名 + 3 旧工具错误文案不变（"未找到基金"被测试断言）
3. **_load_diag_data 搬出 pages** → data/diagnosis.py（规避 utils→pages 反向依赖 + import 页面拉起重 streamlit）
4. **缓存不均**：`get_financial_reports` 无 @cached，Agent 诊断可能 60s+——工具层包 TTL 缓存（复用 data/cache.py）或 README/spinner 明示"诊断 60s+ 属预期"
5. **返回值截断**：Registry 统一 `_truncate`（长列表 top-20、超长 8000 截断）防 prompt 撑爆
6. **SQL 参数绑定** + 新表加进 `init_db()`（Mimosa 门禁同款要求）
7. **多标签页并发**：connection 不存 session_state（check_same_thread 炸）；MVP 接受 last-write
8. **Python 3.9+ 兼容**：不用 `X | None` 联合类型，延续 typing.Optional 风格

---

**执行者**：DSH（用户拍板，zcode 额度留发布）
**预计**：3.5-4 人天（分支开发，不碰现有功能）
**确认后**：DSH 按此实现 + 自测 → Reasonix 验收

---

## Addendum（2026-08-31 · 收敛定案与实施结果）

**底座决策**：经 pi 底座评估（docs/AGENT_MVP_DESIGN_v2_pi.md，已否决留档），维持**自写基座**——
pi-agent-loop 为个人第三方移植（1★/发布后停更），不作依赖；其 API 设计按需吸收进 v1.2（steer/abort/事件流时间线）。
Python 口径保持 3.9+（3.10 升级绑定 pi，一并否决）。

**实施结果（已落地并实测）**：
- 11 工具声明式注册表 + 晚绑定 + 结果截断（top-20/8000），`execute_ai_tool` 旧名旧文案兼容
- `agent_run` 规划循环（计划→多步→防幻觉守则→合规守则），max_tool_rounds=8
- Memory：agent_sessions/agent_messages + 满 8 轮自动摘要（无 key 降级截取）+ 诊断追问注入最近 3 条摘要
- ai_chat 与诊断页「AI 追问」共用 agent_run（追问链续写同一会话）

**验收状态（§7 逐条）**：#1 注册表 11≥10 ✅；#2 ai_chat 已接 agent_run（多步 trace 由 test_agent_core mock 覆盖）✅；
#3 落库/追问链/摘要/注入全链路实测 ✅；#4 追问按钮 ✅；#5 全量 84 测试 ✅；#6 无 key 全链路不崩 ✅。

**已知行为注记（非 bug，写明口径）**：
1. agent_sessions/agent_messages 表由 `init_db()` 建表——**旧库首次升级需跑一次 init_db**（应用启动即自动）。
2. 摘要满 8 轮用户消息才生成；`build_memory_context` 只注入**已有摘要**的会话（list_recent_agent_sessions 过滤）——
   新会话前 8 轮内诊断追问不带历史记忆，属预期；v1.2 可改为"标题即注入"。
3. `add_diary` 为 load→append→save 整表写，多会话并发存在 last-write 覆盖（MVP 接受，工具描述已注明）。
4. `compare_funds` 返回 Markdown 字符串非 JSON（工具描述已注明）。
5. `session_id` 契约：必须是 `ensure_session` 返回的整数 id（字符串键会静默写入失败，已加类型防御）。
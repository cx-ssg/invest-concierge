# Architect 评审（ds pro max）· Agent 化 v2_pi

> 2026-08-31 · 第四视角独立评审

# Agent 化 MVP 设计 v2.0 独立评审

## 1. 还有什么优化

- **引入 AgentRunner 接口**：`pi_bridge.py` 与自写循环应统一为 `AgentRunner` 协议（`run_prompt(session_id, message) -> AgentResult`），客户端只依赖工厂函数 `get_agent_runner()`；import 回退不应要求调用点感知 pi 或 builtin。
- **缓存动态 pydantic 模型**：`pydantic.create_model()` 按 schema JSON 字符串做 `lru_cache`，避免每次 AgentTool 调用重复创建、减少内存碎片。
- **消息落库分表**：`state.messages` 中的 tool 调用消息不应写入 `agent_messages`，而是写入 `tool_trace` 表；`agent_messages` 仅保留最终 user/assistant 消息，防止摘要被中间工具结果污染。
- **历史重建策略**：优先使用 pi 原生历史注入参数（如 `history=` 或 `state.messages` 初始化），若没有则通过 system_prompt 拼接“最近 3 条摘要 + 最近 5 条消息”，并设定 token 上限。
- **事件收集与渲染解耦**：on_event 回调仅向线程安全队列（`queue.Queue`）追加事件，主线程在 `agent.prompt()` 结束后统一渲染；回调内不得操作 `st.session_state`，避免跨线程问题。
- **超时后资源清理**：`asyncio.wait_for` 超时后，在 `finally` 中显式关闭 provider 的 httpx 客户端（若存在 `aclose()`），防止连接泄漏。
- **工具异常封装**：AgentTool.execute 内捕获所有异常并返回结构化 `ToolError`（包含 `error` 和 `suggestion`），阻止异常穿透 pi 循环，保证模型收到可读错误而非中断。

## 2. 不足与风险（按严重度）

- **致命：第三方库可持续性**：pi-agent-loop 为个人移植，存在停止维护、API 漂移、许可证不明风险。需立即核实其 GitHub 活跃度、issue 响应和 license（MIT/Apache 才能安全用于开源项目），否则可能被迫紧急替换。
- **高：双记忆源一致性缺陷**：flush-per-prompt 只写最终消息，若多轮工具调用中途超时/异常，中间状态丢失；且“新会话从 DB 重建历史”未做强制截断，token 成本随会话增长不可控。必须实现“摘要 + 最大消息数”双阈值。
- **高：事件回调线程安全**：on_event 是异步回调，Streamlit `asyncio.run()` 新建事件循环时，回调可能在不同线程触发，普通列表收集会竞态。必须使用 `queue.Queue` 或专用单例事件循环。
- **中：依赖锁定不足**：仅 pin `pi-agent-loop==0.1.0` 和 `pi-ai-client<0.2` 不够，传递依赖未锁定。建议生成 `requirements.lock` 或使用 `uv pip compile`。
- **中：pip 硬依赖导致安装失败**：若用户环境无网络或 pi 包无法安装，应用无法启动。应将 pi 设为 optional extra（`[project.optional-dependencies] pi = [...]`），`AGENT_BACKEND=builtin` 时完全跳过安装。
- **中：Python 3.10 升级可能破坏其他数据源库**：需全量运行现有 56 + 28 测试，并检查所有第三方依赖的 3.10 兼容性，尤其是金融数据相关库。
- **低：测试 mock 与 pi 内部签名强耦合**：小版本更新可能改变 `stream_fn` 参数结构导致测试假失败。可增加一个 weekly CI 跑最新版 pi 测试。

## 3. 实现细节点

- **Agent 构造的 model 参数类型**：PoC 已验证，但需在 `build_deepseek_provider()` 中返回 provider 后，确认获取模型对象的方式（如 `provider.get_model("deepseek-chat")`），不要误传字符串。
- **AgentTool.execute 是否支持 async**：若工具内部有阻塞 I/O（SQLite 查询、网络请求），sync 执行会阻塞事件循环。需用 `asyncio.to_thread` 包装或确认 pi 支持 async 工具。
- **pydantic 动态模型字段细节**：`create_model` 字段名须与 OpenAI schema `properties` 键完全一致；`required` 列表需映射为无默认值的必填字段，`enum` 映射为 `Literal`，`default` 需用 `Field(default=...)`，否则参数校验可能失败。
- **on_event 回调签名**：回调参数可能是 Event 对象（含 `type`、`payload`），需处理 `tool_start`、`tool_end`、`stream_delta`、`error` 等类型，避免只处理流式事件。
- **历史注入的 API 方式**：若 pi 提供 `history` 参数则用其注入历史；若需手动修改 `agent.state.messages`，必须使用库内部的消息类（如 `UserMessage`、`AssistantMessage`），不能裸 dict。
- **asyncio 包裹方式**：在 Streamlit 顶层使用 `asyncio.run(asyncio.wait_for(agent.prompt(...), timeout=120))`，不能嵌套在其他 async 上下文；超时后需捕获 `asyncio.CancelledError`。
- **数据库 flush 并发安全**：每次 prompt 后写 `agent_messages` 需使用事务，并对 `(session_id, seq)` 加唯一约束；若同一会话并发请求，需加行锁或 `INSERT ... ON CONFLICT` 避免重复。
- **回退逻辑的语义**：`AGENT_BACKEND=auto` 时，import 失败回退 builtin；但若 pi 已安装却初始化失败（如密钥错误），不应静默回退，应记录日志并抛出明确提示。

## 4. 未来演进路线（v1.2/v1.3）

- **v1.2：真流式输出**：用 `st.write_stream` 接入 pi 的异步生成器，替代“收集后渲染”，大幅提升体验；同时实现记忆自动压缩（旧消息摘要 + 最近 N 条），控制 token。
- **v1.2：可观测性**：集成 Langfuse 或自定义 trace，展示工具调用链、token 用量、延迟，作为开源亮点“透明决策”。
- **v1.2：一键部署与演示**：提供 Docker Compose、seed 数据和 demo 视频，降低试用门槛，利于开源传播。
- **v1.3：基于反馈的“越用越懂”**：增加用户点赞/纠错按钮，记录反馈并动态调整 system_prompt 中的用户偏好或工具选择策略。
- **v1.3：抽离开源适配层**：将 `pi_bridge.py` 打包为独立库 `pi-agent-loop-streamlit`，贡献回社区，吸引 star，同时吸纳外部贡献。
- **v1.3：多智能体分工**：研究、风控、组合建议三个子 Agent 协同，用户可配置风险偏好。
- **持续：上游追踪**：关注 pi-agent-loop 发布动态，及时跟进 API 变化；保持 builtin 回退始终可用，降低切换成本。
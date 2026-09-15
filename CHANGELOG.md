# Changelog

本项目遵循 [Semver](https://semver.org/)。发布日如有调整，以 GitHub Release 为准。

## [Unreleased] - 2026-09-15

### Fixed
- **P0-1 配置链断点**：`agent_run(model=_reasoner_model())` 的默认参数在 **import 时**被求值一次 → 设置页切换模型对 Agent 对话链路**完全无效**。改为 `model=None` + 函数内解析（调用时读配置）；显式传 `model` 仍优先。（来源：2026-09-14 外部评审复核，见 `docs/COVERAGE_DESIGN.md` §11）
- **P0-2 工具成功标记失真**：`tool_end.ok` 用 `startswith("工具执行失败")` 判定，而真实错误格式是 `{"error": "工具执行出错：..."}`（agent_core.py:368）→ `ok` 恒为 True，排障被误导。新增 `tool_output_is_error()` 统一判定（非 str / 非 JSON / `error` 为真值 → 失败，空 `error` 不误判），`tool_end.ok` 改用之。
- 测试修正：`tests/test_m0_services.py` 中「以'工具执行失败'开头 → ok=False」的用例喂的是**真实代码从不产生**的字符串（锁定错误契约），已改为真实 JSON 错误格式。

### Added
- **P0-3-A 离线评测契约（golden set）**：新增 `tests/golden/cases.py` 作**单一事实源**（26 条用例，覆盖全部 23 个工具 + 3 条多工具编排；字段 `question` / `expect_tools` / `tool_args` / `expect_facts` / `tags`），离线与未来的在线评测共用同一份；新增 `tests/test_golden_offline.py`（6 条用例表合法性校验 + 26 条编排契约）。离线阶段的核心价值：抓出 golden set 里**写错的工具名 / 参数名**（这类错误在在线评测里会静默失效）——已做**阴性对照**验证（注入 `_TYPO` 后 2 条校验立刻 FAIL）。
- **P0-2 后半 · 统一 per-tool 错误码**：三类错误返回在**保留旧中文文案**的前提下新增机器可判字段 `error_code` / `tool` / `retryable`（码值 `UNKNOWN_TOOL` / `NOT_FOUND` / `TOOL_EXCEPTION`，兜底 `UNKNOWN_ERROR`）；新增 `make_tool_error()` 与 `tool_output_error()`（兼容新格式 / 老格式 / 非 JSON 三种形态），`tool_output_is_error()` 改为其薄封装；`tool_end` 事件失败时携带 `error_code` + `retryable`。
  > ⚠️ **诚实标注（源自 2026-09-15 独立审计 ⚪ 条）**：这两个字段**当前没有任何消费方**（前端 `useAgentRun` 只读 `name/ok/elapsed_ms`），属**前瞻性附加**，供未来 M3 图节点 / 降级重试使用 —— 不要写成"消费方可按类型分支"。
- `tests/test_p0_agent_fixes.py`：**8 条 P0 回归锁**（P0-1 model 晚绑定 2 条；P0-2 `ok` 判定 6 条，含空 `error`、非 JSON 等边界）。
- **P0-3-B · 在线评测 + token 记账**：
  - `call_llm` 新增 `_extract_usage()`，在两处 return 带回 `usage`（兼容端点不返回 usage 时为 None，不抛异常）；`agent_run` **跨轮累加**，返回值新增 `usage`（`prompt_tokens` / `completion_tokens` / `total_tokens` / `calls`）
  - `scripts/eval_agent.py`：真调模型的**在线评测**（默认 **dry-run 不花钱**，加 `--run` 才跑；支持 `--limit` / `--ids` / `--json` / `--price`）。判定：**工具按集合命中**（顺序不敏感，仅 `order_sensitive` 用例比序列）+ 事实宽松子串命中；**默认只报 token 不报钱**（单价易过时，要报钱须显式 `--price`）
- `tests/test_tool_error_contract.py`：**12 条错误契约回归锁**（三类 error_code / 网络类 `retryable=True` / 成功路径不含 error 字段 / `tool_output_error()` 四种形态 / `tool_end` 是否携带 code）。
- `tests/test_usage_accounting.py`：**6 条 usage 回归锁**（三种响应形态带回 usage / 无 usage 不炸 / 跨轮累加 / 兼容旧返回键）。
- **P0-4 · 工具层契约加固**：
  - **必填参数校验**：缺参 → `error_code=INVALID_ARGS` 并点名缺哪个（旧实现会把空值透传给工具 —— 等于用一次真实网络请求换一个看不懂的报错；RED 阶段实测该路径真的会去打行情接口，单跑测试从 4.7s 涨到 53s）
  - **单次调用超时**：`TOOL_TIMEOUT_SECONDS`（默认 30s，设 0 关闭）。看门狗用线程池 `future.result(timeout)`；⚠️ **不能用 `with ThreadPoolExecutor(...)`**（`__exit__` 会 `shutdown(wait=True)` 等线程跑完，超时控制形同虚设 —— 这个坑由超时测试当场抓出）；并区分**看门狗超时**（`_ToolTimeout` → `TIMEOUT`）与**工具内部超时**（`TimeoutError` → 保持 `TOOL_EXCEPTION`，旧契约不破）
  - **并行执行能力**：`agent_run(parallel_tools=True)` 时多工具并发执行；**默认关**（工具内部对缓存/SQLite 的线程安全性尚未实测）；无论并行与否，`tool_start`/`tool_end` 事件与 `tool_trace` 仍**按调用顺序**
- `tests/test_tool_contract_hardening.py`：**10 条回归锁**（缺参 / 空参 / 无必填项工具 / 超时生效与关闭 / 并行默认关 / 顺序基线 / 并行下 trace 顺序）。
- 全量 `pytest` **252 passed**（181 → 189 → 201 → 233 → 236 → 242 → 本轮 252）。

### 审计处理（2026-09-15 · 独立子代理审 `4d51a15..HEAD`）
- 总体判定：**P0-1/P0-2 达标**（行为锚定、revert 即 FAIL、无自证陷阱）；**golden set 仅算阶段性半成品**（`test_golden_case_drives_agent_run` 是编排冒烟，不是评测）—— 已如实标注于本节与 `tests/golden/cases.py` docstring。
- 🟡 `error` 字段启发式耦合（`agent_core.py` 的 `tool_output_error`）→ 把「成功载荷不得含非空顶层 `error`」写进 `execute_ai_tool_v2` docstring，并加 2 条契约测试固化（`test_success_payload_must_not_carry_nonempty_error` / `test_empty_string_error_is_treated_as_success`，后者对应 `compare_funds_structured` 的真实形态 `{"ok": true, "error": "", ...}`）。
- 🟡 `MIN_TOOL_COVERAGE=20` 形同虚设 → 提到 **23**（与注册表等值）：新增工具若没同步进 golden set，该断言立刻失败。
- 🟡 `tool_end` 只转发 `error_code`、丢了 `retryable` → 已补上（+ 测试）。
- 🟡 期望标定歧义 → `stock_moat_018` 的问题改为直接给代码（600519）；`cases.py` 写明在线判定约定：**按工具集合命中、顺序不敏感**。
- ⚪ `error_code` 无消费方却被写成"可按类型分支" → 措辞已改（见上）。
- **审计无法验证项**：只读环境跑不了 pytest，"RED→GREEN"是它的静态推演 → 由主代理实测补上（本文件与 §11 记载的 4 failed / 10 failed 与各轮 passed 数均为实际运行输出）。

## [Unreleased] - 2026-09-08

### Fixed
- **v1.2.1 DeepSeek V4 模型升级**：`deepseek-chat`/`deepseek-reasoner` 已 2026-07-24 被官方停用（调旧名 400/404）——默认模型改为 `deepseek-v4-flash`，现役三模型可选：`deepseek-v4-flash` / `deepseek-v4-pro` / `deepseek-v4-flash-vision-exp`（图片输入）
- 思考模式开关：V4 思考默认开启 → agent 对话链路显式关闭（对齐旧 chat 快+便宜行为），诊断"AI 追问"链路开启（保留思考链展示）；思考经 `extra_body={"thinking": ...}` 切换，非换模型名
- **local_env.bat 兼容**：exe 直接启动（无 start.bat）也能读到 key——多路径探测（源码目录/exe 同目录/cwd）

## [Unreleased] - 2026-09-08

### Added
- **v1.2 模型接入**：设置页「模型接入」卡片——直接填 API Key，无需再写 .env/local_env.bat
- 多 provider 支持：DeepSeek 官方 / SiliconFlow 硅基流动 / 阿里云百炼 DashScope / 自定义 OpenAI 兼容端点（中转/网关/本地部署）
- 测试连接：保存前发最小请求验证连通，回显延迟；401/404/429/超时自动翻译为人话
- Key 安全：仅落本机 SQLite（app_settings），掩码回显（sk-ab****wxyz），不入 git/不上传/不回传明文

### Changed
- 配置优先级：设置页 DB > .env/环境变量（.env 老用户零迁移，继续有效）
- 保存即生效无需重启（ai_helper/agent_core/report/status 全链路动态读配置）
- 26 处测试 patch 迁移至 llm_config._TEST_KEY_OVERRIDE 钩子；pytest 171→180

## [1.0.0] - 2026-09-15（计划）

首个公开版本。

### Added
- 双轨导航：主界面右上角一键切换「📊 基金 / 📈 股票」专栏
- 股票综合诊断：基本面评分 / 财务排雷（8 大雷区）/ 护城河评分（6 维）/ 五法估值 + PE·PB 近 5 年历史分位
- AI 多角色辩论：基本面/技术/情绪/风控 4 分析师独立分析 → 决策委员会主席辩论，产出带评级的结论
- AI 对话工具调用：11 个数据工具（行情/持仓/诊断/估值/排雷/护城河/对比/情绪/日记），多步规划循环
- 跨页会话记忆：满 8 轮自动摘要（无 Key 降级为截取兜底），诊断追问注入最近 3 条会话记忆
- 演示模式：无 Key 也可体验（预置示例持仓，纯内存不写库）
- 基金持仓管理 / 资产总览 / 投资日记

### Changed
- 全新「夜航蓝 × 香槟金」深色视觉体系；A股口径统一为红涨绿跌

### Fixed
- 财务摘要解析：同花顺接口返回带单位字符串（如 "91.93%"、"1,741.44亿"）且按年份升序，
  导致取到最旧年度、数值转 0——统一清洗并按报告期取最新行

### Security
- 外链 URL 统一校验（仅 http/https，拒绝本地/私有/保留地址）；JSON 写入路径防穿越；密钥占位符化

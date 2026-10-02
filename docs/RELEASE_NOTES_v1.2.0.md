# invest-concierge v1.2.0

**发布日**：2026-10-02 · **上一个版本**：[v1.1.0](https://github.com/cx-ssg/invest-concierge/releases/tag/v1.1.0)

⚠️ **发布状态**：tag `v1.2.0` 已推送；**GitHub Release 页面尚未创建**（创建后正文即本文）。
⚠️ **本次未重新打包安装器** —— `Releases/latest` 仍指向 `v1.0.0` 的产物（含 exe），
**安装包请到 v1.0.0 页面下载**；本版功能请按下方「源码运行」使用。

---

## 本版内容 · M3 编排层（LangGraph 单链路接入）

一句话：**把「股票深度诊断」这一条链路口图化**，其余工具调用保持原有线性循环 —— 用图换来的
是**检查点（断点续跑）**与**结构化人工确认**，而不是重写整个 Agent。

```
[入口] 股票代码
  ↓  [data_fetch]      拉行情/财报/资金流（现有工具，失败可重试）
  ↓  [条件边]          财报数据是否齐？──否──→ [fallback] 降级为「仅行情诊断」
  ↓                    是
  ↓  [analyze]         6 引擎分析（基本面/排雷/护城河/估值/三表/…）
  ↓  [retrieve]        M1 私域知识层检索公告与研报原文
  ↓  [synthesize]      汇总为带引用的诊断报告
  ↓  [human_review]    用户确认 ──要求修改──→ 回到 analyze（上限 3 轮，到限自动通过）
[出口] 报告 + 检查点持久化（可断点续跑）
```

| 能力 | 说明 |
|---|---|
| **开关** | `ORCHESTRATOR=legacy\|graph`，**默认 `legacy`** ⇒ 不改变现有行为、**回滚面为零**；非法值回落 legacy 且可观测 |
| **检查点** | SQLite（`langgraph-checkpoint-sqlite`）⇒ 中途中断可续跑，不重跑已完成节点 |
| **人工确认** | `interrupt()` 原语（未手搓中断），循环上限 3 轮防死循环烧 token |
| **人审入口（F4）** | `POST /api/stocks/{code}/diagnosis/review`（`decision=approve\|revise` + `note`）+ 诊断页人审面板 |
| **契约** | graph 路径返回**与 legacy 逐字相同的 6 引擎 payload 形状**（额外挂 `_orchestrator` 元信息）⇒ 前端无需改动 |

## 验收（本机实测，每一项都附状态口径）

| # | 命令 / 方式 | 结果 |
|---|---|---|
| C1 | `pytest tests/test_graph.py -q` | **38 passed** |
| C2 | `ORCHESTRATOR=graph` 真跑 600519 | 条件边正确分流、检索证据 5 条、检查点落盘 |
| C3 | 两进程模拟 kill→重启（同 `thread_id`） | phase1 取数 **3 次** → phase2 **0 次**；耗时 14.6s → **0.0s**；`trace` 仅尾部追加 `human_review` ⇒ **已完成节点未重跑** |
| C4 | `pytest -q` | **393 passed**（默认 legacy 行为不变） |
| **E2E** | 走真实 HTTP 层：`GET → POST revise → POST approve` | `pending(0) → revise(1，**真的重跑**) → approved`；三次响应**键集 20、缺失 0**；再次 GET 回到 pending；缺 `decision` ⇒ 422 |
| **打包** | PyInstaller 构建 + TOC 核对 | `invest-concierge.exe` **433 MB 真 PE**；`utils.orchestrator` / `langgraph.graph` / `langgraph.types` / `langgraph.checkpoint.sqlite` / `langchain_core` **全部打入** |

## ⚠️ 诚实边界（请按此口径读上面的数字）

1. **C2 真跑的是 `fallback` 分支** —— 本机 akshare 当前大面积不可用
   （`RemoteDisconnected` / 多个旧接口已下线），所以真机上 6 引擎数据拿不到，图**正确降级**。
   `analyze` 分支由**离线测试** + **桩化可用数据源**覆盖；上面那条 E2E 正是在喂入健康数据源后
   **走到了 `analyze`**（这正是本版修掉的一处结构性缺陷，见下）。
2. **本版未发布安装包资产**（构建与模块完整性已验证，但未附到 Release）。
3. **人审 UI 是最小实现**：批注输入框 + 「确认通过 / 要求修改并重跑」两个按钮；
   「要求修改」会重跑 6 引擎（15–40s，已缓存则秒回）。
4. **只迁移了一条链路**：其余 23 个工具的调用仍是原线性循环。

## 本版修掉的实质性缺陷（含由外部审计发现、以及由「真跑」才暴露的）

本版实现经过**三路独立外部审计**（Codex / Hermes / 一个独立会话，互不读对方报告），
审计判 REVISIONS_NEEDED（1 阻断 + 2 重要 + 4 一般），**全部已修**：

| # | 问题 | 性质 |
|---|---|---|
| **F1** 🔴 | `analyze` 分支**生产结构性不可达**：取数源返回**扁平标量 dict**、与三表键交集为空 ⇒ 判据恒 False ⇒ 6 引擎路径永远走不到（「永久降级」） | 阻断 |
| **F3** 🔴 | 人审上限的测试是**死测试**（循环 0 次迭代），把两层保险同时拆坏仍全绿；且终态语义错（停在 `revise` 像"还在等改"） | 阻断 |
| **A7** 🟠 | graph 路径在**唯一可达分支**上响应只有 5 个键（前端声明 13 个引擎字段全缺）⇒「前端零改动」不成立 | 重要 |
| F2 | 适配层零行为覆盖（测试 monkeypatch 掉被测对象） | 重要 |
| F4 | 产品路径**无 resume 通道** ⇒ 人审永远停在 pending、每次 GET 整轮重跑 | 一般（本版补齐） |
| F5 | 引用溯源字段映射错（报告恒显示「notice（日期不明）」） | 一般 |
| F6 | 四项状态「只写不读」（资金流/批注/两类错误未进报告） | 一般 |
| 其他 | `retrieve_docs` 返回 JSON **字符串**而适配器只判 dict/list ⇒ 证据恒 0；`trace` 用 reducer ⇒ 二次 invoke 轨迹翻倍；`get()` 复用旧 thread ⇒ 返回上次结论而非新诊断 | 由真跑/测试暴露 |

> 这批缺陷的共同点值得记下来：**「测试全绿」与「生产路径可达」是两件事**。
> 本版阶段内出现三次同族（评测不经过被测对象 / 适配层引用不存在的模块 / 契约测试把整图打桩只测映射），
> 因此本版新增了**喂真实形态**与**不打桩可达路径**的契约锁，并用**变异测试**证明新锁真会红。

## 安装 / 运行

- **源码运行**（推荐，本版未打包）：
  ```bash
  pip install -r requirements.txt
  # 图编排（可选，默认 legacy）：
  #   PowerShell:  $env:ORCHESTRATOR = "graph"
  #   bash:        export ORCHESTRATOR=graph
  ```
- **首次使用知识库**需自建语料：`ollama pull bge-m3` → `python scripts/rag_ingest.py --code 600519`。

## 已知边界

- 知识库语料与向量（`*.db` / `corpus/`）不随仓库分发（体积与版权）。
- 免费数据源可能波动，页面降级显示 `--` 而非崩溃。
- **不构成投资建议**，仅供学习与技术研究。

---

*本版经三路外部独立审计 + 整改；审计报告与请求文档不随仓库分发。*

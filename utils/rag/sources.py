# -*- coding: utf-8 -*-
"""引用溯源载荷抽取（A2 引用回跳渲染）—— `tool_end` 事件的 `sources` / `evidence_level` 来源。

## 为什么需要这一层

设计 `docs/COVERAGE_DESIGN.md` §3.2 生成层要求「回答必须带引用编号 [1][2] + 来源 URL/文件名 + 日期」，
但 2026-10-02 现场核查发现：`retrieve_docs` 工具**已经**返回了
`chunk_id / title / url / source / published_at`，而 SSE 的 `tool_end` 事件**只有**
`name / ok / elapsed_ms` —— 检索来的来源在 UI 上完全不可见、不可引用（前端 grep
`citation|chunk_id|evidence_level` **0 命中**）。

本模块是纯函数层：把工具的 JSON 字符串返回**搬运**成事件体里允许出现的最小字段集。

## 契约（三条硬约束）

1. **只搬运、不加工**：`url` / `title` 必须与工具返回**逐字一致**。工具层在 `none` 档已把
   两者剥掉（`utils/rag/retrieve.py` §2026-09-18 审计 B P2-2）—— 事件层**不得补回来**，
   否则「无引用 = 不算回答」的机器强制会在事件层被悄悄撤销。
2. **事件体必须小**：每条只保留 `rank / chunk_id / title / url / source / published_at /
   code / is_table`，**刻意不含 `text`**（正文已完整地躺在 `tool_trace` 里）。
3. **绝不抛异常**：畸形 JSON / 空串 / 非 str / 无 `results` ⇒ 返回 `([], None)`。
   事件是对话主链路的旁路，抽取失败**不得**打断工具时间线与回答生成。

## ⚠️ 双层编码（生产实况，2026-10-02 实测）

`execute_ai_tool_v2` 对**返回字符串的工具**会把结果再 `json.dumps` 一次
（`_truncate` 之后统一序列化，`utils/agent_core.py` 第 496–497 行）——
`retrieve_docs` 正是这一类 ⇒ `agent_run` 里 `_payload` 拿到的 `output` 是
`"\"{\\\"query\\\": ...}\""` 这种**双层字符串**。
只解一层的实现会拿到 `str` → 判成「无 results」→ **事件里永远没有 sources**，
而"喂单层 JSON"的纯函数单测**全绿**。本函数因此逐层解开（上限 2 层）后再判定。

⚠️ `chunk_id` 只在同一个 `kb.db` 构建内稳定（语料重建后会变，见 `retrieve.py` 文件头），
因此它只用于**同一次运行内的引用回跳**，不可跨构建持久化。
"""
import json

__all__ = ["SOURCE_FIELDS", "extract_sources"]

#: 事件体允许出现的字段（顺序即文档顺序；`text` 被刻意排除，见模块 docstring 第 2 条）
SOURCE_FIELDS = (
    "rank",
    "chunk_id",
    "title",
    "url",
    "source",
    "published_at",
    "code",
    "is_table",
)


def extract_sources(tool_output):
    """从 `retrieve_docs` 的 JSON 字符串返回里抽出事件用来源列表。

    返回 `(sources, evidence_level)`：

    - `sources`：`list[dict]`，每条**只含** `SOURCE_FIELDS` 八个键（缺字段补 `None`），
      顺序与工具返回的 `results` 一致（前端引用编号 `[n]` 即该列表下标 +1）。
    - `evidence_level`：工具返回的顶层档位（`"weak"` / `"none"`；无该字段时为 `None`）。

    任何解析失败 / 空串 / `results` 非非空列表 ⇒ `([], None)`，**绝不抛异常**。

    ⚠️ `results` 里非 dict 的元素会被跳过；若跳过之后一条不剩，同样返回 `([], None)`
    （宁可"没有来源"，也不要让前端拿到 `None` 字段的对象去渲染）。
    """
    if not isinstance(tool_output, (str, bytes, bytearray)):
        return [], None
    try:
        data = json.loads(tool_output)
    except Exception:  # noqa: BLE001 - 畸形输入是预期分支，不是异常
        return [], None
    # 双层编码（见模块 docstring）：`execute_ai_tool_v2` 对字符串工具会再 dumps 一次。
    # 逐层解开（上限 2 层，够用且防病态嵌套），任何一层坏掉都按「无来源」处理。
    for _ in range(2):
        if not isinstance(data, str):
            break
        try:
            data = json.loads(data)
        except Exception:  # noqa: BLE001
            return [], None
    if not isinstance(data, dict):
        return [], None

    results = data.get("results")
    if not isinstance(results, list) or not results:
        return [], None

    level = data.get("evidence_level")
    if not isinstance(level, str):
        level = None

    sources = []
    for row in results:
        if not isinstance(row, dict):
            continue
        sources.append({key: row.get(key) for key in SOURCE_FIELDS})
    if not sources:
        return [], None
    return sources, level

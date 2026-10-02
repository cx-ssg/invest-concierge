# -*- coding: utf-8 -*-
"""一次 agent 运行内的**引用编号作用域**（任务 A-R1 · F2 修复）。

## 问题（两路审计独立复现）

`CITATION_NOTE` 说「编号与结果列表的顺序一致（第 1 条 = `[1]`）」—— 这是**每次调用**的口径，
`retrieve_docs` 每次调用都从 `[1]` 起编号；而前端 `useAgentRun.mergeSources` 把**一次运行**里
多次检索的 sources 按 `chunk_id` 去重、首次出现顺序**全局合并**，卡片编号 = 合并后下标 +1。
⇒ 一次运行发生第二次检索时，模型的 `[1]` 指到**第二次**的第一条，而卡片 `[1]` 是**第一次**的
第一条 —— 引用归属错误（金融场景里"点开引用看到的是另一份公告"，正是本特性的信任基础）。

## 修法（任务书方案 A：注入侧全局唯一递增）

本模块维护「本次运行已返回的来源」有序表（`chunk_id` 去重、首次出现顺序 —— **与前端
`mergeSources` 同构**）。`retrieve_docs` 每次返回结果时向作用域**领取**编号，并把编号写进
引用规范文案（`messages.citation_note`）。于是正文 `[n]` 与来源卡编号在**任何次数**的检索下
都指同一条。

## 作用域的建立与复位

`with_citation_scope` 装饰 `agent_core.agent_run` —— **每次运行一个**作用域，用 `try/finally`
复位（异常路径也不泄漏：`contextvars` 在 pytest 主线程里跨用例存活，泄漏会让后续
**直接**调用 `retrieve_docs`（不起作用域）的文案被错误偏移，见 `tests/test_rag_citation_offset.py`）。

## 与前端合并规则的**唯一约定**

- 去重键：`chunk_id`；`chunk_id` 为 None 的条目**不参与去重**（前端同样按"无法判重"处理，
  直接追加）。两侧必须同规则，否则编号又会错位。
- 顺序：**首次出现顺序**（不是 rank、不是分数）。

## 已知残余（基线既有，本修复不扩大）

`execute_ai_tool_v2` 的 `_truncate`（max_len=8000）若把 `results` 从尾部截掉，事件里就没有
sources，而本作用域已经消费了编号 ⇒ 后续检索的编号会比前端卡片号偏大。
`message` 排在 `results` 之前（`retrieve.py::_payload` 的既有取舍），故该情形下连警示语都
已丢失 —— 属既有 P2 残余，未由本次变更引入；已写入 `report-A-R1.md`。
"""
import contextvars
import functools

__all__ = ["CitationScope", "current_scope", "with_citation_scope"]

_scope_var = contextvars.ContextVar("fund_agent_citation_scope", default=None)


class CitationScope:
    """一次运行内的全局引用编号分配器（与前端 `mergeSources` 同构）。"""

    def __init__(self):
        self._number_of = {}   # chunk_id -> 全局编号（首次出现即固定）
        self._next = 1

    @property
    def count(self):
        """已分配出去的编号个数（= 下一次检索的起始编号 - 1）。"""
        return self._next - 1

    def assign(self, chunk_ids):
        """为一次检索的结果（按返回顺序）分配全局编号。

        返回 `(base, numbers)`：
        - `base`：**本次调用之前**已分配出去的编号个数（任务书要求的 N）；
        - `numbers`：本次逐条对应的全局编号（重复的 chunk 沿用原编号）。
        """
        base = self.count
        numbers = []
        for cid in chunk_ids:
            if cid is not None and cid in self._number_of:
                numbers.append(self._number_of[cid])
                continue
            numbers.append(self._next)
            if cid is not None:
                self._number_of[cid] = self._next
            self._next += 1
        return base, numbers


def current_scope():
    """当前运行的作用域；不在任何运行内（直接调用工具、脚本、单测）时为 None。"""
    return _scope_var.get()


def with_citation_scope(fn):
    """装饰 `agent_run`：整段运行包在一个新的引用编号作用域里，退出时复位。"""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        token = _scope_var.set(CitationScope())
        try:
            return fn(*args, **kwargs)
        finally:
            _scope_var.reset(token)

    return wrapper

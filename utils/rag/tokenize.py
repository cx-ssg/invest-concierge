# -*- coding: utf-8 -*-
"""中文分词：中文连续片段按 bigram 切，英文词保留。

复用自知识库 `co-planning/scripts/search_wiki.py:374 tokenize()`（2026-09-15 搬入）。
纯 `re` 实现、**零第三方依赖**（不需要 jieba）。

为什么不用 SQLite FTS5 的内置分词器：实测 `unicode61`/`porter` 对中文整句
按单 token 处理（0 命中），`trigram` 仅 3 字查询命中、5 字查询 0 命中。
见 `docs/COVERAGE_DESIGN.md` §3.3 第 3 条。
"""
import re

_ASCII_WORD = re.compile(r"[A-Za-z0-9_]{2,}")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")


def tokenize(text):
    """返回 token 列表：小写英文/数字词 + 中文 bigram。"""
    if not text:
        return []
    text = str(text)
    toks = [w.lower() for w in _ASCII_WORD.findall(text)]
    for run in _CJK_RUN.findall(text):
        toks.extend(run[i:i + 2] for i in range(len(run) - 1))
    return toks

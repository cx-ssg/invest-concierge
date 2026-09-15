# -*- coding: utf-8 -*-
"""M1 私域知识层内核（RAG）。

契约见 `docs/M1_KERNEL_SPEC.md`。设计依据 `docs/COVERAGE_DESIGN.md` §3。

关键决策（2026-09-15 实测）：**不使用 SQLite FTS5** —— 中文检索实测不可用
（见 COVERAGE_DESIGN.md §3.3 第 3 条），改用「中文 bigram 分词 + 纯 Python BM25
+ bge-m3 向量 + RRF 融合」，全部组件复用自已验证的知识库实现。
"""

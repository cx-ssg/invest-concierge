# -*- coding: utf-8 -*-
"""BM25 检索（纯 Python，无第三方依赖）。

复用自知识库 `co-planning/scripts/search_wiki.py:383 class BM25Index`（2026-09-15 搬入），
实现与参数（k1=1.5, b=0.75）保持一致，便于两个系统的检索结果可比较。
"""
import math


class BM25Index:
    def __init__(self, docs):
        """docs: list[list[str]]，每条已 tokenize。"""
        self.docs = docs
        self.N = len(docs)
        self.df = {}
        lens = []
        for d in docs:
            lens.append(len(d))
            for t in set(d):
                self.df[t] = self.df.get(t, 0) + 1
        self.avl = sum(lens) / max(self.N, 1)

    def score(self, query_toks, k1=1.5, b=0.75):
        """返回每个文档的 BM25 分数（与 self.docs 同序）。"""
        n = self.N
        uniq_q = set(query_toks or [])
        scores = []
        for d in self.docs:
            dl = len(d)
            tf_map = {}
            for t in d:
                tf_map[t] = tf_map.get(t, 0) + 1
            s = 0.0
            for t in uniq_q:
                if t not in tf_map:
                    continue
                tf = tf_map[t]
                df = self.df.get(t, 0)
                idf = math.log((n - df + 0.5) / (df + 0.5) + 1.0)
                s += idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / self.avl))
            scores.append(s)
        return scores

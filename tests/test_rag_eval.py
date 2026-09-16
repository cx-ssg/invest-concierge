# -*- coding: utf-8 -*-
"""评测器回归锁（纯逻辑，不依赖 kb.db / ollama）。

重点：三个指标的口径必须**可被打破**（否则又是一组自证测试）——
每个用例都构造出"指标应当变化"的场景。
"""
import numpy as np

from scripts import rag_eval as ev
from utils.rag.evidence import Evidence


class _FakeJudge:
    """按查询文本返回预设 evidence（避免依赖真实语料）。"""

    def __init__(self, table):
        self.table = table

    def assess(self, query):
        return self.table.get(query, Evidence(0.0, 0.0, "none"))


_META = [{"chunk_id": 1, "text": "贵州茅台营业收入"},
         {"chunk_id": 2, "text": "比亚迪汽车销量"}]
_MATRIX = np.array([[1.0, 0.0], [0.0, 1.0]], dtype="float32")


def test_over_abstain_counter_works():
    """域内可答被判 `none` → over_abstain_rate 上升（**召回护栏** A3c）。"""
    rel = [{"query": "可答问题", "answer_chunk_ids": [1]}]
    judge = _FakeJudge({"可答问题": Evidence(0.0, 0.0, "none")})   # 故意误弃权
    m = ev.evaluate(rel, [], judge, _META, _MATRIX, np.array([[1.0, 0.0]], dtype="float32"))
    assert m["over_abstain_rate"] == 1.0
    assert m["Recall@5"] == 0.0, "弃权的查询不可能召回"


def test_recall_and_mrr_when_answer_returned():
    """答案块在 top-k 里 → Recall@5 = 1、MRR@10 = 1。"""
    rel = [{"query": "茅台", "answer_chunk_ids": [1]}]
    judge = _FakeJudge({"茅台": Evidence(0.5, 1.0, "strong")})
    m = ev.evaluate(rel, [], judge, _META, _MATRIX, np.array([[1.0, 0.0]], dtype="float32"))
    assert m["Recall@5"] == 1.0
    assert m["MRR@10"] == 1.0


def test_strong_false_positive_counter_works():
    """应弃权（无关）却判 `strong` → strong_fp_rate 上升（**A3a 主指标**）。"""
    irr = [{"query": "无关问题", "answer_chunk_ids": []}]
    judge = _FakeJudge({"无关问题": Evidence(0.5, 1.0, "strong")})
    m = ev.evaluate([], irr, judge, _META, _MATRIX, np.array([[1.0, 0.0]], dtype="float32"))
    assert m["strong_fp_rate"] == 1.0


def test_weak_is_counted_separately_from_strong():
    """`weak` 不算 strong 误放行，但要单独计数（它会进生成上下文）。"""
    irr = [{"query": "模糊问题", "answer_chunk_ids": []}]
    judge = _FakeJudge({"模糊问题": Evidence(0.08, 0.5, "weak")})
    m = ev.evaluate([], irr, judge, _META, _MATRIX, np.array([[1.0, 0.0]], dtype="float32"))
    assert m["strong_fp_rate"] == 0.0
    assert m["weak_fp_rate"] == 1.0


def test_scan_returns_curve_grid():
    """扫描必须覆盖多个 (sar, v1) 组合，供按代价选工作点。"""
    judge = _FakeJudge({"b": Evidence(0.2, 0.8, "strong")})
    pts = ev.scan([{"query": "b", "answer_chunk_ids": []}],
                  [{"query": "a", "answer_chunk_ids": []}], judge)
    assert len(pts) >= 10, "曲线点太少无法选工作点"
    assert all(len(p) == 4 for p in pts)
    assert any(fp == 0.0 for _, _, fp, _ in pts), "扫描里应存在零误放行的点"


def test_load_missing_split_returns_empty(tmp_path, monkeypatch):
    """评测集缺失时返回空列表，不抛异常（便于首次运行给出明确提示）。"""
    monkeypatch.setattr(ev, "GOLDEN", str(tmp_path / "nope"))
    assert ev.load("holdout", "rel") == []
    assert ev.load("tuning", "irr") == []

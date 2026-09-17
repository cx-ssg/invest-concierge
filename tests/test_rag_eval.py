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
    """域内可答被判 `none` → over_abstain_rate 上升（**闸门标注保守度**）。

    ⚠️ 2026-09-17 契约修正：旧断言还写着 `Recall@5 == 0.0`（"弃权的查询不可能召回"），
    这个前提被**独立审计实测证伪** —— holdout 的 `rel-0015` 的 gold 排在 **rank 1** 仍被判 none
    → 「判 none」与「检索不到」是**两件事**。评测器已删掉 none 档的 `continue`：
    `over_abstain` 记标注保守度、`Recall@k` 记检索器能力、`guarded_recall` 记产线实际拿到 gold 的比例。
    """
    rel = [{"query": "可答问题", "answer_chunk_ids": [1]}]
    judge = _FakeJudge({"可答问题": Evidence(0.0, 0.0, "none")})   # 故意误弃权
    m = ev.evaluate(rel, [], judge, _META, _MATRIX, np.array([[1.0, 0.0]], dtype="float32"))
    assert m["over_abstain_rate"] == 1.0
    assert m["n_over_abstain"] == 1
    assert m["Recall@5"] == 1.0, "判 none **不等于**检索不到 —— 两者已解耦"
    assert m["guarded_recall"] == 1.0, "none 档不再清空结果 → 产线口径也应拿得到 gold"


def test_assert_clean_holdout_rejects_v1_sample():
    """holdout 负例混入 v1 → 必须**显式报错**（独立审计 P2-2）。

    背景：`batch` 字段此前**无任何代码消费**，而「holdout 的 50 条负例全部 `batch=v2`」
    正是"干净验收组"这个核心卖点的**全部依据** —— 没有强制力的话，
    未来任何人把调参时看过的样本挪进 holdout，指标都不会报警。
    """
    import pytest
    with pytest.raises(ValueError) as ei:
        ev.assert_clean_holdout([{"id": "irr-0001", "batch": "v1"}])
    assert "irr-0001" in str(ei.value), "报错要点名是哪几条，便于修复"
    ev.assert_clean_holdout([{"id": "irr-0101", "batch": "v2"}])     # 合规 → 不抛
    ev.assert_clean_holdout([])                                       # 空集 → 不抛


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


def test_weak_is_delegation_not_failure():
    """`weak` 是**委派点**（交给 LLM 判官），不是检索失败 —— 单独计为 `delegated_rate`。

    2026-09-17 口径修正：外部评审指出旧名 `weak_fp_rate` 与标注规范矛盾
    （`tests/golden/rag/README.md` 里三类负例**都允许** `weak`，只有 `strong` 是禁止的）。
    """
    irr = [{"query": "模糊问题", "kind": "near_miss", "answer_chunk_ids": []}]
    judge = _FakeJudge({"模糊问题": Evidence(0.08, 0.5, "weak")})
    m = ev.evaluate([], irr, judge, _META, _MATRIX, np.array([[1.0, 0.0]], dtype="float32"))
    assert m["strong_fp_rate"] == 0.0, "weak 不算 strong 违规"
    assert m["delegated_rate"] == 1.0, "但要计入委派率（成本）"
    assert m["by_kind"]["near_miss"]["weak"] == 1


def test_strong_fp_is_reported_per_kind():
    """`strong_fp` 必须**按 kind 分列** —— 混池会让漂亮的类与违规的类互相抵消。

    外部评审实测：域外类 0/6（完美）与近义干扰类 1/17（真违规）混池后只剩 2.8%，
    看不出违规究竟出在哪一类。
    """
    irr = [{"query": "域外问题", "kind": "out_of_domain", "answer_chunk_ids": []},
           {"query": "近义问题", "kind": "near_miss", "answer_chunk_ids": []}]
    judge = _FakeJudge({"域外问题": Evidence(0.0, 0.0, "none"),
                        "近义问题": Evidence(0.5, 1.0, "strong")})
    m = ev.evaluate([], irr, judge, _META, _MATRIX,
                    np.array([[1.0, 0.0], [0.0, 1.0]], dtype="float32"))  # 数量须与查询一致
    assert m["by_kind"]["out_of_domain"]["strong"] == 0
    assert m["by_kind"]["near_miss"]["strong"] == 1


def test_evaluate_rejects_qvec_length_mismatch():
    """`qvecs` 条数与查询数不一致时必须**显式报错**。

    ⚠️ 旧实现用 `zip(rows_irr, qvecs[...])` → 长度不匹配会**静默截断**，
    指标少算一部分而看不出（2026-09-17 本组用例首次运行时真实踩到）。
    """
    import pytest
    irr = [{"query": "a", "kind": "near_miss", "answer_chunk_ids": []},
           {"query": "b", "kind": "near_miss", "answer_chunk_ids": []}]
    with pytest.raises(ValueError):
        ev.evaluate([], irr, _FakeJudge({}), _META, _MATRIX,
                    np.array([[1.0, 0.0]], dtype="float32"))          # 故意少一行


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

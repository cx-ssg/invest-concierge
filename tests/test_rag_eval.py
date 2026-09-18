# -*- coding: utf-8 -*-
"""评测器回归锁（纯逻辑，不依赖 kb.db / ollama）。

重点：三个指标的口径必须**可被打破**（否则又是一组自证测试）——
每个用例都构造出"指标应当变化"的场景。
"""
import json

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
    `over_abstain` 记标注保守度、`Recall@k` 记检索器能力、`trusted_recall` 记判据采信过的召回。
    """
    rel = [{"query": "可答问题", "answer_chunk_ids": [1]}]
    judge = _FakeJudge({"可答问题": Evidence(0.0, 0.0, "none")})   # 故意误弃权
    m = ev.evaluate(rel, [], judge, _META, _MATRIX, np.array([[1.0, 0.0]], dtype="float32"))
    assert m["over_abstain_rate"] == 1.0
    assert m["n_over_abstain"] == 1
    assert m["Recall@5"] == 1.0, "判 none **不等于**检索不到 —— 两者已解耦"
    assert m["trusted_recall"] == 0.0, \
        "但判据没采信 → `trusted_recall` 归零（这正是它比旧 `guarded_recall` 有用的地方）"


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
    ev.assert_clean_holdout([])                                       # 空集 → 不抛（函数本身不管空集）


def test_trusted_recall_turns_red_when_judge_degrades():
    """`trusted_recall` 必须能检测「判据退化」—— 这是它替代 `guarded_recall` 的全部理由。

    2026-09-18（两份外部审计**各自实测**）：`guarded_recall` **恒等于** `Recall@k` ——
    删掉 none 档硬停后 `run_hybrid` 的 judge 只用于算 evidence、不参与过滤（构造性恒等），
    把判据换成「恒返回 none」它**纹丝不动**。而 `trusted_recall`（gold 在 top-k **且** level != none）
    在同样场景下会掉到 0 —— 这正是本次要修的那类退化。
    """
    rel = [{"query": "可答问题", "answer_chunk_ids": [1]}]
    m1 = ev.evaluate(rel, [], _FakeJudge({"可答问题": Evidence(0.5, 1.0, "strong")}),
                     _META, _MATRIX, np.array([[1.0, 0.0]], dtype="float32"))
    assert m1["Recall@5"] == 1.0 and m1["trusted_recall"] == 1.0
    m2 = ev.evaluate(rel, [], _FakeJudge({"可答问题": Evidence(0.0, 0.0, "none")}),
                     _META, _MATRIX, np.array([[1.0, 0.0]], dtype="float32"))
    assert m2["Recall@5"] == 1.0, "裸检索能力不受判据影响"
    assert m2["trusted_recall"] == 0.0, "判据恒 none → 采信召回必须归零（旧 guarded_recall 测不出）"


def test_load_holdout_rejects_empty_negative_set(tmp_path, monkeypatch):
    """`load_holdout()` 是 holdout 的唯一入口：**空负例必须报错**，不得静默产出「干净」报告。

    2026-09-18（审计 B 的 P2-1）：`irr` 为空时旧断言放行 → `n_irr=0`、`by_kind={}`、
    `strong_fp=0.000`，而 `main()` 里 A3a 主结论那行根本不打印。
    """
    import pytest
    # ⚠️ 2026-09-18：`rel` 必须非空 —— 本测试的目标是「空**负例**」，
    # rel 为空会先撞上另一道门（见 `test_load_holdout_rejects_empty_positive_set`）。
    (tmp_path / "queries_holdout_rel.json").write_text(
        json.dumps([{"id": "rel-0001", "query": "x", "answer_chunk_ids": [1],
                     "batch": "clean"}], ensure_ascii=False), encoding="utf-8")
    (tmp_path / "queries_holdout_irr.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(ev, "GOLDEN", str(tmp_path))
    with pytest.raises(ValueError) as ei:
        ev.load_holdout()
    assert "负例为空" in str(ei.value)


def test_load_holdout_rejects_v1_negative(tmp_path, monkeypatch):
    """`load_holdout()` 必须对混入的 v1 负例报错 —— 校验下沉到数据入口，绕过 CLI 也绕不过。

    ⚠️ 2026-09-18：`rel` 必须**非空** —— 否则会先撞上「空正例」那道门
    （见 `test_load_holdout_rejects_empty_positive_set`），本测试就测不到它的目标（v1 负例）。
    """
    import pytest
    (tmp_path / "queries_holdout_rel.json").write_text(
        json.dumps([{"id": "rel-0001", "query": "x", "answer_chunk_ids": [1],
                     "batch": "clean"}], ensure_ascii=False), encoding="utf-8")
    (tmp_path / "queries_holdout_irr.json").write_text(
        json.dumps([{"id": "irr-0001", "kind": "near_miss", "query": "x",
                     "answer_chunk_ids": [], "batch": "v1"}], ensure_ascii=False),
        encoding="utf-8")
    monkeypatch.setattr(ev, "GOLDEN", str(tmp_path))
    with pytest.raises(ValueError) as ei:
        ev.load_holdout()
    assert "irr-0001" in str(ei.value)


def test_load_holdout_rejects_empty_positive_set(tmp_path, monkeypatch):
    """**空正例**同样必须被拦 —— 第六轮审计二 P1-2 的「镜像洞」。

    上一轮只堵了负例一侧（`if not irr: raise`），`rel` 完全裸奔：
    空正例时 `n_rel = max(0, 1) = 1` ⇒ `Recall@5 = 0`、`trusted_recall = 0`、
    `strong_rel_rate = 0`，**而 A3a 仍打印 `20/20 = 1.000 <<< 主结论`**，
    无异常、`EXIT=0` —— 「没有正例、却看起来干净」的报告可以**静默产出**。
    本测试锁这道新门（函数入口 + CLI 入口各一次）。
    """
    import pytest
    (tmp_path / "queries_holdout_rel.json").write_text("[]", encoding="utf-8")
    (tmp_path / "queries_holdout_irr.json").write_text(
        json.dumps([{"id": "irr-0001", "kind": "near_miss", "query": "x",
                     "answer_chunk_ids": [], "batch": "clean"}], ensure_ascii=False),
        encoding="utf-8")
    monkeypatch.setattr(ev, "GOLDEN", str(tmp_path))
    with pytest.raises(ValueError) as ei:
        ev.load_holdout()
    assert "正例为空" in str(ei.value)
    with pytest.raises(ValueError) as ei2:
        ev.main(["--split", "holdout"])          # ← CLI 入口也必须拦住
    assert "正例为空" in str(ei2.value)


def test_main_cli_path_actually_uses_load_holdout(tmp_path, monkeypatch):
    """**走 CLI 路径**验证 holdout 校验真的接线了（审计二 P1-1 的回归锁）。

    审计二实测：`load_holdout()` 此前**零生产调用点** —— `main()` 走的是
    `load()` + `assert_clean_holdout()`，而上面两条测试测的是**函数本身**，
    于是「`main()` 改走它」这个声称与实际不符、套件却**全绿**。
    ⇒ **本测试锁的是调用链，不是函数**：把 `main()` 里的接线改回去，它必须变红。

    ⚠️ 2026-09-18：`rel` 补成非空 —— 本测试的目标是「空**负例**」，rel 为空会先撞另一道门。
    """
    import pytest
    monkeypatch.setattr(ev, "GOLDEN", str(tmp_path))
    (tmp_path / "queries_holdout_rel.json").write_text(
        json.dumps([{"id": "rel-0001", "query": "x", "answer_chunk_ids": [1],
                     "batch": "clean"}], ensure_ascii=False), encoding="utf-8")
    (tmp_path / "queries_holdout_irr.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError) as ei:
        ev.main(["--split", "holdout"])          # ← 走 CLI 入口，不是直调函数
    assert "负例为空" in str(ei.value)


def test_main_cli_path_rejects_v1_negative(tmp_path, monkeypatch):
    """CLI 路径同样必须拦住混入的 v1 负例（同上：锁调用链，不锁函数）。

    ⚠️ 2026-09-18：`rel` 补成非空（理由同 `test_load_holdout_rejects_v1_negative`）。
    """
    import pytest
    monkeypatch.setattr(ev, "GOLDEN", str(tmp_path))
    (tmp_path / "queries_holdout_rel.json").write_text(
        json.dumps([{"id": "rel-0001", "query": "x", "answer_chunk_ids": [1],
                     "batch": "clean"}], ensure_ascii=False), encoding="utf-8")
    (tmp_path / "queries_holdout_irr.json").write_text(
        json.dumps([{"id": "irr-0001", "kind": "near_miss", "query": "x",
                     "answer_chunk_ids": [], "batch": "v1"}], ensure_ascii=False),
        encoding="utf-8")
    with pytest.raises(ValueError) as ei:
        ev.main(["--split", "holdout"])
    assert "irr-0001" in str(ei.value)


def test_recall_and_mrr_when_answer_returned():
    """答案块在 top-k 里 → Recall@5 = 1、MRR@10 = 1。"""
    rel = [{"query": "茅台", "answer_chunk_ids": [1]}]
    judge = _FakeJudge({"茅台": Evidence(0.5, 1.0, "strong")})
    m = ev.evaluate(rel, [], judge, _META, _MATRIX, np.array([[1.0, 0.0]], dtype="float32"))
    assert m["Recall@5"] == 1.0
    assert m["MRR@10"] == 1.0


def test_no_strong_tier_in_metrics():
    """撤下 `strong` 档后，指标字典里**不再有** `strong_fp_rate` / `strong_rel_rate`（锁撤档）。

    背景：这两个量度量的档位已不存在。**但它们要回答的问题没有消失** ——
    「负例有没有被误放行」现在由 `delegated_rate` 承担：撤档后任何非 none 的负例都落在 `weak`，
    而 `weak` 一律带 `WEAK_EVIDENCE_NOTE` 警示（**不再有"跳过警示"的通道**）。
    """
    irr = [{"query": "模糊问题", "kind": "near_miss", "answer_chunk_ids": []}]
    judge = _FakeJudge({"模糊问题": Evidence(0.08, 0.5, "weak")})
    m = ev.evaluate([], irr, judge, _META, _MATRIX, np.array([[1.0, 0.0]], dtype="float32"))
    assert "strong_fp_rate" not in m, "撤档后不应再报 strong_fp_rate"
    assert "strong_rel_rate" not in m, "撤档后不应再报 strong_rel_rate"
    assert m["delegated_rate"] == 1.0, "非 none 的负例全部计入（= 未通过判据的结果占比）"
    assert m["by_kind"]["near_miss"]["weak"] == 1


def test_weak_level_counts_as_non_none():
    """`weak` = **非 none 的统称** —— 它不再表示"委派给判官"（判官从未实现）。

    ⚠️ 2026-09-18 语义变更（撤档的副作用）：`delegated_rate` 从「**成本**」变成「**风险面**」。
    """
    irr = [{"query": "模糊问题", "kind": "near_miss", "answer_chunk_ids": []},
           {"query": "域外问题", "kind": "out_of_domain", "answer_chunk_ids": []}]
    judge = _FakeJudge({"模糊问题": Evidence(0.08, 0.5, "weak"),
                        "域外问题": Evidence(0.0, 0.0, "none")})
    m = ev.evaluate([], irr, judge, _META, _MATRIX,
                    np.array([[1.0, 0.0], [0.0, 1.0]], dtype="float32"))
    assert m["delegated_rate"] == 0.5, "只有 weak 计入；none 不计"
    assert m["by_kind"]["out_of_domain"]["none"] == 1
    assert m["by_kind"]["near_miss"]["weak"] == 1


def test_by_kind_has_no_strong_column():
    """`by_kind` 必须**按 kind 分列** —— 混池会让漂亮的类与糟糕的类互相抵消（外部评审实测）。

    ⚠️ 2026-09-18 撤档后**只有 none / weak 两列**（`strong` 列已删）。
    """
    irr = [{"query": "域外问题", "kind": "out_of_domain", "answer_chunk_ids": []},
           {"query": "近义问题", "kind": "near_miss", "answer_chunk_ids": []}]
    judge = _FakeJudge({"域外问题": Evidence(0.0, 0.0, "none"),
                        "近义问题": Evidence(0.08, 0.5, "weak")})
    m = ev.evaluate([], irr, judge, _META, _MATRIX,
                    np.array([[1.0, 0.0], [0.0, 1.0]], dtype="float32"))  # 数量须与查询一致
    assert "strong" not in m["by_kind"]["out_of_domain"], "撤档后 by_kind 不应再有 strong 列"
    assert m["by_kind"]["out_of_domain"]["none"] == 1
    assert m["by_kind"]["near_miss"]["weak"] == 1


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


def test_scan_returns_none_curve_grid():
    """扫描必须覆盖多个 none 档阈值组合，供按代价选工作点。

    ⚠️ 2026-09-18 **撤下 `strong` 档**后，`scan()` 的 strong 曲线**不再用于定阈值**
    （已经没有对象）—— 它保留在返回值里仅作**历史对照**（`curves["strong"]`），
    故本测试**只对 none 曲线下实质断言**。
    """
    judge = _FakeJudge({"b": Evidence(0.2, 0.8, "weak")})
    curves = ev.scan([{"query": "b", "answer_chunk_ids": []}],
                     [{"query": "a", "answer_chunk_ids": []}], judge)
    none_pts = curves["none"]
    assert len(none_pts) >= 10, "none 档曲线点太少无法选工作点"
    assert all(len(p) == 4 for p in none_pts)
    assert any(fp == 0.0 for _, _, fp, _ in none_pts), "扫描里应存在零误放行的点"
    # 假想 strong 曲线仍在（8 个点），但**只作历史对照**，不再是可选的阈值工作点
    assert len(curves["strong"]) >= 6, "（历史对照用）假想 strong 曲线点数不足"


def test_load_missing_split_returns_empty(tmp_path, monkeypatch):
    """评测集缺失时返回空列表，不抛异常（便于首次运行给出明确提示）。"""
    monkeypatch.setattr(ev, "GOLDEN", str(tmp_path / "nope"))
    assert ev.load("holdout", "rel") == []
    assert ev.load("tuning", "irr") == []

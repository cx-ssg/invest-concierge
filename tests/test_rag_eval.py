# -*- coding: utf-8 -*-
"""评测器回归锁（纯逻辑，不依赖 kb.db / ollama）。

重点：三个指标的口径必须**可被打破**（否则又是一组自证测试）——
每个用例都构造出"指标应当变化"的场景。
"""
import json

import numpy as np

from scripts import rag_eval as ev
from utils.rag import evidence as ev_mod
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
    # ⚠️ 2026-09-19 第七轮审计一 P3-8：原写 `"strong"` —— **生产已不可能产出该值**
    # （`LEVEL_STRONG` 已随撤档删除）。测试虽仍通过（`evaluate` 只判 `== LEVEL_NONE`），
    # 但那样**行使的是一个不可达状态** ⇒ 改为 `"weak"`（撤档后唯一非 none 的档）。
    m1 = ev.evaluate(rel, [], _FakeJudge({"可答问题": Evidence(0.5, 1.0, "weak")}),
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


def test_main_cli_path_routes_through_load_holdout(tmp_path, monkeypatch):
    """**强锁调用链**：`main()` 必须**真的调用** `load_holdout()`（审计二 U9 的修正）。

    ⚠️ 本测试原名为 `test_main_cli_path_rejects_v1_negative`，**名不副实**：
    它只断言「`main()` 抛 `ValueError` 且消息含 `irr-0001`」——
    而**把接线改回旧写法**（`load()` + `assert_clean_holdout()`）**它照样通过**
    （因为 `assert_clean_holdout([v1 行])` 本身就会抛）。
    也就是说，它测的是**那个断言的行为**，**不是 `main()` 的接线** ——
    与上一轮 `load_holdout()` 死代码事件是同一个盲区。

    本测试改为**直接监视 `load_holdout` 是否被调用** ⇒ 接线一旦改回去必然变红。
    （姊妹测试 `test_main_cli_path_actually_uses_load_holdout` 仍然有效：
    **空负例只有 `load_holdout()` 里那道门才拦得住** —— `assert_clean_holdout([])` 不抛。）
    """
    import pytest
    monkeypatch.setattr(ev, "GOLDEN", str(tmp_path))
    called = []
    real = ev.load_holdout

    def spy():
        called.append(True)
        return real()

    monkeypatch.setattr(ev, "load_holdout", spy)          # ← 模块全局名，正是 main() 调的那个
    (tmp_path / "queries_holdout_rel.json").write_text("[]", encoding="utf-8")
    (tmp_path / "queries_holdout_irr.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        ev.main(["--split", "holdout"])
    assert called, ("`main()` 没有调用 `load_holdout()` —— 接线被改回了旧写法"
                    "（`load()` + `assert_clean_holdout()`），审计二 U9 指出的正是这个盲区")


def test_recall_and_mrr_when_answer_returned():
    """答案块在 top-k 里 → Recall@5 = 1、MRR@10 = 1。"""
    rel = [{"query": "茅台", "answer_chunk_ids": [1]}]
    judge = _FakeJudge({"茅台": Evidence(0.5, 1.0, "weak")})   # strong 档已撤，改用 weak（见上）
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

    ⚠️ 2026-10-01（F1）：原末尾断言 `curves["strong"]` 已删除 —— 那条"假想 strong 曲线"
    描述的对象已于 2026-09-18 撤档（`7270159`）后不存在，属**语义残留**。
    它的位置现由 `curves["sar_none"]`（下方三条测试）承担。
    """
    judge = _FakeJudge({"b": Evidence(0.2, 0.8, "weak")})
    curves = ev.scan([{"query": "b", "answer_chunk_ids": []}],
                     [{"query": "a", "answer_chunk_ids": []}], judge)
    none_pts = curves["none"]
    assert len(none_pts) >= 10, "none 档曲线点太少无法选工作点"
    assert all(len(p) == 4 for p in none_pts)
    assert any(fp == 0.0 for _, _, fp, _ in none_pts), "扫描里应存在零误放行的点"


# --- F1（2026-10-01）：`SAR_NONE` 敏感性表 -----------------------------------
# 背景：调高 `SAR_NONE` 会同时压低「负例残留暴露」、抬高「可答查询被剥夺
# 引用凭据的比例（oa）」。这条权衡在 5×7=35 点的 `curves["none"]` 网格里**存在但被
# 噪声埋住** ⇒ 单独出一张 v1 固定的 6 行表。计划：docs/M1_F1_SAR_NONE_PLAN.md


def _sensitivity_judge():
    """按 sar/v1 分层的假证据 —— 覆盖**三个判别力维度**（缺一即盲区）：

    | 样本 | sar | v1 | 作用 |
    |---|---|---|---|
    | `r05` | 0.05 | 0.10 | 低于所有上界 → 每个点都判 none |
    | `r10c` | **0.10** | 0.10 | **边界锁**：sar 恰等于扫描点 ⇒ 只有严格 `<` 才不判 none |
    | `r12` | 0.12 | 0.10 | 仅 `sar_n > 0.12` 的点判 none |
    | `r10b` | 0.10 | **0.36** | **v1 阈值锁（上侧）**：只比 `V1_NONE=0.35` 高 0.01 |
    | `r10e` | 0.10 | **0.34** | **v1 阈值锁（下侧）**：只比 `V1_NONE=0.35` 低 0.01 —— 与 `r10b` 一起**夹住 0.35** |
    | `i03` | 0.03 | 0.10 | 每个点都判 none → 永不暴露 |
    | `i18` | 0.18 | 0.10 | 仅 `sar_n > 0.18` 的点判 none |

    实测期望（n_rel=5 / n_irr=2）：

    | sar_n | 0.06 | 0.10 | 0.15 | 0.20 | 0.25 | 0.30 |
    |---|---|---|---|---|---|---|
    | oa（正例判 none） | 0.20 | 0.20 | 0.80 | 0.80 | 0.80 | 0.80 |
    | neg（负例未判 none） | 0.5 | 0.5 | 0.5 | 0.0 | 0.0 | 0.0 |

    ⚠️ 三条锁的**具体**含义：
    - 若实现把 `sar < sar_n` 写成 `<=` ⇒ `sar_n=0.10` 处 oa 从 0.20 变 0.40（`r10c`/`r10e` 被误判）
    - 若 v1 阈值被写死成 `> 0.36`（如 0.40 / 0.45）⇒ `sar_n=0.30` 处 oa 变 1.00（`r10b` 被误判）
    - 若 v1 阈值被写死成 `< 0.34`（如 0.30）⇒ 同上处 oa 变 0.60（`r10e`/`r10b` 被误判）
    ⚠️ **但有限样本永远有盲区**（第八轮外部审计实测：阈值落在 `(0.10, 0.36]` 时六格全不变、四条锁全绿）。
    结构性修法不在这里，而在「**表与生产判据共享唯一实现**」——
    见 `test_scan_uses_shared_none_predicate` 与 `utils/rag/evidence.py::is_none`。
    """
    return _FakeJudge({
        "r05": Evidence(0.05, 0.10, "none"),
        "r10c": Evidence(0.10, 0.10, "none"),   # 边界：恰等于扫描点 0.10
        "r12": Evidence(0.12, 0.10, "weak"),
        "r10b": Evidence(0.10, 0.36, "weak"),   # v1 只比 V1_NONE=0.35 高 0.01（见 docstring）
        "r10e": Evidence(0.10, 0.34, "weak"),   # v1 只比 V1_NONE=0.35 低 0.01（夹住 0.35）
        "i03": Evidence(0.03, 0.10, "none"),
        "i18": Evidence(0.18, 0.10, "weak"),
    })


def _sensitivity_rows():
    rel = [{"query": q, "answer_chunk_ids": []} for q in ("r05", "r10c", "r12", "r10b", "r10e")]
    irr = [{"query": q, "answer_chunk_ids": []} for q in ("i03", "i18")]
    return rel, irr


def test_scan_returns_sar_none_sensitivity_grid():
    """`SAR_NONE` 敏感性表：v1 固定 `V1_NONE`，扫 sar 上界 —— 服务「要不要调高 SAR_NONE」。

    ⚠️ **判别力**（防"恒等摆设"）：除形状外还锁**数值真的随 `sar_n` 变化**、以及
    **边界与 v1 阈值两处实现细节**（见 `_sensitivity_judge` docstring）——
    只锁形状的测试在被测函数退化成常量时**不会红**（本仓 2026-09-18 踩过
    `guarded_recall` 恒等指标的坑；2026-10-01 又因"只测函数不测调用链"被审计打回）。
    """
    import pytest
    rel, irr = _sensitivity_rows()
    curves = ev.scan(rel, irr, _sensitivity_judge())
    grid = curves["sar_none"]
    assert len(grid) == 6, "敏感性表应为 6 行（0.06/0.10/0.15/0.20/0.25/0.30）"
    assert all(len(p) == 3 for p in grid), "每行应为 (sar_n, 负例残留暴露, oa)"
    assert any(abs(s - ev_mod.SAR_NONE) < 1e-9 for s, _, _ in grid), "表里必须含当前 SAR_NONE 点"
    # 单调性（数学必然 —— 写反方向的实现会被抓住）
    for (s1, n1, o1), (s2, n2, o2) in zip(grid, grid[1:]):
        assert s2 > s1, "扫描点必须递增"
        assert n2 <= n1 + 1e-12, "sar_n 增大时负例残留暴露不应上升"
        assert o2 >= o1 - 1e-12, "sar_n 增大时 oa 不应下降"
    by_sar = {s: (n, o) for s, n, o in grid}
    assert by_sar[0.30] != by_sar[0.06], "表不随 sar_n 变化 —— 恒等摆设"
    assert by_sar[0.06] == pytest.approx((0.5, 0.20)), "当前点数值与期望不符（口径或符号写反）"
    assert by_sar[0.30] == pytest.approx((0.0, 0.80)), "末点数值与期望不符（口径或符号写反）"
    # 边界锁：sar 恰等于 0.10 的样本不得被判 none（严格小于）
    assert by_sar[0.10][1] == pytest.approx(0.20), \
        "边界写成 `<=` 了 —— sar 恰等于阈值时被误判 none（sar_n=0.10 处 oa 应变 0.40）"
    # v1 阈值锁：v1=0.36（≥ V1_NONE）与 v1=0.34（< V1_NONE）两个贴边样本**夹住 0.35**
    assert by_sar[0.30][1] == pytest.approx(0.80), \
        "v1 阈值用错 —— 两个贴边样本之一被误判（写死 >0.36 会让 oa 变 1.00、写死 <0.34 会变 0.60）"


def test_sar_none_grid_matches_none_grid_at_v1_none():
    """两张表在共同点 `(sar_n, V1_NONE)` 上必须**逐位一致** —— 防同一事实两处口径漂移。

    （新表是独立循环算的，不是为了"少写代码"从 none 网格派生 —— 独立算 + 交叉锁，
    既免疫将来 `curves["none"]` 网格变动，又不会静默分叉。）
    """
    import pytest
    rel, irr = _sensitivity_rows()
    curves = ev.scan(rel, irr, _sensitivity_judge())
    none_map = {(s, v): (w, o) for s, v, w, o in curves["none"]}
    for s, neg, oa in curves["sar_none"]:
        assert (s, ev_mod.V1_NONE) in none_map, "none 网格缺少共同点 sar_n=%.2f" % s
        assert none_map[(s, ev_mod.V1_NONE)] == pytest.approx((neg, oa)), \
            "sar_n=%.2f 处两张表不一致 —— 口径已分叉" % s


def test_scan_uses_shared_none_predicate(monkeypatch):
    """`scan()` 的闸门必须走**共享判据** `evidence.is_none()`，且 v1 维必须传生产常量。

    ⚠️ 2026-10-01（**第八轮外部审计**双路实测）：
    - 表用字面量**重新实现**了闸门 → 把表内 v1 阈值换成 `0.30`（与生产 0.35 同侧不同值），
      **344 条全绿**（漏网）；
    - 把**生产**门限换成 `0.30` / `10` 同样全绿 —— 两侧都没有交叉锁。
    本条同时锁两件事：① 表真的**调用**共享判据；② 表在 v1 维传的是 `V1_NONE` 而非字面量。
    （修法本身是结构性的：判据只留**一处实现**，改一处不可能只改一半。）
    """
    import utils.rag.evidence as e_mod
    calls = []
    real = e_mod.is_none

    def spy(sar, v1, sar_none=None, v1_none=None):
        calls.append((sar_none, v1_none))
        return real(sar, v1, sar_none, v1_none)

    monkeypatch.setattr(e_mod, "is_none", spy)
    rel, irr = _sensitivity_rows()
    ev.scan(rel, irr, _sensitivity_judge())

    assert calls, "`scan()` 没有调用共享判据 `evidence.is_none()` —— 闸门被复写了"
    # 所有传入门限必须落在**允许集合**（稠密网格取值）内：
    #   —— 表侧写死别的字面量（如 0.30，第八轮审计实测的漏网变异）会立刻越界 ⇒ 红。
    grid_v1 = {0.35, 0.45, 0.55, 0.65, 0.75}
    bad_v1 = {v for _, v in calls} - grid_v1
    assert not bad_v1, \
        "出现了网格取值之外的 v1 门限 %r —— 表侧写死字面量会与判据分叉" % sorted(bad_v1)
    grid_sar = {0.06, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40}
    bad_sar = {s for s, _ in calls} - grid_sar
    assert not bad_sar, "出现了网格取值之外的 sar 门限 %r" % sorted(bad_sar)
    # ⚠️ 只断言「calls 非空」不够（第一版就是这么写的，被自己的变异 M4 证伪）：
    #    若**只有网格**走共享判据、表侧自己重算公式，calls 依然非空 ⇒ 漏网。
    #    也不能按"每个 sar 点的总次数"粗算（网格单点是 5 v1 × 2 行 = 10 次，阈值取小了仍漏）。
    #    这里按 **(sar_n, v1_none) 调用对**精确锁：
    #    网格贡献 irr+rel 各 1 次（2），敏感性表再贡献 2 次 ⇒ 共 4。
    from collections import Counter
    pair = Counter(calls)
    # 每次迭代对**每条查询**各调 1 次 ⇒ 单轮 = len(rel) + len(irr)；网格 1 轮 + 表 1 轮。
    #   ⚠️ 这两个数字是**实测**出来的，不是推出来的：我先前两次手算都错了
    #   （先按"每迭代 1 次"、再漏乘"每条查询"→ 实测单点是 7 次而非 2 次）。
    need = (len(rel) + len(irr)) * 2
    thin = {s: pair[(s, e_mod.V1_NONE)] for s in ev.SAR_NONE_SWEEP
            if pair[(s, e_mod.V1_NONE)] < need}
    assert not thin, ("这些 (sar_n, V1_NONE) 组合的调用次数少于 %d：%r —— "
                      "多半是敏感性表**没走共享判据**（自己重算了公式）" % (need, thin))


def test_scan_has_no_strong_curve():
    """撤档后 `--scan` **不应再返回假想 strong 曲线**（对象已不存在，属残留）。

    2026-10-01 F1：它被 `SAR_NONE` 敏感性表替代 —— 而它当初的存在理由（"将来若恢复三档"）
    已随用户拍板的撤档决定作废。
    """
    judge = _FakeJudge({"b": Evidence(0.2, 0.8, "weak")})
    curves = ev.scan([{"query": "b", "answer_chunk_ids": []}],
                     [{"query": "a", "answer_chunk_ids": []}], judge)
    assert "strong" not in curves, "假想 strong 曲线仍在 —— 2026-09-18 撤档的残留未清"


class _FakeConn:
    """只为 `main()` 的 `conn.close()` 提供一个对象。"""

    def close(self):
        pass


_FAKE_METRICS = {
    "n_rel": 4, "n_irr": 2, "n_over_abstain": 1,
    "by_kind": {"out_of_domain": {"n": 2, "none": 2, "weak": 0},
                "near_miss": {"n": 2, "none": 1, "weak": 1}},
    "delegated_rate": 0.5, "abstain_recall": 0.5, "over_abstain_rate": 0.25,
    "Recall@%d" % ev.TOP_K: 1.0, "tool_recall": 1.0, "hard_stop_count": 0,
    "trusted_recall": 0.75, "MRR@10": 1.0,
}


def test_main_scan_path_prints_sar_none_table(monkeypatch, capsys):
    """**走 CLI 路径**锁 `--scan` 的打印接线（本仓盲区：测试测函数、不测调用链）。

    为什么需要它：F1 把「假想 strong 档曲线」换成 `SAR_NONE` 敏感性表。
    若只测 `scan()` 的**返回值**，打印这一层**零覆盖** —— 把 `main()` 里的
    `curves["sar_none"]` 改回旧键、或整段删掉，套件照样全绿。
    （同源教训：`load_holdout()` 曾是**死代码**，而 340 条测试全绿。）
    ⚠️ 2026-10-01 critic 审计的 P2 就是「新表三条测试测函数不测调用链」⇒ 本用例即其修法。

    做法：把 `main()` 的上下游全部替换成假对象，**只保留打印逻辑与它的接线**在测。
    """
    import re
    rel, irr = _sensitivity_rows()
    judge = _sensitivity_judge()
    monkeypatch.setattr(ev, "load_holdout", lambda: (rel, irr))
    monkeypatch.setattr(ev.rag_store, "get_conn", lambda db=None: _FakeConn())
    monkeypatch.setattr(ev.rag_store, "load_index", lambda conn: (_META, _MATRIX))
    monkeypatch.setattr(ev, "EvidenceJudge", lambda corpus: judge)
    monkeypatch.setattr(ev, "embed_texts_batched", lambda qs: [[1.0, 0.0] for _ in qs])
    monkeypatch.setattr(ev, "evaluate", lambda *a, **k: dict(_FAKE_METRICS))

    rc = ev.main(["--scan"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "SAR_NONE 敏感性表" in out, "`--scan` 没打印敏感性表 —— 打印接线断了"
    assert out.count("<- 当前") == 1, "`<- 当前` 标记应恰好出现一次"
    assert "假想" not in out, "旧的「假想 strong 曲线」仍在打印 —— 残留未清"
    rows = re.findall(r"^\s+0\.\d\d\s+[01]\.\d\d\d\s+[01]\.\d\d\d", out, re.M)
    assert len(rows) == 6, "敏感性表应有 6 行数据，实际 %d 行" % len(rows)
    # 列序 + 标记行 + 表头口径（第二轮审计指出：只数行数会漏掉"列交换"/"标记打错行"两种改坏方式）
    assert re.search(r"^\s+0\.06\s+0\.500\s+0\.200\s*<-\s*当前\s*$", out, re.M), \
        "当前行应为 `0.06  0.500  0.200  <- 当前`（列序或标记行被改坏了）"
    assert "v1 固定 0.35" in out, "表头应点名 v1 固定为 `V1_NONE`（口径必须写在输出里）"


def test_load_missing_split_returns_empty(tmp_path, monkeypatch):
    """评测集缺失时返回空列表，不抛异常（便于首次运行给出明确提示）。"""
    monkeypatch.setattr(ev, "GOLDEN", str(tmp_path / "nope"))
    assert ev.load("holdout", "rel") == []
    assert ev.load("tuning", "irr") == []

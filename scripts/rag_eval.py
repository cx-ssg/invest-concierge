# -*- coding: utf-8 -*-
"""检索评测器 —— 按 `tests/golden/rag/README.md` 的指标口径打分。

指标（替代旧口径的"可分 / 不可分"）：

- `A3a 域外主动弃权`    **主结论** —— 域外查询被主动弃权的比例（`out_of_domain` 应 = 1.000）
- `over_abstain_rate`   域内可答却被判 none —— **可答查询被剥夺引用凭据的比例**（A3c 召回护栏）
- `delegated_rate`      非 none 占比 —— ⚠️ 判官从未实现 ⇒ 这是**风险面**（"可被引用的错误断言"的参数），
                        **撤档（2026-09-18）后不再是"成本指标"**；补数 `abstain_recall = 1 − delegated_rate`
- `Recall@k` / `MRR@10` 答案块是否被召回、排多前（块级标注才有）
- `trusted_recall`      ⚠️ **派生量** ≡ `Recall@k − over_abstain`，不是独立测量

（旧口径的 `weak_fp_rate` 已删 —— weak 是**委派点**，不是失败；见 README 的标注规范。
 撤下 `strong` 档后，`strong_fp_rate` / `正例 strong 率` 一并**失去对象**，已从上表移除。）

用法：
  python scripts/rag_eval.py                    # holdout（⚠️ 已用于阈值选择 = **拟合集**，非干净验收组）
  python scripts/rag_eval.py --split tuning     # 调参（会打印警告）
  python scripts/rag_eval.py --scan             # 扫阈值：none 稠密网格 + SAR_NONE 敏感性表
"""
import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.rag import store as rag_store                      # noqa: E402
from utils.rag import evidence as ev_mod                      # noqa: E402
from utils.rag.embed import embed_texts_batched               # noqa: E402
from utils.rag.evidence import EvidenceJudge, LEVEL_NONE  # noqa: E402
from utils.rag.hybrid import MAX_PER_DOC_DEFAULT, run_hybrid  # noqa: E402
from utils.rag.tokenize import tokenize                       # noqa: E402

GOLDEN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "tests", "golden", "rag")
TOP_K = 5


def load(split, key):
    path = os.path.join(GOLDEN, "queries_%s_%s.json" % (split, key))
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def assert_clean_holdout(rows_irr):
    """holdout 负例必须**全部** `batch="v2"`（= 从未参与调参）—— 给核心卖点加强制力。

    2026-09-17 独立审计 P2-2：`batch` 字段此前**无任何代码消费**，
    而「holdout 的 50 条负例全 v2」正是"干净验收组"的**全部依据**；
    没有这道门，未来把调参时看过的样本挪进 holdout，指标不会报警。
    """
    bad = [r.get("id") for r in rows_irr if r.get("batch") != "v2"]
    if bad:
        raise ValueError(
            'holdout 负例必须全部 batch="v2"（从未参与调参），以下不是：%s —— '
            "v1 样本曾用于定阈值，混入验收组会让验收失去意义" % bad)


def load_holdout():
    """holdout 的**唯一入口**：读取 + 强制校验（含拒绝空负例）。

    2026-09-18（审计 B 的 P2-1）：原先把校验只放在 `main()` 里 → 任何**绕过 CLI** 的调用方
    （直接 `load()`）都不受约束，审计实测传入 3 条 `batch=v1` 负例静默通过。
    更隐蔽的一条：`irr` 为空时断言放行 → `n_irr=0`、`by_kind={}`、`strong_fp=0.000`，
    且 `main()` 里 A3a 主结论那行因 `ood.get("n")` 为假**根本不打印** ——
    「没有负例、却看起来干净」的报告可以静默产出。故此处显式拒绝空集。
    """
    rel, irr = load("holdout", "rel"), load("holdout", "irr")
    # ⚠️ 2026-09-18 第六轮审计二 P1-2：**上一轮只堵了负例一侧 —— 正例一侧是镜像洞**。
    # `rel` 为空时 `n_rel = max(0, 1) = 1` ⇒ `Recall@5 = 0`、`trusted_recall = 0`、
    # `strong_rel_rate = 0`，**而 A3a 仍打印 `20/20 = 1.000 <<< 主结论`**，无异常、`EXIT=0`
    # —— 「没有正例、却看起来干净」的报告**仍可静默产出**，形状与上一轮修掉的那条完全一致。
    if not rel:
        raise ValueError("holdout 正例为空（queries_holdout_rel.json 缺失或为空）—— "
                         "空正例会让 Recall/trusted_recall 静默归零，而 A3a 仍显示满分")
    if not irr:
        raise ValueError("holdout 负例为空（queries_holdout_irr.json 缺失或为空）—— "
                         "不得产出「没有负例却看起来干净」的验收报告")
    assert_clean_holdout(irr)
    return rel, irr


def evaluate(rows_rel, rows_irr, judge, meta, matrix, qvecs, k=TOP_K,
             max_per_doc=MAX_PER_DOC_DEFAULT):
    """返回指标 dict。qvecs 与 rows 顺序一致（已批量 embed）。

    ⚠️ 这是**纯计算函数**：数据入口的干净性校验在 `load_holdout()`，不在这里
    （它无法区分 tuning / holdout，放进来会误伤 tuning 的 v1 负例）。

    `max_per_doc`：同文档限额，透传给两次 `run_hybrid`（裸检索 + 工具真实形态）。
    默认 = 产线默认值 `MAX_PER_DOC_DEFAULT`（2），**不改变既有行为**；
    传 `0` 或 `None` = **关闭限额**（`None` 是 `hybrid.py` 的「关闭」语义）。
    ⚠️ 2026-10-02 外部复验 R-2 补：此前无法关闭限额 ⇒ RELEASE_NOTES 表格的
    「行① 基线（旧语料、无限额 1.000 / 0.702）」**没有任何 CLI 复现路径**
    （旧语料也只能带限额跑出 0.952/0.690 —— 一个表里不存在的状态）。
    """
    mpg = None if max_per_doc in (0, None) else max_per_doc
    n_expect = len(rows_rel) + len(rows_irr)
    if len(qvecs) != n_expect:
        # ⚠️ 旧实现直接 `zip(rows, qvecs[...])` 配对 → 长度不匹配会**静默截断**，
        # 指标少算一部分却看不出来（2026-09-17 新增用例时真实踩到）。
        raise ValueError("qvecs 条数 %d 与查询总数 %d 不一致 —— 静默截断会算错指标"
                         % (len(qvecs), n_expect))
    # ⚠️ 2026-09-18 第六轮审计二 P3-1：这里原有 `chunk_pos = {m["chunk_id"]: i ...}`，
    # 但 `evaluate()` **从未使用它**（全仓仅此一处）→ 已删（死变量）。
    # 这类"看起来在岗、其实不在岗"的代码是 `load_holdout` 死代码的同族，一并清掉。

    over_abstain = 0
    recall_hits, trusted_hits, rr = 0, 0, []
    tool_recall_hits, hard_stop = 0, 0
    for r, qv in zip(rows_rel, qvecs[:len(rows_rel)]):
        ev = judge.assess(r["query"])
        # ⚠️ 2026-09-18 **撤下 `strong` 档**后不再统计「正例 strong 率」——
        # `LEVEL_STRONG` 已删、分档只出 none / weak，那个量因此**失去对象**。
        if ev.level == LEVEL_NONE:
            over_abstain += 1
            # ⚠️ 2026-09-17 **删掉了原地的 `continue`**（独立审计实测指出）：
            # 旧写法在 none 档直接跳过检索 → ① Recall/MRR 的**分子被少算**（分母仍是 n_rel）
            # ② 报告里的 `got_top5=[]` 被误读成「检索也失败了」，真相是该条 gold 排在 **rank 1**。
            # 三个量各司其职、不可互替：
            #   Recall@k       = 检索器本身能力（judge=None 的**裸检索**）
            #   trusted_recall = **判据采信过的召回**：gold 在 top-k **且** `level != none`
            #   over_abstain   = 闸门把可答查询标成 none 的比例
        order, _, _ = run_hybrid(r["query"], matrix, meta, k=max(k, 10),
                                 query_vec=qv, judge=None, max_per_doc=mpg)
        got = [meta[i]["chunk_id"] for i in order[:k]]
        gold = set(r.get("answer_chunk_ids") or [])
        if gold & set(got):
            recall_hits += 1
            if ev.level != LEVEL_NONE:
                trusted_hits += 1
        # ⚠️ 2026-09-18 第六轮审计二（**一处改动同时关掉 U10 与「评测绕过被测对象」**）：
        # 上面那次是 **`judge=None` 的裸检索**；而**工具的真实形态**是 `judge=judge`
        # （`retrieve_docs` 就是这么调的）。此前评测**从不经过它**，于是：
        #   ① **分层硬停**（`hybrid.py`：零 bigram 交集 → 物理回空）在评测里**永不触发**
        #      —— 它**不改变 `level`**（同一个 evidence 早退），而 `judge=None` 又绕开判据，
        #      所以覆盖率从 5/20 掉到 0，**没有任何指标会红**；
        #   ② 工具层的任何退化（返空 / 排序错 / `code` 过滤失效）离线指标**一个都不会动**。
        # 现在补跑一次真实形态，产出 `tool_recall`（经过被测对象的召回）
        # 与 `hard_stop_count`（硬停触发数，此前完全不可见）。
        # 当下 holdout 上 `tool_recall` **应当 == trusted_recall**（0 条正例被硬停）——
        # **一旦不等，就是真信号**。
        tool_order, _, _ = run_hybrid(r["query"], matrix, meta, k=max(k, 10),
                                      query_vec=qv, judge=judge, max_per_doc=mpg)
        if not tool_order:
            hard_stop += 1
        if gold & {meta[i]["chunk_id"] for i in tool_order[:k]}:
            tool_recall_hits += 1
        all10 = [meta[i]["chunk_id"] for i in order[:10]]
        rank = next((j + 1 for j, c in enumerate(all10) if c in gold), None)
        rr.append(1.0 / rank if rank else 0.0)
        # ⚠️ 2026-09-18 **删除了 `guarded_recall`**（两份外部审计**各自实测**证明它恒等于 `Recall@k`）：
        #   删掉 none 档硬停后，`run_hybrid` 的 `judge` **只用于算 evidence、不参与任何过滤/排序**
        #   （构造性恒等）→「带闸门再跑一次」与「裸检索」返回同一个 order；
        #   审计 B 的证伪实验：把判据换成「恒返回 none」，`guarded_recall` **纹丝不动**（仍 1.000/0.571）。
        #   另：当时两侧池大小 `kk` 在 N≤100 时相同、N=150 起才会因**池大小**而非闸门出现差异 —— 该指标在
        #   任何规模上都不成立。替代量 `trusted_recall` 会随判据退化而变红（判官恒 none → 0/n_rel）。

    # 负例：**按 kind 分列**（2026-09-17 修正口径，外部评审指出）
    # ⚠️ 2026-09-19 第七轮审计一 P2-2：下面两行是**撤档前的口径**，与 20 行之后的
    # `delegated_rate` 注释**自相矛盾**（那里说 weak 是"风险面"）。已按撤档后重写：
    # - 撤档后**没有"违规 / 合规"之分**（`strong` 已不存在）—— 负例只有两种落点：
    #   `none`（判据主动弃权）与 `weak`（判据未通过、但**带完整 `url`/`title` 返回给模型**）；
    # - `weak` **不再是"可接受的委派点"** —— 那个说法预设了"判官会兜底"，而**判官从未实现**
    #   ⇒ 它是**暴露面**：负例落 weak 的比例就是 `delegated_rate`（见下方注释）。
    by_kind = {}
    for r, qv in zip(rows_irr, qvecs[len(rows_rel):]):
        lv = judge.assess(r["query"]).level
        d = by_kind.setdefault(r.get("kind", "unknown"),
                               {"n": 0, "none": 0, "weak": 0})
        d["n"] += 1
        d[lv] += 1

    delegated = sum(d["weak"] for d in by_kind.values())

    n_rel, n_irr = max(len(rows_rel), 1), max(len(rows_irr), 1)
    return {
        "n_rel": len(rows_rel), "n_irr": len(rows_irr),
        "over_abstain_rate": over_abstain / n_rel,
        # ⚠️⚠️ 2026-09-18 **`delegated_rate` 的语义变了**（撤下 strong 档的副作用，必记）：
        # 以前它是「**委派成本**」—— 落 weak 表示"交给 LLM 判官"，是可接受的中间态
        # （理由一直是"判官会兜底"）。**但判官从未实现**（全仓 grep `llm_judge|judge_evidence` 0 命中）
        # ⇒ 现在它描述的是「**未通过判据、却仍返回给模型的结果占比**」= **风险面**，
        # 不再是成本。**读它的时候不要再按"成本"理解。**
        "delegated_rate": delegated / n_irr,
        # ⚠️ 2026-09-19 第七轮审计一 U1：**新增 `abstain_recall`** ——
        # `tests/golden/rag/README.md` 的指标表**早就定义了它**（「应弃权的查询中，
        # 实际判 `none` 的比例」）却**从未实现** ⇒ 作者想给 `delegated_rate` 设上限时，
        # 缺的那个名字**规范里其实已经有了**。二者关系：**`abstain_recall ≡ 1 − delegated_rate`**（负例口径）。
        # ⚠️ **按 kind 读，不要只看总数**：`out_of_domain` 应当 = 1.0（当前 20/20 ✓），
        # 而 `in_domain_unanswerable` / `near_miss` 是**字面判据结构上做不到**的那两类 ——
        # 对它们**只报数、不设门**（设门 = 要求判据做超出其能力的事）。
        "abstain_recall": (len(rows_irr) - delegated) / n_irr,
        "by_kind": by_kind,
        "Recall@%d" % k: recall_hits / n_rel,
        # `trusted_recall`（2026-09-18 新增，替代已删的 `guarded_recall`）：
        # **判据采信过的召回** —— gold 在 top-k **且** `level != none`。
        # 与 `Recall@k` 之差 = 「检索到了但判据没采信」的比例（这才是能随判据退化变红的量：
        # 判官恒 none → 0/n_rel；而旧 `guarded_recall` 在同样场景下纹丝不动）。
        "trusted_recall": trusted_hits / n_rel,
        # ⚠️ 2026-09-19 第七轮审计一 P3-1 / P3-2：**这段注释与代码不一致，已对齐**。
        # ① **对照物错了**：`tool_recall` 应与 **`Recall@k`** 比（**同义** —— 都是"gold 是否在 top-k"），
        #    **不是** `trusted_recall` —— 后者额外要求 `level != none`（holdout 上 1.000 vs 0.905）。
        #    正文的打印早就改对了（见下方 `[!] tool_recall != Recall@k`），**只有注释漏改** ——
        #    留着它的风险不是当下出错，而是**下一位编辑者照旧注释把打印改回去**（附录 A #9 第三次）。
        # ② **它也是派生量**：`tool_recall = Recall@k − (被分层硬停吞掉的召回)/n_rel`，
        #    与 `trusted_recall` 同构 ⇒ **不要与 `Recall@k` 并列成两个独立证据**。
        #    它的**独有价值在"路径"**：它是唯一经过被测对象（`judge=judge` + 硬停）的那条。
        "tool_recall": tool_recall_hits / n_rel,
        "hard_stop_count": hard_stop,
        # ⚠️ 2026-09-18 **撤下 strong 档**后，`strong_rel_rate` / `strong_fp_rate` 一并移除 ——
        # 它们度量的档位已不存在（分档只出 none / weak）。
        # **这不是"指标消失"**：它们要回答的问题（"负例有没有被误放行"）现在**由
        # `delegated_rate` 全权承担** —— 撤档后任何非 none 的负例都落在 weak，
        # 而 weak 一律带警示语（不再有"跳过警示"的通道）。
        # 旧值（**仅存历史，勿再引用**）：holdout `strong_fp=0.000`、正例 strong 率 0.381。
        "n_over_abstain": over_abstain,
        "MRR@10": sum(rr) / n_rel if rr else 0.0,
    }


# `SAR_NONE` 敏感性表的扫描点（2026-10-01 F1）。**含当前值 0.06**，其上界到 0.30 ——
# 再往上（0.40）**风险面已进入平台期**（tuning 0.141 / holdout 0.080，不再下降）而 oa 已 0.86~0.92
# —— 对决策无增量信息（见 --scan 的两组实测）。
SAR_NONE_SWEEP = (0.06, 0.10, 0.15, 0.20, 0.25, 0.30)


def scan(rows_rel, rows_irr, judge):
    """扫阈值，输出两张权衡表。

    **纪律**：两个池**互相留出** —— 在 tuning 上定值，用 holdout 报**一次**（反之亦然）；
    **绝不在同一个池上既选阈值又报成绩**。

    `weak_fp` 口径订正（2026-09-17）：旧版 `oa` 算的是"非 strong"（含 weak），
    与 `evaluate()` 的 `over_abstain`（只算 none）**不是同一个量** —— 两处口径必须一致。

    返回 `{"none": [...], "sar_none": [...]}`：
    - `none`：5×7 稠密网格 `(sar_none, v1_none, weak_fp, over_abstain)`
      `weak_fp` = 应弃权却**未判 none** 的比例（= 会进入生成上下文的暴露面）
      `over_abstain` = 域内可答却被判 none 的比例（召回护栏）
    - `sar_none`（**2026-10-01 F1 新增**）：把 `v1` 固定为 `V1_NONE`（实测 none 侧几乎由 SAR
      承担）后的 **6 行干净表** `(sar_none, 负例残留暴露, over_abstain)` ——
      **直接服务「要不要调高 `SAR_NONE`」**：调高会同时**压低**残留暴露、**抬高** oa
      （可答查询被剥夺引用凭据的比例）。**纪律：先定"可接受的引用丢失率"，再动 `SAR_NONE`。**
      ⚠️ 与 `none` 网格在共同点 `(sar_n, V1_NONE)` 上数值必须一致
      （交叉锁：`tests/test_rag_eval.py::test_sar_none_grid_matches_none_grid_at_v1_none`）。

    ⚠️ **历史**：此处原有一条「假想 strong 档曲线」。该档已于 2026-09-18 撤档（`7270159`），
    曲线所描述的对象**不存在** ⇒ 已删除（残留清理；计划 `docs/M1_F1_SAR_NONE_PLAN.md` §0）。
    """
    a_rel = [judge.assess(r["query"]) for r in rows_rel]
    a_irr = [judge.assess(r["query"]) for r in rows_irr]
    n_rel, n_irr = max(len(a_rel), 1), max(len(a_irr), 1)

    none_out = []
    for sar_n in (0.06, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40):
        for v1_n in (0.35, 0.45, 0.55, 0.65, 0.75):
            irr_none = sum(1 for e in a_irr if ev_mod.is_none(e.sar, e.v1, sar_n, v1_n))
            oa = sum(1 for e in a_rel if ev_mod.is_none(e.sar, e.v1, sar_n, v1_n))
            none_out.append((sar_n, v1_n, (len(a_irr) - irr_none) / n_irr, oa / n_rel))

    # ⚠️ 独立计算（**不从 `none` 网格派生**）：将来改稠密网格的取值集合时，本表不得被静默改变。
    # 两处一致性由测试交叉锁（见 docstring）。
    # ⚠️ 2026-10-01（第八轮外部审计，双路实测）：闸门改走**共享判据** `ev_mod.is_none` ——
    # 此前这里用字面量重算，与生产判据零交叉锁（表内写死 0.30 时 344 条测试全绿）。
    sar_none_out = []
    for sar_n in SAR_NONE_SWEEP:
        irr_none = sum(1 for e in a_irr if ev_mod.is_none(e.sar, e.v1, sar_n, ev_mod.V1_NONE))
        oa = sum(1 for e in a_rel if ev_mod.is_none(e.sar, e.v1, sar_n, ev_mod.V1_NONE))
        sar_none_out.append((sar_n, (len(a_irr) - irr_none) / n_irr, oa / n_rel))
    return {"none": none_out, "sar_none": sar_none_out}


def main(argv=None):
    # 2026-09-18（审计 A 的 P2-1）：本脚本原先在 **GBK 控制台**上直接崩 ——
    # 新增的 `⚠️`(U+26A0) 无法用 cp936 编码 → `UnicodeEncodeError` + 退出码 1。
    # 这与本仓「UTF-8 铁律」自相矛盾（`print` 走平台默认编码）。双保险：
    # ① 这里 reconfigure；② 打印文本改用 ASCII 标记 `[!]`。
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["tuning", "holdout"], default="holdout")
    ap.add_argument("--db", default=None)
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--max-per-doc", type=int, default=MAX_PER_DOC_DEFAULT,
                    help="同文档限额（每文档最多几块进 top-k）；0 = 关闭（复现旧行为）")
    args = ap.parse_args(argv)

    if args.split == "tuning":
        print("[eval] ⚠️ 本组已用于调参，**不得作为验收依据**（只能回答「阈值该定在哪」）")
    else:
        # ⚠️ 2026-09-18 第六轮审计二 P2-3：此处原印「验收组（未参与调参）」——
        # 但 holdout **已被用于选择 `SAR_STRONG`**（第五轮两份审计独立判定为违规）。
        # 报告 §4g 早已改口径，**横幅却还印着旧口径** —— 而横幅是使用者/审计方
        # **第一眼**看到的字符串。代码与文档的口径必须一致。
        # ⚠️ 2026-09-19 第七轮：**`strong` 档已撤下** ⇒ 原横幅末尾的"strong 档可达性"成了残句。
        # 但它**背后的污染事实仍然成立**（holdout 参与过阈值选择）⇒ 保留"拟合集"口径，
        # 只去掉已消失的对象。（**这是自查残留时发现的第 9 处，两份审计都没列。**）
        print("[eval] holdout —— ⚠️ **已用于阈值选择（拟合集）**，"
              "其上的阈值结论不得作为泛化依据")

    # ⚠️ 2026-09-18（审计二 P1-1 抓出）：**这里才是 `load_holdout()` 唯一的调用点**。
    # 我上一轮声称「`main()` 改走它」，但那个 edit 被工具拒绝后**我只补发了 reconfigure**，
    # 本行没改 → `load_holdout()` 成了**死代码**（全仓零生产调用点），
    # 而守护它的两条测试测的是**函数本身、不是调用链**，所以套件全绿、缺陷仍在。
    # 「保护从未被装上，而套件全绿」—— 这条教训比缺陷本身值钱。
    if args.split == "holdout":
        rel, irr = load_holdout()          # 读取 + assert_clean_holdout + **拒绝空集**
    else:
        rel, irr = load(args.split, "rel"), load(args.split, "irr")
    if not rel and not irr:
        print("[eval] 评测集为空 —— 先跑 scripts/rag_eval_build.py")
        return 2

    conn = rag_store.get_conn(args.db)
    meta, matrix = rag_store.load_index(conn)
    conn.close()
    if not meta or matrix is None:
        print("[eval] 索引为空 —— 先跑 scripts/rag_ingest.py")
        return 2

    judge = EvidenceJudge([tokenize(m.get("text") or "") for m in meta])
    qs = [r["query"] for r in rel] + [r["query"] for r in irr]
    qvecs = np.asarray(embed_texts_batched(qs), dtype="float32")

    m = evaluate(rel, irr, judge, meta, matrix, qvecs,
                 max_per_doc=args.max_per_doc)
    print("[eval] chunks=%d 阈值：none 档 sar<%.2f v1<%.2f（**strong 档已撤下**：非 none 一律 weak）"
          % (len(meta), ev_mod.SAR_NONE, ev_mod.V1_NONE))
    print("[eval] n_rel=%d n_irr=%d" % (m["n_rel"], m["n_irr"]))
    ood = m["by_kind"].get("out_of_domain") or {}
    if ood.get("n"):
        print("  A3a 域外主动弃权  = %d/%d = %.3f   <<< 主结论"
              % (ood["none"], ood["n"], ood["none"] / ood["n"]))
    print("  delegated_rate    = %.3f   (**非 none 占比** —— 判据未通过、但结果仍返回给模型；"
          % m["delegated_rate"])
    print("                                判官从未实现 ⇒ 这是**风险面**不是成本（2026-09-18 撤档后语义已变）)")
    print("  abstain_recall    = %.3f   (= 1 − delegated_rate；README 早已定义、**本轮才实现** ——"
          % m["abstain_recall"])
    print("                                ⚠️ **请按 kind 读**：out_of_domain 应为 1.0，另两类字面判据做不到)")
    print("  over_abstain_rate = %.3f   (%d/%d 判 none —— **闸门标注保守度**，不是召回损失："
          % (m["over_abstain_rate"], m["n_over_abstain"], m["n_rel"]))
    print("                                none 档不再无条件清空结果；零 bigram 交集仍硬停)")
    if m["over_abstain_rate"] > 0.20:
        # 2026-09-18（审计 B 的 P1-2）：修复后若干 headline 指标在「判据过度拒绝」时**全是满分**
        # （A3a 20/20、Recall 不变）—— 盲区是单向的。这里给一个下限告警。
        print("  [!] over_abstain_rate > 0.20 —— 闸门可能在过度拒绝（该方向上无其他指标会报警）")
    print("  --- 检索侧 ---")
    print("  Recall@%d          = %.3f   (judge=None **裸检索**能力 —— 旧版在 none 档 `continue`，分子被少算)"
          % (TOP_K, m["Recall@%d" % TOP_K]))
    print("  tool_recall       = %.3f   (**工具真实形态**：judge 生效 + 分层硬停 —— 评测此前从不走这条路；"
          % m["tool_recall"])
    print("                                它是**唯一经过被测对象**的检索指标)")
    if abs(m["tool_recall"] - m["Recall@%d" % TOP_K]) > 1e-9:
        # ⚠️ 对照物是 **`Recall@k`**（**同义**：都是"gold 是否在 top-k"），**不是 `trusted_recall`** ——
        # 后者额外要求 `level != none`，两者**定义不同**，差值反映的是判据弃权、不是工具退化。
        # （第六轮审计二的建议原文写的是"应当等于 trusted_recall"，此处按定义更正。）
        # 本阈值下硬停通常为 0 ⇒ 二者**应当相等**；不等即「被测对象」与「裸检索」出现真实分歧。
        print("  [!] tool_recall != Recall@k —— **被测对象与裸检索出现分歧**（对照下方硬停计数）")
    print("  硬停触发          = %d/%d   (零 bigram 交集 → 物理回空；此前在评测里**完全不可见** ——"
          % (m["hard_stop_count"], m["n_rel"]))
    print("                                覆盖率从 5/20 掉到 0 也不会有别的指标变红)")
    print("  trusted_recall    = %.3f   (gold 在 top-k **且** 判据采信 —— 判据退化时它会变红；"
          % m["trusted_recall"])
    print("                                ⚠️ **派生量**：在**当前评测集上**数值恰好等于"
          " `Recall@k − over_abstain`")
    print("                                  —— 这是**经验巧合，不是构造性恒等**：被判 none 的正例"
          "若其 gold 不在 top-k 内，两者立刻背离（第八轮外部审计实跑反例证实）")
    print("                                  —— 判据退化时它会变红（对照用例："
          "tests/test_rag_eval.py::test_trusted_recall_turns_red_when_judge_degrades）")
    print("  MRR@10            = %.3f" % m["MRR@10"])
    print("  --- 按 kind 分列（混池会互相抵消，必须分列看）---")
    for kind, d in sorted(m["by_kind"].items()):
        print("   %-26s n=%-3d none=%-3d weak=%-3d"
              % (kind, d["n"], d["none"], d["weak"]))

    if args.scan:
        curves = scan(rel, irr, judge)
        print("[eval] --- none 档扫描 (sar_none, v1_none) -> "
              "(weak_fp=未判 none 的负例比例, over_abstain) ---")
        for sar_n, v1_n, wfp, oa in curves["none"]:
            flag = "  ← 当前" if (abs(sar_n - ev_mod.SAR_NONE) < 1e-9
                                  and abs(v1_n - ev_mod.V1_NONE) < 1e-9) else ""
            print("  sar<%.2f v1<%.2f -> weak_fp=%.3f oa=%.3f%s" % (sar_n, v1_n, wfp, oa, flag))
        # ⚠️ 2026-10-01（F1）：此处原印「假想 strong 档曲线」—— 该档已于 2026-09-18 撤档，
        # 曲线描述的对象**不存在**（语义残留）⇒ 删除，位置让给**服务真实决策**的敏感性表。
        print("[eval] --- SAR_NONE 敏感性表（v1 固定 %.2f）—— 服务「要不要调高 SAR_NONE」 ---"
              % ev_mod.V1_NONE)
        print("  sar_none   负例残留暴露   oa(可答查询被剥夺引用凭据)")
        for sar_n, neg, oa in curves["sar_none"]:
            flag = "  <- 当前" if abs(sar_n - ev_mod.SAR_NONE) < 1e-9 else ""
            print("  %.2f       %.3f          %.3f%s" % (sar_n, neg, oa, flag))
        print("  -> 决策提示：先定「可接受的引用丢失率(oa)」，再动 SAR_NONE ——"
              " 两个方向都有真实代价，没有免费选项")
        print("  !  平台期：tuning 0.25 / holdout 0.15 之后风险面不再下降，继续调高只涨 oa")
    print("[eval] RESULT: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

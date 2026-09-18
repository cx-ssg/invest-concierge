# -*- coding: utf-8 -*-
"""检索评测器 —— 按 `tests/golden/rag/README.md` 的指标口径打分。

指标（替代旧口径的"可分 / 不可分"）：

- `A3a 域外主动弃权`  **主结论** —— 域外查询被主动弃权的比例（**不受 strong 档可达性影响**）
- `over_abstain_rate` 域内可答却被判 none（**召回护栏** A3c）
- `strong_fp_rate`    应弃权却判 strong —— ⚠️ **仅负例侧敏感**，且在本阈值下 holdout 上
                      **数学上不可能触发**（最大负例 SAR 0.1382 < 0.15）。它**不是**主指标
- `正例 strong 率`    strong 档**可达性**的唯一报警量（< 0.20 会打 `[!]`）
- `delegated_rate`    落 weak 的比例（**成本指标**，不是失败）
- `Recall@k` / `MRR@10` 答案块是否被召回、排多前（块级标注才有）
- `trusted_recall`    ⚠️ **派生量** ≡ `Recall@k − over_abstain`，不是独立测量

（旧口径的 `weak_fp_rate` 已删 —— weak 是**委派点**，不是失败；见 README 的标注规范。）

用法：
  python scripts/rag_eval.py                    # holdout（⚠️ 已用于选 strong 阈值 = **拟合集**）
  python scripts/rag_eval.py --split tuning     # 调参（会打印警告）
  python scripts/rag_eval.py --scan             # 扫 SAR/V1 阈值出曲线
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
from utils.rag.hybrid import run_hybrid                       # noqa: E402
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


def evaluate(rows_rel, rows_irr, judge, meta, matrix, qvecs, k=TOP_K):
    """返回指标 dict。qvecs 与 rows 顺序一致（已批量 embed）。

    ⚠️ 这是**纯计算函数**：数据入口的干净性校验在 `load_holdout()`，不在这里
    （它无法区分 tuning / holdout，放进来会误伤 tuning 的 v1 负例）。
    """
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
                                 query_vec=qv, judge=None)
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
                                      query_vec=qv, judge=judge)
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
    # - `strong` 才是违规（A3a 只看 out_of_domain）
    # - `weak` 是**委派点**（交给 LLM 判官），不是失败 —— 按 kind 的规范它本就允许 weak
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
        "by_kind": by_kind,
        "Recall@%d" % k: recall_hits / n_rel,
        # `trusted_recall`（2026-09-18 新增，替代已删的 `guarded_recall`）：
        # **判据采信过的召回** —— gold 在 top-k **且** `level != none`。
        # 与 `Recall@k` 之差 = 「检索到了但判据没采信」的比例（这才是能随判据退化变红的量：
        # 判官恒 none → 0/n_rel；而旧 `guarded_recall` 在同样场景下纹丝不动）。
        "trusted_recall": trusted_hits / n_rel,
        # ⚠️ 2026-09-18 第六轮审计一 P2：`trusted_recall` 是**派生量** ——
        # 当前数据上它恒等于 `Recall@k − over_abstain`（1.000−0.095=0.905），
        # 且只依赖 none 边界 ⇒ 对 strong 档改动**完全无反应**。不要在报告里当独立指标并列。
        # 2026-09-18 第六轮审计二：**唯一经过被测对象的检索指标** ——
        # `judge=judge` + 分层硬停，即 `retrieve_docs` 的真实调用形态。
        # 此前评测从不走这条路 ⇒ 工具层退化（返空/排序错/硬停误触发）在离线指标上不可见。
        # 当下 holdout 上它**应当 == trusted_recall**（正例 0 条被硬停）；**一旦不等就是真信号**。
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


def scan(rows_rel, rows_irr, judge):
    """扫**两组阈值**，输出权衡曲线。

    ⚠️ 2026-09-18 第六轮审计二 P2-2 / U11：此前**只扫 none 档**（`SAR_NONE`/`V1_NONE`），
    **没有 strong 档维度** —— 而 `evidence.py` 的注释却写「改前必须先重跑
    `scripts/rag_threshold_probe.py`」，那个工具**从不扫 `SAR_STRONG`**（硬编码 4+4 条老查询）
    ⇒ **是个死指针**。这正是「`SAR_STRONG=0.15` 只能在 holdout 上选」的**根因**：
    **标定工具缺失 → 被迫用验收池**。现在补上 strong 维度。

    **纪律**：两个池**互相留出** —— 在 tuning 上定值，用 holdout 报**一次**（反之亦然）；
    **绝不在同一个池上既选阈值又报成绩**。

    `weak_fp` 口径订正（2026-09-17）：旧版 `oa` 算的是"非 strong"（含 weak），
    与 `evaluate()` 的 `over_abstain`（只算 none）**不是同一个量** —— 两处口径必须一致。

    返回 `{"none": [...], "strong": [...]}`：
    - none 档 `(sar_none, v1_none, weak_fp, over_abstain)`
      `weak_fp` = 应弃权却**未判 none** 的比例（= 会进入生成上下文的暴露面）
      `over_abstain` = 域内可答却被判 none 的比例（召回护栏）
    - strong 档 `(sar_strong, 正例 strong 率, 负例 strong 数, 负例 strong 率)`
      ⚠️ 判据与生产一致：**`v1 > 0`**（第六轮审计一 P1 起）—— 不是 `feature` 非空。
    """
    a_rel = [judge.assess(r["query"]) for r in rows_rel]
    a_irr = [judge.assess(r["query"]) for r in rows_irr]
    n_rel, n_irr = max(len(a_rel), 1), max(len(a_irr), 1)

    none_out = []
    for sar_n in (0.06, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40):
        for v1_n in (0.35, 0.45, 0.55, 0.65, 0.75):
            irr_none = sum(1 for e in a_irr if e.sar < sar_n and e.v1 < v1_n)
            oa = sum(1 for e in a_rel if e.sar < sar_n and e.v1 < v1_n)
            none_out.append((sar_n, v1_n, (len(a_irr) - irr_none) / n_irr, oa / n_rel))

    strong_out = []
    for sar_s in (0.10, 0.12, 0.14, 0.15, 0.16, 0.18, 0.20, 0.25):
        rel_s = sum(1 for e in a_rel if e.v1 > 0 and e.sar >= sar_s)
        irr_s = sum(1 for e in a_irr if e.v1 > 0 and e.sar >= sar_s)
        strong_out.append((sar_s, rel_s / n_rel, irr_s, irr_s / n_irr))
    return {"none": none_out, "strong": strong_out}


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
    args = ap.parse_args(argv)

    if args.split == "tuning":
        print("[eval] ⚠️ 本组已用于调参，**不得作为验收依据**（只能回答「阈值该定在哪」）")
    else:
        # ⚠️ 2026-09-18 第六轮审计二 P2-3：此处原印「验收组（未参与调参）」——
        # 但 holdout **已被用于选择 `SAR_STRONG`**（第五轮两份审计独立判定为违规）。
        # 报告 §4g 早已改口径，**横幅却还印着旧口径** —— 而横幅是使用者/审计方
        # **第一眼**看到的字符串。代码与文档的口径必须一致。
        print("[eval] holdout —— ⚠️ 已用于选择 strong 阈值（**拟合集**），"
              "strong 档可达性不得作为验收结论")

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

    m = evaluate(rel, irr, judge, meta, matrix, qvecs)
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
    print("                                ⚠️ **派生量**：当前恒等于 `Recall@k − over_abstain`，"
          "对 strong 档改动完全无反应，勿当独立指标并列)")
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
        # ⚠️ 2026-09-18 **撤下 `strong` 档**后，这条曲线**不再用于定阈值**（已经没有对象）。
        # 保留它只为**历史对照**与「将来若恢复三档时的参考」——
        # **它不代表系统当前存在 `strong` 档**（当前分档只有 none / weak）。
        print("[eval] --- （仅历史参考，勿用于定阈值）假想 strong 档曲线 —— 判据同 v1>0 ---")
        for sar_s, rel_rate, irr_n, irr_rate in curves["strong"]:
            print("  sar>=%.2f -> 假想正例 %.3f   假想负例 %d 条 (%.3f)"
                  % (sar_s, rel_rate, irr_n, irr_rate))
    print("[eval] RESULT: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""检索评测器 —— 按 `tests/golden/rag/README.md` 的指标口径打分。

指标（替代旧口径的"可分 / 不可分"）：
- `over_abstain_rate` 域内可答却被判 none（**召回护栏** A3c）
- `strong_fp_rate`     应弃权却判 strong（**A3a 主指标**）
- `weak_fp_rate`       应弃权判 weak（可接受但不理想：会进生成上下文并标注证据不足）
- `Recall@k` / `MRR@10` 答案块是否被召回、排多前（块级标注才有）

用法：
  python scripts/rag_eval.py                    # 验收（默认 holdout）
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
from utils.rag.evidence import EvidenceJudge, LEVEL_NONE, LEVEL_STRONG  # noqa: E402
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


def evaluate(rows_rel, rows_irr, judge, meta, matrix, qvecs, k=TOP_K):
    """返回指标 dict。qvecs 与 rows 顺序一致（已批量 embed）。"""
    n_expect = len(rows_rel) + len(rows_irr)
    if len(qvecs) != n_expect:
        # ⚠️ 旧实现直接 `zip(rows, qvecs[...])` 配对 → 长度不匹配会**静默截断**，
        # 指标少算一部分却看不出来（2026-09-17 新增用例时真实踩到）。
        raise ValueError("qvecs 条数 %d 与查询总数 %d 不一致 —— 静默截断会算错指标"
                         % (len(qvecs), n_expect))
    chunk_pos = {m["chunk_id"]: i for i, m in enumerate(meta)}

    over_abstain = 0
    recall_hits, rr = 0, []
    for r, qv in zip(rows_rel, qvecs[:len(rows_rel)]):
        ev = judge.assess(r["query"])
        if ev.level == LEVEL_NONE:
            over_abstain += 1
            continue                                     # 弃权 → 不可能召回
        order, _, _ = run_hybrid(r["query"], matrix, meta, k=max(k, 10),
                                 query_vec=qv, judge=None)
        got = [meta[i]["chunk_id"] for i in order[:k]]
        gold = set(r.get("answer_chunk_ids") or [])
        if gold & set(got):
            recall_hits += 1
        all10 = [meta[i]["chunk_id"] for i in order[:10]]
        rank = next((j + 1 for j, c in enumerate(all10) if c in gold), None)
        rr.append(1.0 / rank if rank else 0.0)

    # 负例：**按 kind 分列**（2026-09-17 修正口径，外部评审指出）
    # - `strong` 才是违规（A3a 只看 out_of_domain）
    # - `weak` 是**委派点**（交给 LLM 判官），不是失败 —— 按 kind 的规范它本就允许 weak
    by_kind = {}
    for r, qv in zip(rows_irr, qvecs[len(rows_rel):]):
        lv = judge.assess(r["query"]).level
        d = by_kind.setdefault(r.get("kind", "unknown"),
                               {"n": 0, "none": 0, "weak": 0, "strong": 0})
        d["n"] += 1
        d[lv] += 1

    strong_fp = sum(d["strong"] for d in by_kind.values())
    delegated = sum(d["weak"] for d in by_kind.values())

    n_rel, n_irr = max(len(rows_rel), 1), max(len(rows_irr), 1)
    return {
        "n_rel": len(rows_rel), "n_irr": len(rows_irr),
        "over_abstain_rate": over_abstain / n_rel,
        "strong_fp_rate": strong_fp / n_irr,
        # 委派率：落 weak 的比例 = **成本指标**（多一次判官调用），**不是失败**。
        # 旧名 `weak_fp_rate` 与标注规范矛盾（规范里三类负例都允许 weak）。
        "delegated_rate": delegated / n_irr,
        "by_kind": by_kind,
        "Recall@%d" % k: recall_hits / n_rel,
        "MRR@10": sum(rr) / n_rel if rr else 0.0,
    }


def scan(rows_rel, rows_irr, judge):
    """扫 **none 档**阈值（`SAR_NONE` / `V1_NONE`），输出权衡曲线。

    为什么扫 none 档而不是 strong 档：strong 误放行已经很低
    （验收组按 kind 分列全为 0：ood 0/20、unans 0/15、near_miss 0/15），
    真正的暴露面在 **weak 档** —— 它仍会把结果送进生成上下文。
    ⚠️ 2026-09-17 订正：本行原写"holdout 首跑 2.8%"，那是 §4b② 已判定为**口径错误的混池数字**。
    提高 none 门槛能把 weak 压向 none，代价是 `over_abstain`（误杀相关查询）上升。

    ⚠️ 口径修正（2026-09-17）：旧版 `oa` 算的是"非 strong"（含 weak），
    与 `evaluate()` 的 `over_abstain`（只算 none）不是同一个量 —— 两处口径必须一致。

    返回 `[(sar_none, v1_none, weak_fp, over_abstain), ...]`：
    - `weak_fp` = 应弃权却**未判 none** 的比例（= 会进入生成上下文的暴露面）
    - `over_abstain` = 域内可答却被判 none 的比例（召回护栏）
    """
    a_rel = [judge.assess(r["query"]) for r in rows_rel]
    a_irr = [judge.assess(r["query"]) for r in rows_irr]
    n_rel, n_irr = max(len(a_rel), 1), max(len(a_irr), 1)
    out = []
    for sar_n in (0.06, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40):
        for v1_n in (0.35, 0.45, 0.55, 0.65, 0.75):
            irr_none = sum(1 for e in a_irr if e.sar < sar_n and e.v1 < v1_n)
            oa = sum(1 for e in a_rel if e.sar < sar_n and e.v1 < v1_n)
            out.append((sar_n, v1_n, (len(a_irr) - irr_none) / n_irr, oa / n_rel))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["tuning", "holdout"], default="holdout")
    ap.add_argument("--db", default=None)
    ap.add_argument("--scan", action="store_true")
    args = ap.parse_args(argv)

    if args.split == "tuning":
        print("[eval] ⚠️ 本组已用于调参，**不得作为验收依据**（只能回答「阈值该定在哪」）")
    else:
        print("[eval] 验收组（未参与调参）")

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
    print("[eval] chunks=%d 阈值 sar>=%.2f v1>=%.2f / none 档 sar<%.2f v1<%.2f"
          % (len(meta), ev_mod.SAR_STRONG, ev_mod.V1_STRONG, ev_mod.SAR_NONE, ev_mod.V1_NONE))
    print("[eval] n_rel=%d n_irr=%d" % (m["n_rel"], m["n_irr"]))
    print("  over_abstain_rate = %.3f   (域内可答被判 none；越低越好)  ← A3c 召回护栏"
          % m["over_abstain_rate"])
    print("  strong_fp_rate    = %.3f   (应弃权却 strong；越低越好)  ← A3a 主指标"
          % m["strong_fp_rate"])
    print("  delegated_rate    = %.3f   (落 weak = 需 LLM 判官；**成本指标，不是失败**)"
          % m["delegated_rate"])
    print("  Recall@%d          = %.3f" % (TOP_K, m["Recall@%d" % TOP_K]))
    print("  MRR@10            = %.3f" % m["MRR@10"])
    print("  --- 按 kind 分列（混池会互相抵消，必须分列看）---")
    for kind, d in sorted(m["by_kind"].items()):
        print("   %-26s n=%-3d none=%-3d weak=%-3d strong=%-3d" %
              (kind, d["n"], d["none"], d["weak"], d["strong"]))

    if args.scan:
        print("[eval] --- none 档扫描 (sar_none, v1_none) -> "
              "(weak_fp=未判 none 的负例比例, over_abstain) ---")
        for sar_n, v1_n, wfp, oa in scan(rel, irr, judge):
            flag = "  ← 当前" if (abs(sar_n - ev_mod.SAR_NONE) < 1e-9
                                  and abs(v1_n - ev_mod.V1_NONE) < 1e-9) else ""
            print("  sar<%.2f v1<%.2f -> weak_fp=%.3f oa=%.3f%s" % (sar_n, v1_n, wfp, oa, flag))
    print("[eval] RESULT: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

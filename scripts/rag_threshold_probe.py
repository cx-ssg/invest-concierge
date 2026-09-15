# -*- coding: utf-8 -*-
"""真实语料相似度分布探针 —— 为 min_sim / min_sim_ratio 定值提供实测依据。

为什么需要它：`MIN_SIM=0.45` / `MIN_SIM_RATIO=0.85` 初版只基于 **5 篇样例**定值
（见 docs/M1_KERNEL_SPEC.md §9「阈值样本量小」）。接入真实公告语料后**必须重测**，
否则就是在用臆测的阈值跑生产。

用法（须先跑过 rag_ingest，kb.db 里有向量）：
  python scripts/rag_threshold_probe.py
  python scripts/rag_threshold_probe.py --db D:/path/to/kb.db

判读方式：
- 无关查询的 `max_sim` 应 **低于** MIN_SIM（否则 A3「无关查询返回 0 条」失效）
- 相关查询的 `max_sim` 应显著高于该阈值；其「入选块数」用于校准 MIN_SIM_RATIO

⚠️ **样本内 vs 留出（critic 独立审计 2026-09-16 F1 的应对）**：
用「定阈值时用过的那批查询」去做验收 = **循环论证**，不提供任何泛化证据。
- 默认组（TUNING）= 定值时用过 → 只能回答「阈值可以定在哪」
- `--holdout` 组 = **从未参与定值** → 才回答「换一批查询还成立吗」
定完阈值后**必须跑 --holdout 复核**；两组结论不一致时以留出组为准。
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.rag import store as rag_store             # noqa: E402
from utils.rag.embed import embed_texts_batched      # noqa: E402
from utils.rag.hybrid import MIN_SIM, MIN_SIM_RATIO, _cosine_scores  # noqa: E402

# ① 定值组（2026-09-16 据此把 MIN_SIM 定为 0.62）
RELEVANT_QUERIES = [
    "茅台上半年营业收入增长多少",
    "利润分配方案是什么",
    "董事会决议公告的内容",
    "业绩说明会什么时候召开",
]
IRRELEVANT_QUERIES = [
    "量子计算最新进展",
    "python 异步编程入门",
    "今天天气怎么样",
    "如何学习滑雪",
]

# ② 留出组（held-out）：**从未参与定值**，用于回答 F1 的循环论证问题
HOLDOUT_RELEVANT = [
    "会计政策变更对利润的影响",
    "风险评估报告的结论是什么",
    "股东会审议通过了哪些议案",
    "高级管理人员是否发生变动",
]
HOLDOUT_IRRELEVANT = [
    "如何训练一个大语言模型",
    "世界杯决赛比分是多少",
    "北京到上海的航班时刻",
    "怎么写一封求职信",
]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--holdout", action="store_true",
                    help="用从未参与定值的留出查询组（泛化验证，回应 critic F1）")
    args = ap.parse_args(argv)

    conn = rag_store.get_conn(args.db)
    meta, matrix = rag_store.load_index(conn)
    conn.close()

    if matrix is None or len(meta) == 0:
        print("[probe] 索引为空或无向量 —— 先跑 scripts/rag_ingest.py")
        print("[probe] RESULT: NO_INDEX")
        return 2

    print("[probe] chunks=%d dim=%d  当前阈值 MIN_SIM=%.2f RATIO=%.2f"
          % (len(meta), matrix.shape[1], MIN_SIM, MIN_SIM_RATIO))

    rel_q = HOLDOUT_RELEVANT if args.holdout else RELEVANT_QUERIES
    irr_q = HOLDOUT_IRRELEVANT if args.holdout else IRRELEVANT_QUERIES
    print("[probe] 查询组 = %s" % ("HOLDOUT（未参与定值）" if args.holdout else "TUNING（参与定值）"))
    qs = [("REL", q) for q in rel_q] + [("IRR", q) for q in irr_q]
    qv = np.asarray(embed_texts_batched([q for _, q in qs]), dtype="float32")

    rel_max, irr_max = [], []
    for i, (kind, q) in enumerate(qs):
        sims = _cosine_scores(qv[i], matrix)
        mx = float(np.max(sims))
        cutoff = mx * MIN_SIM_RATIO
        kept = int(np.sum(sims > cutoff))
        above = int(np.sum(sims >= MIN_SIM))
        (rel_max if kind == "REL" else irr_max).append(mx)
        print("[%s] max_sim=%.4f  ratio_cutoff=%.4f  入选块数=%d  超绝对阈值的块数=%d  q=%s"
              % (kind, mx, cutoff, kept, above, q))
        top = int(np.argmax(sims))
        print("      top1: %s" % (meta[top]["text"] or "")[:60].replace("\n", " "))

    print("--- 汇总 ---")
    print("REL max_sim: min=%.4f max=%.4f   (期望显著 > MIN_SIM)"
          % (min(rel_max), max(rel_max)))
    print("IRR max_sim: min=%.4f max=%.4f   (期望 < MIN_SIM，否则 A3 失效)"
          % (min(irr_max), max(irr_max)))

    gap_ok = min(rel_max) > max(irr_max)
    thr_ok = max(irr_max) < MIN_SIM < min(rel_max)
    print("判定：两类分布可分=%s ；当前 MIN_SIM=%.2f 满足 IRR<MIN_SIM<REL=%s"
          % (gap_ok, MIN_SIM, thr_ok))
    if not thr_ok:
        print("建议：MIN_SIM 应取 (max(IRR), min(REL)) 区间内，即 %.3f ~ %.3f"
              % (max(irr_max), min(rel_max)))
    print("[probe] RESULT: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

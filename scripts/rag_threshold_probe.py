# -*- coding: utf-8 -*-
"""证据判据探针 —— 为 SAR / V1 阈值提供实测依据，并做 TUNING / HOLDOUT 双组泛化验证。

**2026-09-16 换代**：旧版探针对应的是 `max_sim` **绝对阈值**方案 —— 该方案已被实测证伪
（HOLDOUT 组区间倒挂：无关 0.6902 > 相关 0.6668，见 `docs/M1_INGEST_AUDIT.md`）。
现判据为 `utils/rag/evidence.py` 的 `EvidenceJudge`（SAR + V1，均为尺度无关量）。

用法（须先跑过 `rag_ingest.py`，kb.db 里有语料）：
  python scripts/rag_threshold_probe.py              # TUNING 组（定阈值时用的那批）
  python scripts/rag_threshold_probe.py --holdout    # HOLDOUT 组（**从未参与定值，必跑**）

判读规则：
- **REL 不得被判 `none`** —— 误弃权 = 该给的不给（A3c 召回护栏）
- **IRR 不得被判 `strong`** —— 误放行 = 不该给却给了（A3a）
- 两组结论不一致时**以留出组为准**（用定值组自证 = 循环论证，critic 审计 F1）
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.rag import store as rag_store                              # noqa: E402
from utils.rag.evidence import EvidenceJudge, LEVEL_NONE, SAR_NONE, V1_NONE  # noqa: E402
from utils.rag.tokenize import tokenize                               # noqa: E402

# ① 定值组（参与过阈值调试）
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

# ② 留出组（held-out）：**从未参与定值** —— 才回答"换一批查询还成立吗"
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
    meta, _matrix = rag_store.load_index(conn)
    conn.close()
    if not meta:
        print("[probe] 索引为空 —— 先跑 scripts/rag_ingest.py")
        print("[probe] RESULT: NO_INDEX")
        return 2

    judge = EvidenceJudge([tokenize(m.get("text") or "") for m in meta])
    print("[probe] chunks=%d  阈值 SAR_STRONG=%.2f V1_STRONG=%.2f SAR_NONE=%.2f V1_NONE=%.2f"
          % (len(meta), SAR_STRONG, V1_STRONG, SAR_NONE, V1_NONE))

    rel_q = HOLDOUT_RELEVANT if args.holdout else RELEVANT_QUERIES
    irr_q = HOLDOUT_IRRELEVANT if args.holdout else IRRELEVANT_QUERIES
    print("[probe] 查询组 = %s" % ("HOLDOUT（未参与定值）" if args.holdout
                                   else "TUNING（参与定值，不得用于验收）"))

    rows = []
    for kind, q in [("REL", q) for q in rel_q] + [("IRR", q) for q in irr_q]:
        ev = judge.assess(q)
        rows.append((kind, q, ev))
        print("[%s] sar=%.4f  v1=%.3f  level=%-6s  q=%s"
              % (kind, ev.sar, ev.v1, ev.level, q))

    rel = [r for r in rows if r[0] == "REL"]
    irr = [r for r in rows if r[0] == "IRR"]
    rel_none = [r for r in rel if r[2].level == LEVEL_NONE]
    irr_strong = [r for r in irr if r[2].level == LEVEL_STRONG]

    print("--- 汇总 ---")
    print("REL sar: %.4f~%.4f  v1: %.3f~%.3f"
          % (min(r[2].sar for r in rel), max(r[2].sar for r in rel),
             min(r[2].v1 for r in rel), max(r[2].v1 for r in rel)))
    print("IRR sar: %.4f~%.4f  v1: %.3f~%.3f"
          % (min(r[2].sar for r in irr), max(r[2].sar for r in irr),
             min(r[2].v1 for r in irr), max(r[2].v1 for r in irr)))

    ok = (not rel_none) and (not irr_strong)
    print("判定：可分=%s（REL 误弃权 %d/4 ；IRR 误放行 %d/4）"
          % (ok, len(rel_none), len(irr_strong)))
    if rel_none:
        print("  ⚠️ 误弃权（该给的不给）: %s" % [r[1] for r in rel_none])
    if irr_strong:
        print("  ⚠️ 误放行（不该给却给）: %s" % [r[1] for r in irr_strong])
    print("[probe] RESULT: %s" % ("OK" if ok else "REVISIONS_NEEDED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

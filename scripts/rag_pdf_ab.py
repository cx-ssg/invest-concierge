# -*- coding: utf-8 -*-
"""双轨语料对照评测（PDF 全文 vs API 正文）—— 量化「补全文能多答对多少题」。

计划：`docs/M1_PDF_AB_PLAN.md` §3。设计要点：

1. **问题集预注册**（硬编码在本文件里，跑之前就固定）——只取「答案确定落在 5000 字截断之后」
   的**报表细节级**事实点（合并/母公司资产负债表、合并利润表、合并现金流量表）。
   ⚠️ 2026-10-01 实测教训：**摘要级**指标（营收/净利润/每股收益/加权 ROE）**API 语料里就有**
   （它们恰在前 5000 字内），拿它们做对照会得出"补全文没用"的**错误结论** —— 已剔除。
2. **判定**：top-5 块的**数字集合**里是否出现答案数字（两套语料 `chunk_id` 不可对齐）。
3. **阴性对照**：把答案换成不存在的数字 → 必须 0 命中（否则说明判定机制虚高）。
4. **自检**：跑前检查每条答案是否**真的**不在 API 语料里；若在，打印告警并标注（不静默剔除）。

用法：
  python scripts/rag_pdf_ab.py --api-db kb.db --pdf-db kb_pdf.db --k 5
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                    # noqa: E402

from utils.rag import store as rag_store              # noqa: E402
from utils.rag.embed import embed_texts_batched       # noqa: E402
from utils.rag.evidence import EvidenceJudge          # noqa: E402
from utils.rag.hybrid import run_hybrid               # noqa: E402
from utils.rag.tokenize import tokenize               # noqa: E402
from scripts.rag_ingest_pdf import find_numbers       # noqa: E402

# ⚠️ 预注册问题集（跑之前固定；答案数字由人工从 PDF 块原文逐条核定）
CASES = [
    ("2026年6月末合并资产负债表的货币资金是多少？", "53518798979.08", "合并资产负债表"),
    ("2026年6月末拆出资金是多少？", "141084158124.01", "合并资产负债表"),
    ("2026年6月末母公司应收账款是多少？", "9846293678.36", "母公司资产负债表"),
    ("2026上半年合并利润表的营业总收入是多少？", "92278072083.21", "合并利润表"),
    ("2026上半年营业总成本是多少？", "30946044878.08", "合并利润表"),
    ("2026上半年营业成本是多少？", "9473762565.88", "合并利润表"),
    ("2026上半年综合收益总额是多少？", "17062381326.67", "合并利润表"),
    ("2026上半年销售商品、提供劳务收到的现金是多少？", "98421697395.39", "合并现金流量表"),
    ("2026年6月末合并资产负债表的存货是多少？", "61317208371.30", "合并资产负债表"),
    ("2026年6月末合并资产负债表流动资产合计是多少？", "260724668103.40", "合并资产负债表"),
]
FAKE_ANSWER = "9876543210.12"          # 阴性对照：任何语料里都不该有


def load_bundle(db):
    """返回 `(meta, matrix, judge)`；`judge` 用**该库自己的语料**构建（判据基准随语料走）。"""
    conn = rag_store.get_conn(db)
    meta, matrix = rag_store.load_index(conn)
    conn.close()
    judge = EvidenceJudge([tokenize(m.get("text") or "") for m in meta])
    return meta, matrix, judge


def hit_of(order, meta, answer, k):
    """top-k 块的数字集合里是否含 `answer`。"""
    texts = [meta[i].get("text") or "" for i in order[:k]]
    nums = set()
    for t in texts:
        nums |= find_numbers(t)
    return answer in nums


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--api-db", default="kb.db")
    ap.add_argument("--pdf-db", default="kb_pdf.db")
    ap.add_argument("--k", type=int, default=5)
    args = ap.parse_args(argv)

    api = load_bundle(args.api_db)
    pdf = load_bundle(args.pdf_db)
    print("[ab] API 库 %d 块；PDF 库 %d 块；k=%d；问题集 %d 条（预注册）"
          % (len(api[0]), len(pdf[0]), args.k, len(CASES)))

    # ---- 自检：答案是否真的不在 API 语料里 ----
    api_text = "\n".join(m.get("text") or "" for m in api[0])
    api_nums = find_numbers(api_text)
    leaks = [c for c in CASES if c[1] in api_nums]
    print("[ab] 自检：%d/%d 条的答案**出现在** API 语料里%s"
          % (len(leaks), len(CASES), "（这些条测不出截断损失，已标注 [!]）" if leaks else " ✅"))
    for q, ans, src in leaks:
        print("      [!] %s（%s = %s）" % (q, src, ans))

    # ---- 查询向量（两库共用：向量只取决于文本） ----
    qs = [c[0] for c in CASES]
    qvecs = np.asarray(embed_texts_batched(qs), dtype="float32")

    rows = []
    for (q, ans, src), qv in zip(CASES, qvecs):
        api_level = api[2].assess(q).level
        pdf_level = pdf[2].assess(q).level
        o_api, _, _ = run_hybrid(q, api[1], api[0], k=args.k, query_vec=qv, judge=None)
        o_pdf, _, _ = run_hybrid(q, pdf[1], pdf[0], k=args.k, query_vec=qv, judge=None)
        rows.append((q, ans, src, hit_of(o_api, api[0], ans, args.k),
                     hit_of(o_pdf, pdf[0], ans, args.k), api_level, pdf_level))

    print("\n %-46s %-18s %-9s %-8s %-9s %-8s" %
          ("问题", "答案数字", "API命中", "API档", "PDF命中", "PDF档"))
    for q, ans, src, ha, hp, la, lp in rows:
        mark = "[!]" if ans in api_nums else "   "
        print(" %s%-42s %-18s %-9s %-8s %-9s %-8s" %
              (mark, q[:40], ans, "是" if ha else "否", la, "是" if hp else "否", lp))

    n = len(rows)
    api_hits = sum(1 for r in rows if r[3])
    pdf_hits = sum(1 for r in rows if r[4])

    # ---- 阴性对照 ----
    fake = []
    for (q, _, _), qv in zip(CASES, qvecs):
        o_api, _, _ = run_hybrid(q, api[1], api[0], k=args.k, query_vec=qv, judge=None)
        o_pdf, _, _ = run_hybrid(q, pdf[1], pdf[0], k=args.k, query_vec=qv, judge=None)
        fake.append(hit_of(o_api, api[0], FAKE_ANSWER, args.k)
                    or hit_of(o_pdf, pdf[0], FAKE_ANSWER, args.k))

    print("\n[ab] === 汇总 ===")
    print("   hit@%-2d  API 语料（5000 字截断） = %d/%d" % (args.k, api_hits, n))
    print("   hit@%-2d  PDF 全文语料            = %d/%d" % (args.k, pdf_hits, n))
    print("   阴性对照（假答案 %s）        = %d/%d  %s"
          % (FAKE_ANSWER, sum(fake), n, "✅ 判定机制有效" if not any(fake) else "❌ 判定虚高"))
    print("   被弃权（level=none）：API %d/%d，PDF %d/%d"
          % (sum(1 for r in rows if r[5] == "none"), n,
             sum(1 for r in rows if r[6] == "none"), n))
    print("[ab] RESULT: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

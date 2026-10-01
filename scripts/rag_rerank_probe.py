# -*- coding: utf-8 -*-
"""LLM rerank 探针：对粗筛 top-k 逐对判断「这段能否回答该问题」，重排后报 MRR。

**为什么需要**（2026-10-02 实测）：语料切到 PDF 全文（75 → 268 块）后，
`Recall@5` 靠**同文档限额**修回 0.952，但 `MRR@10` 停在 0.605（迁移前基线 0.702）——
零依赖杠杆已扫尽（`max_per_doc` 扫过、RRF 双路权重扫过，收益不成比例）。
本探针验证"精读式重排"能补多少：**实测 holdout MRR 0.605 → 0.706**（5 题改善、0 题恶化）。

⚠️ **两个实测坑（都踩过）**：
1. `deepseek-v4-flash` **默认开思考**，思考会**把 `max_tokens` 吃光** → `content` 为空串
   （首轮 105 个候选里 **61%** 拿不到判定）。修：显式关思考
   （`extra_body={"thinking": {"type": "disabled"}}`，项目 `ai_helper.py:117` 是同一姿势的 enabled 版）
   **并**给足 `max_tokens` 兜底。
2. 重排**不改变结果集合** ⇒ `Recall@5` 不会变，只有 `MRR` 会动 —— 别把"Recall 没涨"当失败。

⚠️ **这是离线探针，不是产线**：`retrieve_docs` 目前**没有** rerank。
接入前必须先解决**成本/延迟**（每查询 +k 次 LLM 调用；**2026-10-02 实测 21 题全流程 51.8~60s ≈ 2.5~2.9s/题，含检索**；早期估算为每题 +5~10s）。

用法：
  python scripts/rag_rerank_probe.py --split holdout            # 跑 holdout 21 条正例
  python scripts/rag_rerank_probe.py --split tuning --k 5 --candidates 5
  python scripts/rag_rerank_probe.py --no-rerank                # 只报基线 MRR（不调模型）
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                            # noqa: E402

from utils.rag import store as rag_store                      # noqa: E402
from utils.rag.embed import embed_texts_batched               # noqa: E402
from utils.rag.hybrid import run_hybrid                       # noqa: E402

GOLDEN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "tests", "golden", "rag")
PROMPT = (
    "你是检索结果审核员。判断下面这段公告文字**能否回答**用户的问题。\n\n"
    "用户问题：{q}\n\n候选文字：\n{chunk}\n\n"
    "只输出一个字符：能回答输出 Y，不能回答 N。不要任何解释。"
)


def make_judge(model, base_url, api_key):
    """返回 `judge_pair(q, chunk) -> True/False/None`（None = 未判定）。"""
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=base_url)

    def judge_pair(q, chunk):
        kwargs = dict(model=model, temperature=0, max_tokens=512,
                      messages=[{"role": "user",
                                 "content": PROMPT.format(q=q, chunk=(chunk or "")[:1200])}])
        for attempt in range(3):
            try:
                if attempt == 0:
                    r = client.chat.completions.create(
                        extra_body={"thinking": {"type": "disabled"}}, **kwargs)
                else:                       # 网关不认 disabled → 只靠 max_tokens 兜底
                    r = client.chat.completions.create(**kwargs)
                txt = (r.choices[0].message.content or "").strip().upper()
                if not txt:
                    time.sleep(0.8)
                    continue
                return txt.startswith("Y")
            except Exception as e:
                print("      [!] 调用失败(%s): %s" % (type(e).__name__, str(e)[:80]))
                time.sleep(1)
        return None
    return judge_pair


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["tuning", "holdout"], default="holdout")
    ap.add_argument("--db", default="kb.db")
    ap.add_argument("--k", type=int, default=5, help="粗筛取几个候选")
    ap.add_argument("--candidates", type=int, default=None, help="送判定的候选数（默认 = --k）")
    ap.add_argument("--max-per-doc", type=int, default=2, help="同文档限额（0 = 关闭）")
    ap.add_argument("--no-rerank", action="store_true", help="只报基线，不调模型")
    ap.add_argument("--out", default=None, help="逐题明细 JSON 路径")
    args = ap.parse_args(argv)
    cands = args.candidates or args.k
    mpg = args.max_per_doc or None

    rows = json.load(open(os.path.join(GOLDEN, "queries_%s_rel.json" % args.split),
                          encoding="utf-8"))
    conn = rag_store.get_conn(args.db)
    meta, matrix = rag_store.load_index(conn)
    conn.close()
    print("[rerank] %s：正例 %d 条；语料 %d 块；k=%d 候选=%d 限额=%s"
          % (args.split, len(rows), len(meta), args.k, cands, mpg))

    judge = None
    if not args.no_rerank:
        import config
        key = config.get_api_key()
        if not key:
            print("[rerank] 没有可用的 DEEPSEEK_API_KEY（.env / local_env.bat）—— 只能报基线")
            args.no_rerank = True
        else:
            print("[rerank] 模型 = %s（%s，关思考）" % (config.DEEPSEEK_MODEL, config.DEEPSEEK_API_BASE))
            judge = make_judge(config.DEEPSEEK_MODEL, config.DEEPSEEK_API_BASE, key)

    base_rr, new_rr, details = [], [], []
    for i, r in enumerate(rows, 1):
        q = r["query"]
        gold = set(r.get("answer_chunk_ids") or [])
        qv = np.asarray(embed_texts_batched([q]), dtype="float32")[0]
        order, _, _ = run_hybrid(q, matrix, meta, k=max(args.k, cands), query_vec=qv,
                                 judge=None, max_per_doc=mpg)
        base_ids = [meta[j]["chunk_id"] for j in order[:args.k]]

        def rank_of(ids):
            return next((t + 1 for t, c in enumerate(ids) if c in gold), None)

        b = rank_of(base_ids)
        base_rr.append(1.0 / b if b else 0.0)
        verdicts, new_ids = [], base_ids
        if judge is not None:
            verdicts = [judge(q, meta[j].get("text")) for j in order[:cands]]
            yes = [j for j, v in zip(order[:cands], verdicts) if v is True]
            no = [j for j, v in zip(order[:cands], verdicts) if v is not True]
            ordered = yes + no + list(order[cands:])          # 其余保持原顺序接在后面
            new_ids = [meta[j]["chunk_id"] for j in ordered[:args.k]]
        n = rank_of(new_ids)
        new_rr.append(1.0 / n if n else 0.0)
        details.append({"id": r.get("id"), "q": q, "gold": sorted(gold),
                        "base_rank": b, "new_rank": n,
                        "verdicts": ["Y" if v else ("N" if v is False else "?") for v in verdicts]})
        print("%2d/%d %-9s base=%-5s new=%-5s %s%s"
              % (i, len(rows), r.get("id"), b, n, "".join(details[-1]["verdicts"]),
                 "" if b == n else "   <<< 变化"), flush=True)

    print("\n[rerank] === 汇总（%s，%d 条正例）===" % (args.split, len(rows)))
    print("  不 rerank      MRR@10 = %.3f" % (sum(base_rr) / len(base_rr)))
    if judge is not None:
        unknown = sum(1 for d in details for v in d["verdicts"] if v == "?")
        total = sum(len(d["verdicts"]) for d in details)
        print("  DS rerank     MRR@10 = %.3f" % (sum(new_rr) / len(new_rr)))
        print("  未判定比例 = %d/%d（应接近 0；>20%% 说明模型输出被吃光）"
              % (unknown, total))
    print("  （提示：重排不改变结果集合 ⇒ Recall@5 不变，只有 MRR 会动）")
    if args.out:
        json.dump(details, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("[rerank] 明细 → %s" % args.out)
    print("[rerank] RESULT: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

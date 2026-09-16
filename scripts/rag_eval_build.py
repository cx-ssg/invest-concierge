# -*- coding: utf-8 -*-
"""评测集构建器 —— 按 `tests/golden/rag/README.md` 生成四类查询并分流 TUNING/HOLDOUT。

三个设计要点（均直接回应两份外部评审）：
1. **正例必须改写**：从 chunk 抽事实 → LLM 改写成口语化问句 → 校验与**标题/原文**的
   bigram 覆盖率 < 0.5（旧 TUNING 组 3/4 条覆盖率 ≥0.5 = "照标题写"的偏置，评审实测指出）；
2. **补上「域内不可答」**：词面全在域内、语料里没有那个事实 —— 这才是诱发幻觉的那类，
   旧 A3 完全没覆盖；
3. **物理隔离**：按 `id` 哈希固定分流到两个文件，**不得事后调整**（否则又成自证）。

用法：
  python scripts/rag_eval_build.py --out tests/golden/rag
  python scripts/rag_eval_build.py --out tests/golden/rag --per-doc 4 --model qwen2.5:7b
"""
import argparse
import hashlib
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.rag import store as rag_store      # noqa: E402
from utils.rag.tokenize import tokenize       # noqa: E402

OLLAMA = "http://127.0.0.1:11434/api/generate"
COVERAGE_MAX = 0.5          # 与标题/原文的字面覆盖率上限（超过视为照抄，丢弃）


def llm(prompt, model, timeout=180):
    body = json.dumps({"model": model, "prompt": prompt, "stream": False,
                       "options": {"temperature": 0.3}}).encode("utf-8")
    req = urllib.request.Request(OLLAMA, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8")).get("response", "").strip()


def coverage(query, ref):
    """query 的 bigram 有多大比例出现在 ref 里（衡量"照抄"程度）。"""
    q, r = set(tokenize(query)), set(tokenize(ref or ""))
    return len(q & r) / len(q) if q else 1.0


def parse_lines(raw, want):
    """从 LLM 输出里抠出问题行（兼容 1. / - / 、/ 换行）。"""
    out = []
    for line in (raw or "").splitlines():
        s = line.strip()
        for pre in ("1.", "2.", "3.", "4.", "5.", "6.", "-", "•", "、"):
            if s.startswith(pre):
                s = s[len(pre):].strip()
        if len(s) >= 6:
            out.append(s.rstrip("。").strip())
    return out[:want]


def route(qid):
    """按 id 哈希固定分流到 tuning / holdout（不得事后调整）。"""
    h = int(hashlib.sha1(qid.encode("utf-8")).hexdigest()[:8], 16)
    return "holdout" if h % 2 else "tuning"


def pick_fact_chunks(meta, per_doc, min_len=120):
    """挑信息密度高的块（够长、含数字），按文档分组。"""
    by_doc = {}
    for m in meta:
        text = (m.get("text") or "").strip()
        if len(text) < min_len:
            continue
        by_doc.setdefault(m["doc_id"], []).append(m)
    picked = []
    for doc_id, items in by_doc.items():
        items.sort(key=lambda m: (-sum(c.isdigit() for c in m["text"]), -len(m["text"])))
        picked.extend(items[:per_doc])
    return picked


PROMPT_ANSWERABLE = """下面是一段上市公司公告原文。请据此提出 {n} 个**投资者真会问**的问题。

硬性要求：
1. 每个问题都必须能由这段原文回答；
2. **不得照抄原文的连续字串**（不要出现 6 个以上与原文相同的连续汉字）；
3. 用口语化提问（像散户在问），可以换同义词、换句式、把数字换成"多少"；
4. 只输出问题，每行一个，不要编号、不要解释。

原文：
\"\"\"{text}\"\"\""""


PROMPT_UNANSWERABLE = """以下是一家上市公司**已发布公告**的主题清单：
{titles}

请提出 {n} 个问题，满足：
1. 问题里要出现该公司或所处行业相关的词（听起来像在问这家公司）；
2. 答案**不可能**出现在上面这些公告里（问的是未披露的、未来的、或这些公告不涉及的事）；
3. 只输出问题，每行一个，不要编号、不要解释。"""


PROMPT_NEAR_MISS = """以下是某公司公告语料**确实包含**的内容类型：
- 财务数据（营收、利润、每股分红的具体数字）
- 会议决议的程序性描述（谁参加、通过了什么议案）
- 人事任免的履历描述

请提出 {n} 个问题，它们**使用了公告里常见的关键词**（如"护城河""风险控制""战略""治理""分红政策"），
但问的是**这些公告并不包含的定性分析或评价**，例如"某某的护城河是否在变窄"这类需要专业判断的问题。
只输出问题，每行一个，不要编号、不要解释。"""


OOD_SEEDS = [
    "量子计算最新进展", "python 异步编程入门", "今天天气怎么样", "如何学习滑雪",
    "世界杯决赛比分是多少", "北京到上海的航班时刻", "怎么写一封求职信",
    "如何训练一个大语言模型", "推荐几部好看的电影", "红烧肉怎么做才好吃",
]

# ---- 负例模板（**规则生成，零 LLM 调用**：LLM 生成太慢且不可控）----
# 域内不可答：**域内实体 + 语料不可能披露的事实类型** —— 这才是诱发幻觉的那类
UNANSWERABLE_TEMPLATES = [
    "{e}在人工智能领域的布局进展如何",
    "{e}有没有进军新能源行业的计划",
    "{e}明年的营收目标是多少",
    "{e}在海外市场的份额有多大",
    "{e}的员工持股计划具体条款是什么",
    "{e}与哪些科技公司建立了战略合作",
]
# 近义干扰：词面命中（护城河/治理/战略…）但问的是**语料不含的定性判断**
NEAR_MISS_TEMPLATES = [
    "{e}的护城河是否在变窄",
    "{e}的公司治理水平在行业内排第几",
    "{e}的品牌溢价还能维持多久",
    "{e}的管理层能力该怎么评价",
    "{e}的长期战略是否清晰",
]


def _entities(titles):
    """域内实体（固定 + 标题补充）。刻意保持简单可靠，不做脆弱的标题解析。"""
    base = ["贵州茅台", "公司董事会", "贵州茅台集团财务有限公司", "公司股东会", "公司管理层"]
    return base


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="tests/golden/rag")
    ap.add_argument("--db", default=None)
    ap.add_argument("--model", default="qwen2.5:7b")
    ap.add_argument("--per-doc", type=int, default=1)
    args = ap.parse_args(argv)

    conn = rag_store.get_conn(args.db)
    meta, matrix = rag_store.load_index(conn)
    conn.close()
    if not meta or matrix is None:
        print("[build] 索引为空 —— 先跑 scripts/rag_ingest.py")
        return 2

    titles = sorted({(m.get("title") or "") for m in meta if m.get("title")})
    print("[build] chunks=%d docs=%d titles=%d" % (len(meta), len({m['doc_id'] for m in meta}), len(titles)))

    rows, dropped = [], 0

    # ---- ① 域内可答（块级标注）----
    fact_chunks = pick_fact_chunks(meta, args.per_doc)
    print("[build] 正例：将对 %d 个事实块做 LLM 改写（每块约 10-20s）" % len(fact_chunks), flush=True)
    for i, m in enumerate(fact_chunks, 1):
        print("  [%d/%d] doc=%s len=%d" % (i, len(fact_chunks), m["doc_id"],
                                           len(m["text"] or "")), flush=True)
        try:
            raw = llm(PROMPT_ANSWERABLE.format(n=2, text=(m["text"] or "")[:1200]), args.model)
        except Exception as e:
            print("  [warn] LLM 失败：%s" % str(e)[:80])
            continue
        for q in parse_lines(raw, 2):
            cov_title = coverage(q, m.get("title") or "")
            cov_text = coverage(q, m.get("text") or "")
            if cov_title > COVERAGE_MAX or cov_text > COVERAGE_MAX:
                dropped += 1
                continue          # 照抄 → 丢弃（否则复刻"照标题写"的偏置）
            rows.append({"kind": "in_domain_answerable", "query": q,
                         "answer_chunk_ids": [m["chunk_id"]],
                         "source_doc": m.get("title"), "coverage_vs_title": round(cov_title, 3),
                         "coverage_vs_source": round(cov_text, 3)})

    # ---- ② 域内不可答（规则生成：域内实体 × 语料不可能披露的事实类型）----
    ents = _entities(titles)
    for e in ents:
        for tpl in UNANSWERABLE_TEMPLATES:
            rows.append({"kind": "in_domain_unanswerable", "query": tpl.format(e=e),
                         "answer_chunk_ids": [],
                         "note": "规则生成：域内实体 + 语料不含的事实类型"})

    # ---- ③ 域外 ----
    for q in OOD_SEEDS:
        rows.append({"kind": "out_of_domain", "query": q, "answer_chunk_ids": []})

    # ---- ④ 近义干扰（规则生成：词面命中但问的是定性判断）----
    for e in ents:
        for tpl in NEAR_MISS_TEMPLATES:
            rows.append({"kind": "near_miss", "query": tpl.format(e=e),
                         "answer_chunk_ids": [],
                         "note": "规则生成：词面命中但问的是语料不含的定性判断"})

    # ---- 分流 + 编号 ----
    buckets = {"tuning": {"rel": [], "irr": []}, "holdout": {"rel": [], "irr": []}}
    counters = {"rel": 0, "irr": 0}
    for r in rows:
        is_rel = r["kind"] == "in_domain_answerable"
        key = "rel" if is_rel else "irr"
        counters[key] += 1
        r["id"] = "%s-%04d" % (key, counters[key])
        buckets[route(r["id"])][key].append(r)

    os.makedirs(args.out, exist_ok=True)
    for split in ("tuning", "holdout"):
        for key in ("rel", "irr"):
            path = os.path.join(args.out, "queries_%s_%s.json" % (split, key))
            with open(path, "w", encoding="utf-8") as f:
                json.dump(buckets[split][key], f, ensure_ascii=False, indent=1)
            print("[build] %s : %d 条 -> %s" % (split, len(buckets[split][key]), path))

    snap = {"n_chunks": len(meta), "n_docs": len({m["doc_id"] for m in meta}),
            "dim": int(matrix.shape[1]), "drop_by_coverage": dropped,
            "model_for_gen": args.model}
    with open(os.path.join(args.out, "corpus_snapshot.json"), "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)
    print("[build] 快照: %s" % snap)
    print("[build] 因字面覆盖率过高被丢弃: %d 条" % dropped)
    print("[build] RESULT: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

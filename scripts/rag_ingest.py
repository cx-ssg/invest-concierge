# -*- coding: utf-8 -*-
"""M1 采集层（第一版）：拉个股公告 → 切块 → 向量化 → 落 kb.db。

用法：
  python scripts/rag_ingest.py --code 600519 --limit 30
  python scripts/rag_ingest.py --code 600519 --no-embed     # 只落文本（无 ollama 时）

设计依据 docs/COVERAGE_DESIGN.md §3.2 采集层/索引层。
幂等：documents 的唯一键是 (code, source, published_at)，重复拉取只更新不新增。

⚠️ 已知限制：`stock_individual_notice_report` **只返回公告标题，不含正文**。
本切片先打通链路；正文抓取（巨潮/东财详情页 + PDF 解析）属后续切片，见 spec §1。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.rag import store as rag_store      # noqa: E402
from utils.rag.chunker import chunk_document  # noqa: E402

SOURCE = "notice"


def fetch_notices(code, begin, end):
    """拉个股公告列表（东方财富）。返回 list[dict]。"""
    import akshare as ak
    df = ak.stock_individual_notice_report(
        security=code, symbol="全部", begin_date=begin, end_date=end
    )
    rows = []
    for _, r in df.iterrows():
        title = str(r.get("公告标题") or "").strip()
        if not title:
            continue
        rows.append({
            "code": str(r.get("代码") or code),
            "source": SOURCE,
            "title": title,
            "url": str(r.get("网址") or ""),
            "published_at": str(r.get("公告日期") or "")[:10],
            "notice_type": str(r.get("公告类型") or ""),
            "name": str(r.get("名称") or ""),
        })
    return rows


def build_text(row):
    """公告当前只有标题 —— 把可用元信息拼成可检索文本（正文抓取属后续切片）。"""
    return "{}\n公告类型：{}\n公司：{}".format(
        row["title"], row["notice_type"], row["name"]
    )


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="600519")
    ap.add_argument("--begin", default="20260101")
    ap.add_argument("--end", default="20260915")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--db", default=None)
    ap.add_argument("--no-embed", action="store_true")
    args = ap.parse_args(argv)

    rows = fetch_notices(args.code, args.begin, args.end)[: args.limit]
    print("[ingest] 拉到公告 {} 条（code={}）".format(len(rows), args.code))
    if not rows:
        print("[ingest] RESULT: NO_DATA")
        return 2

    conn = rag_store.get_conn(args.db)
    rag_store.ensure_schema(conn)

    pending = []   # (chunk_id, text) 待向量化
    for row in rows:
        chunks = chunk_document(build_text(row))
        if not chunks:
            continue
        doc_id = rag_store.upsert_document(conn, row)
        ids = rag_store.insert_chunks(conn, doc_id, chunks)
        for cid, c in zip(ids, chunks):
            pending.append((cid, c["text"]))

    print("[ingest] 落库完成：新增/更新块 {} 个".format(len(pending)))

    if args.no_embed:
        print("[ingest] --no-embed：跳过向量化")
    else:
        from utils.rag.embed import embed_texts
        try:
            vecs = embed_texts([t for _, t in pending])
            rag_store.save_embeddings(conn, [cid for cid, _ in pending], vecs)
            print("[ingest] 向量化完成：{} 个块（dim={}）".format(len(vecs), len(vecs[0])))
        except RuntimeError as e:
            print("[ingest] embedding 不可用 -> 仅落文本（BM25 单路仍可用）")
            print("         原因：{}".format(str(e)[:140]))
            print("[ingest] RESULT: DEGRADED")
            print("[ingest] stats:", rag_store.stats(conn))
            conn.close()
            return 0

    print("[ingest] RESULT: OK")
    print("[ingest] stats:", rag_store.stats(conn))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

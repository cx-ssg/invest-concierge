# -*- coding: utf-8 -*-
"""M1 采集层：拉个股公告（**含正文**）→ 切块 → 向量化 → 落 kb.db。

数据源（2026-09-16 实测打通，取代只给标题的 akshare 路径）：
- 列表：`np-anotice-stock.eastmoney.com/api/security/ann`（含 art_code）
- 正文：`np-cnotice-stock.eastmoney.com/api/content/ann?art_code=...`
  实测样本 **1285 字真实正文**（含「重要内容提示」等结构），另带 `attach_url` 指向 PDF。
  → 正文既可直接获取，**PDF 解析降级为可选**（不再是 M1 的前置）。

用法：
  python scripts/rag_ingest.py --code 600519 --limit 30
  python scripts/rag_ingest.py --code 600519 --limit 5 --no-body    # 只标题（冒烟，快）
  python scripts/rag_ingest.py --code 600519 --no-embed             # 不向量化（BM25 单路）

幂等：documents 唯一键 `(code, source, published_at, title)`（utils/rag/store.py v2）。
失败策略：正文抓不到 → 回退「标题 + 类型 + 公司」，**绝不静默丢公告**。
"""
import argparse
import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.rag import store as rag_store      # noqa: E402
from utils.rag.chunker import chunk_document  # noqa: E402

SOURCE = "notice"
LIST_URL = ("https://np-anotice-stock.eastmoney.com/api/security/ann"
            "?sr=-1&page_size={size}&page_index=1&ann_type=A"
            "&client_source=web&stock_list={code}")
CONTENT_URL = ("https://np-cnotice-stock.eastmoney.com/api/content/ann"
               "?art_code={art}&client_source=web&page_index=1")
DETAIL_URL = "https://data.eastmoney.com/notices/detail/{code}/{art}.html"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
_TAG = re.compile(r"<[^>]+>")


def _http_get(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _first_field(items, key):
    """防御性取嵌套列表首项的字段（东财 columns/codes 结构会随接口变动）。"""
    try:
        first = (items or [])[0]
        return (first.get(key) or "") if isinstance(first, dict) else ""
    except Exception:
        return ""


def clean_body(raw):
    """清洗正文：去 HTML 标签、统一空白、压缩连续空行。**表格行（含 |）必须保留**。"""
    if not raw:
        return ""
    text = _TAG.sub("", str(raw))
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def build_text(row, body=None):
    """组装可检索文本：标题在头部（最强检索信号）+ 正文；无正文回退元信息。"""
    head = row.get("title", "") or ""
    if body and str(body).strip():
        return "{}\n{}".format(head, str(body).strip())
    return "{}\n公告类型：{}\n公司：{}".format(
        head, row.get("notice_type", "") or "", row.get("name", "") or "")


def fetch_notices(code, size=50, skipped_sink=None):
    """东财公告列表。列表是硬依赖，失败直接抛（由 main 转成 NO_DATA）。

    `skipped_sink`：可选 list，用于上报「因标题为空被跳过」的条数 ——
    静默丢弃会让语料缺口无从察觉（critic 独立审计 2026-09-16 F4）。
    """
    data = json.loads(_http_get(LIST_URL.format(size=size, code=code)))
    rows = []
    skipped = 0
    for it in ((data.get("data") or {}).get("list")) or []:
        title = (it.get("title") or "").strip()
        if not title:
            skipped += 1
            continue
        art = it.get("art_code") or ""
        rows.append({
            "code": code,
            "source": SOURCE,
            "title": title,
            "url": DETAIL_URL.format(code=code, art=art),
            "published_at": str(it.get("notice_date") or "")[:10],
            "art_code": art,
            "notice_type": _first_field(it.get("columns"), "column_name"),
            "name": _first_field(it.get("codes"), "short_name"),
        })
    if skipped_sink is not None:
        skipped_sink.append(skipped)
    return rows


def fetch_notice_body(art_code, timeout=30, error_sink=None):
    """公告正文；**任何异常返回 None**（调用方回退标题，不中断整轮 ingest）。

    `error_sink`：可选 list，失败时 append 原因 —— 否则调用方无法区分
    「这条公告本就无正文」与「接口挂了」（两者都是 None）（critic 审计 2026-09-16 F3）。
    """
    if not art_code:
        if error_sink is not None:
            error_sink.append("EMPTY_ART_CODE")
        return None
    try:
        data = json.loads(_http_get(CONTENT_URL.format(art=art_code), timeout=timeout))
    except Exception as e:
        if error_sink is not None:
            error_sink.append("{}: {}".format(type(e).__name__, str(e)[:80]))
        return None
    body = clean_body((data.get("data") or {}).get("notice_content") or "")
    return body or None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="600519")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--size", type=int, default=50, help="列表页大小（东财单页上限 50）")
    ap.add_argument("--db", default=None)
    ap.add_argument("--no-body", action="store_true", help="不抓正文（只标题，冒烟用）")
    ap.add_argument("--no-embed", action="store_true")
    args = ap.parse_args(argv)

    skipped = []
    try:
        rows = fetch_notices(args.code, size=args.size, skipped_sink=skipped)[: args.limit]
    except Exception as e:
        print("[ingest] 列表拉取失败：{}: {}".format(type(e).__name__, str(e)[:160]))
        print("[ingest] RESULT: NO_DATA")
        return 2
    print("[ingest] 公告列表 {} 条（code={}）".format(len(rows), args.code))
    if skipped and skipped[0]:
        print("[ingest] ⚠️ 列表中有 {} 条因标题为空被跳过".format(skipped[0]))
    if not rows:
        print("[ingest] RESULT: NO_DATA")
        return 2

    conn = rag_store.get_conn(args.db)
    rag_store.ensure_schema(conn)

    pending, body_ok, body_errors, empty_chunks = [], 0, [], 0
    for row in rows:
        body = None
        if not args.no_body:
            body = fetch_notice_body(row.get("art_code"), error_sink=body_errors)
            if body:
                body_ok += 1
        chunks = chunk_document(build_text(row, body))
        if not chunks:
            empty_chunks += 1
            continue
        doc_id = rag_store.upsert_document(conn, row)
        ids = rag_store.insert_chunks(conn, doc_id, chunks)
        for cid, c in zip(ids, chunks):
            pending.append((cid, c["text"]))
    print("[ingest] 正文命中 {}/{} 条；切块为空 {} 条；落库块 {} 个".format(
        body_ok, len(rows), empty_chunks, len(pending)))
    if body_errors:
        print("[ingest] ⚠️ 正文抓取失败 {} 条，样本：{}".format(
            len(body_errors), body_errors[:3]))

    if not pending:
        # 旧实现会一路走到 vecs[0] → IndexError（critic 独立审计 2026-09-16 F5）
        print("[ingest] ⚠️ 无块可入库；stats:", rag_store.stats(conn))
        conn.close()
        print("[ingest] RESULT: NO_CHUNKS")
        return 2

    if args.no_embed:
        print("[ingest] --no-embed：跳过向量化")
    else:
        from utils.rag.embed import embed_texts_batched
        try:
            def _progress(done, total):
                print("  embed {}/{}".format(done, total), flush=True)

            vecs = embed_texts_batched([t for _, t in pending], on_progress=_progress)
            rag_store.save_embeddings(conn, [cid for cid, _ in pending], vecs)
            print("[ingest] 向量化完成：{} 个块（dim={}）".format(len(vecs), len(vecs[0])))
        except RuntimeError as e:
            print("[ingest] embedding 不可用 -> 仅落文本（BM25 单路仍可用）")
            print("         原因：{}".format(str(e)[:140]))
            print("[ingest] stats:", rag_store.stats(conn))
            conn.close()
            print("[ingest] RESULT: DEGRADED")
            return 0

    print("[ingest] stats:", rag_store.stats(conn))
    conn.close()
    print("[ingest] RESULT: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

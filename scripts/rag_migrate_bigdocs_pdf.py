# -*- coding: utf-8 -*-
"""把 kb.db 里的**大文档**（被 5000 字截断的那几篇）替换为巨潮 PDF 全文。

背景（2026-10-01 实测）：东财 API `notice_content` 在 ~5000 字处截断 ——
同一篇半年报 API 正文 3,040 字 / 5 块，而 PDF 全文 118,591 字 / 198 块；
对照实验（`scripts/rag_pdf_ab.py`）显示报表细节类问题 API 侧 hit@5 = 0/10、PDF 侧 8/10。

⚠️ **本脚本会改写主语料**（`kb.db`）⇒ 跑之前必须备份：
    `Copy-Item kb.db kb.db.<日期>.bak`
⚠️ 它会**替换指定 doc_id 的块**，因此引用该文档旧 `chunk_id` 的 gold 需要重标
   —— 跑完请用 `--print-chunk-map` 的输出核对，并重跑 `rag_eval.py`。

用法：
  python scripts/rag_migrate_bigdocs_pdf.py --db kb.db --doc-id 6 --code 600519
  python scripts/rag_migrate_bigdocs_pdf.py --db kb.db --doc-id 6 --no-embed
"""
import argparse
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.rag import store as rag_store                          # noqa: E402
from utils.rag.chunker import chunk_document                      # noqa: E402
from scripts.rag_ingest_pdf import (extract_pdf_text, fetch_announcements,  # noqa: E402
                                    _get_bytes, find_numbers)


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="kb.db")
    ap.add_argument("--doc-id", type=int, required=True, help="要替换的 documents.id")
    ap.add_argument("--code", default="600519")
    ap.add_argument("--category", default="category_bndbg_szsh")
    ap.add_argument("--se-date", default="2026-01-01~2026-12-31")
    ap.add_argument("--pdf-dir", default=None)
    ap.add_argument("--no-embed", action="store_true")
    ap.add_argument("--print-chunk-map", action="store_true",
                    help="打印新块的 (id, seq, 前 40 字)，便于对账/重标 gold")
    args = ap.parse_args(argv)

    if args.pdf_dir is None:
        import config
        args.pdf_dir = os.path.join(config._DATA_DIR, "corpus", "pdf")
    os.makedirs(args.pdf_dir, exist_ok=True)

    conn = rag_store.get_conn(args.db)
    row = conn.execute("SELECT id, title FROM documents WHERE id=?", (args.doc_id,)).fetchone()
    if not row:
        print("[migrate] 找不到 doc_id=%s" % args.doc_id)
        print("[migrate] RESULT: NO_DOC")
        return 2
    title = row["title"]
    old_ids = [r[0] for r in conn.execute(
        "SELECT id FROM chunks WHERE doc_id=? ORDER BY seq", (args.doc_id,))]
    print("[migrate] 目标 doc#%s「%s」现有 %d 块（id %s）"
          % (args.doc_id, title[:40], len(old_ids), old_ids))

    rows = fetch_announcements(args.code, args.category, args.se_date, drop_summary=True)
    if not rows:
        print("[migrate] 巨潮未找到公告（code=%s category=%s）" % (args.code, args.category))
        print("[migrate] RESULT: NO_DATA")
        return 2
    ann = rows[0]
    print("[migrate] 巨潮命中：「%s」（%s）" % (ann["title"][:40], ann["pdf_url"][:70]))

    blob = _get_bytes(ann["pdf_url"])
    if blob[:4] != b"%PDF":
        print("[migrate] [!] 下载内容不是 PDF（%d 字节）" % len(blob))
        print("[migrate] RESULT: NOT_PDF")
        return 2
    dest = os.path.join(args.pdf_dir, "doc{}_{}.pdf".format(
        args.doc_id, hashlib.md5(ann["pdf_url"].encode()).hexdigest()[:8]))
    with open(dest, "wb") as fh:
        fh.write(blob)

    text, n_pages, n_empty = extract_pdf_text(dest)
    chunks = chunk_document(text)
    print("[migrate] PDF %d 页 / %d 字 / %d 块（空白页 %d）"
          % (n_pages, len(text), len(chunks), n_empty))
    if not chunks:
        print("[migrate] RESULT: NO_CHUNKS")
        return 2

    conn.execute(
        "UPDATE documents SET url=?, file_path=?, sha256=? WHERE id=?",
        (ann["pdf_url"], dest, hashlib.sha256(blob).hexdigest(), args.doc_id))
    conn.commit()
    ids = rag_store.insert_chunks(conn, args.doc_id, chunks)
    print("[migrate] 写入 %d 块（覆盖同 seq 的旧块，其余为新增）" % len(ids))

    # 清理：旧块数 > 新块数时，删除多余的高 seq 块（本脚本预期新块更多，但仍做保护）
    if len(ids) < len(old_ids):
        stale = old_ids[len(ids):]
        for cid in stale:
            conn.execute("DELETE FROM chunks WHERE id=?", (cid,))
        conn.commit()
        print("[migrate] 清理多余的 %d 个旧块：%s" % (len(stale), stale))

    if args.no_embed:
        print("[migrate] --no-embed：跳过向量化（检索前必须补跑 embed）")
    else:
        from utils.rag.embed import embed_texts_batched
        vecs = embed_texts_batched([c["text"] for c in chunks])
        rag_store.save_embeddings(conn, ids, vecs)
        print("[migrate] 向量化完成：%d 块" % len(vecs))

    if args.print_chunk_map:
        print("[migrate] --- 新块映射（id / seq / 前 40 字）---")
        for cid, c in zip(ids, chunks):
            nums = sorted(find_numbers(c["text"]))[:3]
            print("   #%-4s seq=%-3s %s … [数字样本 %s]"
                  % (cid, c.get("seq"), c["text"][:40].replace("\n", " "), nums))

    print("[migrate] stats:", rag_store.stats(conn))
    conn.close()
    print("[migrate] RESULT: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

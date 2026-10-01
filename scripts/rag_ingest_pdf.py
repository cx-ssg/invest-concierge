# -*- coding: utf-8 -*-
"""巨潮 PDF 全文采集 —— 双轨语料的「全文本」一侧（计划：`docs/M1_PDF_AB_PLAN.md`）。

**为什么需要它**（2026-10-01 实测）：东财 `notice_content` 在 ~5000 字处截断 ——
同一篇半年报的 API 正文 **3,040 字**，而 PDF 全文 **118,591 字 / 110 页**
（含「管理层讨论与分析」完整章节 + 资产负债表 + 现金流量表）。

**为什么走巨潮而不是东财**：`static.cninfo.com.cn` 实测可直下（`%PDF-1`，832 KB）；
东财 `pdf.dfcfw.com` 有反爬（返回 `EO_Bot_Ssid` 混淆 JS 挑战页，加 Referer 也无效）。

**铁律（双轨隔离）**：本脚本只写 `--db` 指定的库（默认 `kb_pdf.db`），
**不触碰** `kb.db` 与 `scripts/rag_ingest.py`（现有 API 路径保持可用）。

用法：
  python scripts/rag_ingest_pdf.py --code 600519 --limit 2 --db kb_pdf.db
  python scripts/rag_ingest_pdf.py --code 600519 --limit 2 --no-embed     # 只抽文本 + 切块
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.rag import store as rag_store          # noqa: E402
from utils.rag.chunker import chunk_document      # noqa: E402

SOURCE = "notice_pdf"
QUERY_URL = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
PDF_BASE = "http://static.cninfo.com.cn/"
DEFAULT_CATEGORY = "category_bndbg_szsh"          # 半年度报告
DEFAULT_SE_DATE = "2026-01-01~2026-12-31"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

_FULLWIDTH = str.maketrans("０１２３４５６７８９", "0123456789")
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def parse_cninfo_announcements(payload, drop_summary=False, base=PDF_BASE):
    """巨潮 `hisAnnouncement/query` 响应 → `[{title, pdf_url, date, size_kb, sec_code}]`。

    - `adjunctUrl` 是相对路径（`finalpage/2026-08-15/xxx.PDF`）⇒ 拼成绝对 URL
    - `drop_summary=True` 时剔除「…摘要」条目（摘要是全文的子集，留着只是噪声）
    - 空响应 / 缺 `adjunctUrl` / 时间戳异常一律**跳过或兜底**，不抛异常
    """
    rows = []
    for it in (payload.get("announcements") or []):
        title = (it.get("announcementTitle") or "").strip()
        adj = (it.get("adjunctUrl") or "").strip()
        if not title or not adj:
            continue
        if drop_summary and "摘要" in title:
            continue
        try:
            date = datetime.datetime.fromtimestamp(
                float(it.get("announcementTime")) / 1000.0).strftime("%Y-%m-%d")
        except (TypeError, ValueError, OSError, OverflowError):
            date = ""
        rows.append({
            "title": title,
            "pdf_url": base + adj.lstrip("/"),
            "date": date if len(date) == 10 else "",
            "size_kb": it.get("adjunctSize"),
            "sec_code": it.get("secCode"),
        })
    return rows


def find_numbers(text):
    """抽文本里的数字（千分位去逗号、全角转半角），返回集合。

    用途：**跨语料判定答案命中** —— 两套语料的 `chunk_id` 不可对齐，
    只能用答案里的数字串当锚（如 `16.74`）。集合语义避免 `1.5` 命中 `11.5`。
    """
    if not text:
        return set()
    out = set()
    for m in _NUM_RE.finditer(text.translate(_FULLWIDTH)):
        s = m.group(0).replace(",", "")
        if s:
            out.add(s)
    return out


def _post_json(url, form, headers=None, timeout=60):
    h = {"User-Agent": UA, "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
         "X-Requested-With": "XMLHttpRequest",
         "Referer": "http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _get_bytes(url, headers=None, timeout=180):
    h = {"User-Agent": UA, "Accept": "*/*", "Referer": "http://www.cninfo.com.cn/"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_announcements(code, category=DEFAULT_CATEGORY, se_date=DEFAULT_SE_DATE,
                        page_size=30, drop_summary=True):
    """查巨潮公告列表。`stock` 参数格式为 `<code>,gssh0<code>`（沪市）。"""
    payload = _post_json(QUERY_URL, {
        "pageNum": 1, "pageSize": page_size, "column": "sse", "tabName": "fulltext",
        "plate": "", "stock": "{},gssh0{}".format(code, code), "searchkey": "", "secid": "",
        "category": category, "trade": "", "seDate": se_date,
        "sortName": "", "sortType": "", "isHLTitle": "true",
    })
    return parse_cninfo_announcements(payload, drop_summary=drop_summary)


def extract_pdf_text(path):
    """`pypdf` 抽全文；返回 `(text, n_pages, n_empty_pages)`。"""
    from pypdf import PdfReader          # 延迟 import：未装时给清晰报错
    reader = PdfReader(path)
    parts, empty = [], 0
    for page in reader.pages:
        t = page.extract_text() or ""
        if not t.strip():
            empty += 1
        parts.append(t)
    return "\n".join(parts), len(reader.pages), empty


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="600519")
    ap.add_argument("--category", default=DEFAULT_CATEGORY)
    ap.add_argument("--se-date", default=DEFAULT_SE_DATE)
    ap.add_argument("--limit", type=int, default=2, help="最多采几篇")
    ap.add_argument("--db", default=None, help="目标库（默认 kb_pdf.db，**不动 kb.db**）")
    ap.add_argument("--pdf-dir", default=None)
    ap.add_argument("--no-embed", action="store_true")
    args = ap.parse_args(argv)

    # ⚠️ 落盘位置按设计 §3.2 的 `_DATA_DIR/corpus/pdf/`：
    # `config._DATA_DIR` 在**源码模式=项目根**、exe 模式=LOCALAPPDATA。
    # （**不要**写进 `data/` —— 那是本项目的 **Python 包**（`__init__.py`/`cache.py`…），不是语料目录。）
    import config                                   # 延迟 import：测试导入本模块时不拉起配置
    pdf_dir = args.pdf_dir or os.path.join(config._DATA_DIR, "corpus", "pdf")
    os.makedirs(pdf_dir, exist_ok=True)
    db = args.db or "kb_pdf.db"

    try:
        rows = fetch_announcements(args.code, args.category, args.se_date,
                                   drop_summary=True)[: args.limit]
    except Exception as e:
        print("[pdf-ingest] 列表拉取失败：{}: {}".format(type(e).__name__, str(e)[:160]))
        print("[pdf-ingest] RESULT: NO_DATA")
        return 2
    print("[pdf-ingest] 列表 {} 条（code={} category={}）".format(
        len(rows), args.code, args.category))
    if not rows:
        print("[pdf-ingest] RESULT: NO_DATA")
        return 2

    conn = rag_store.get_conn(db)
    rag_store.ensure_schema(conn)

    pending, ok, errs, chars = [], 0, [], 0
    for row in rows:
        print("[pdf-ingest] 下载 {} …".format(row["title"][:40]), flush=True)
        try:
            blob = _get_bytes(row["pdf_url"])
        except Exception as e:
            errs.append("{}: {}".format(row["title"][:20], type(e).__name__))
            continue
        if blob[:4] != b"%PDF":
            errs.append("{}: NOT_PDF({}B)".format(row["title"][:20], len(blob)))
            continue
        dest = os.path.join(pdf_dir, "{}_{}.pdf".format(
            row["date"] or "0000-00-00", hashlib.md5(row["pdf_url"].encode()).hexdigest()[:8]))
        with open(dest, "wb") as fh:
            fh.write(blob)
        try:
            text, n_pages, n_empty = extract_pdf_text(dest)
        except Exception as e:
            errs.append("{}: EXTRACT_{}".format(row["title"][:20], type(e).__name__))
            continue
        if not text.strip():
            errs.append("{}: EMPTY_TEXT".format(row["title"][:20]))
            continue
        chunks = chunk_document(text)
        if not chunks:
            errs.append("{}: NO_CHUNKS".format(row["title"][:20]))
            continue
        doc = {
            "code": args.code, "source": SOURCE, "title": row["title"],
            "url": row["pdf_url"], "published_at": row["date"], "file_path": dest,
            "sha256": hashlib.sha256(blob).hexdigest(),
        }
        doc_id = rag_store.upsert_document(conn, doc)
        ids = rag_store.insert_chunks(conn, doc_id, chunks)
        for cid, c in zip(ids, chunks):
            pending.append((cid, c["text"]))
        ok += 1
        chars += len(text)
        print("[pdf-ingest]   {} 页 / {} 字 / {} 块（空白页 {}）".format(
            n_pages, len(text), len(chunks), n_empty))

    print("[pdf-ingest] 成功 {} / {} 篇；抽取合计 {} 字；落库块 {} 个".format(
        ok, len(rows), chars, len(pending)))
    if errs:
        print("[pdf-ingest] [!] 失败 {} 条，样本：{}".format(len(errs), errs[:3]))
    if not pending:
        print("[pdf-ingest] stats:", rag_store.stats(conn))
        conn.close()
        print("[pdf-ingest] RESULT: NO_CHUNKS")
        return 2

    if args.no_embed:
        print("[pdf-ingest] --no-embed：跳过向量化")
    else:
        from utils.rag.embed import embed_texts_batched
        try:
            def _progress(done, total):
                print("  embed {}/{}".format(done, total), flush=True)

            vecs = embed_texts_batched([t for _, t in pending], on_progress=_progress)
            rag_store.save_embeddings(conn, [cid for cid, _ in pending], vecs)
            print("[pdf-ingest] 向量化完成：{} 块".format(len(vecs)))
        except Exception as e:
            print("[pdf-ingest] [!] 向量化失败（块已落库，可稍后重跑）：{}: {}".format(
                type(e).__name__, str(e)[:160]))
    print("[pdf-ingest] stats:", rag_store.stats(conn))
    conn.close()
    print("[pdf-ingest] RESULT: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

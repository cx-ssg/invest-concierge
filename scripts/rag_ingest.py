# -*- coding: utf-8 -*-
"""M1 采集层：拉个股公告（**含正文**）→ 切块 → 向量化 → 落 kb.db。

数据源（2026-09-16 实测打通，取代只给标题的 akshare 路径）：
- 列表：`np-anotice-stock.eastmoney.com/api/security/ann`（含 art_code）
- 正文：`np-cnotice-stock.eastmoney.com/api/content/ann?art_code=...&page_index=N`
  实测样本 **1285 字真实正文**（含「重要内容提示」等结构），另带 `attach_url` 指向 PDF。
  → 正文既可直接获取，**PDF 解析降级为可选**（不再是 M1 的前置）。

⚠️ **翻页聚合（C1，2026-10-03 实测根因）**：正文 API 是**分页**的 ——
单次请求恒定只回 **5000 字**（`page_size` 请求参数放大无效），返回体自带
`page_size` 字段 = **总页数**（《贵州茅台2026年半年度报告》实测 = 44）。
旧实现只请求 `page_index=1` ⇒ 只拿到封面/重要提示，「第三节 管理层讨论与分析」
等正文全部缺失（该篇旧库仅 5 块 / 3040 字）。现按 `page_index` 递增逐页取并拼接。

用法：
  python scripts/rag_ingest.py --code 600519 --limit 30
  python scripts/rag_ingest.py --code 600519 --limit 5 --no-body    # 只标题（冒烟，快）
  python scripts/rag_ingest.py --code 600519 --no-embed             # 不向量化（BM25 单路）
  python scripts/rag_ingest.py --code 600519 --art-code AN2026...994408 --db kb.db
                                                                    # 只按 doc 重采这一篇

幂等：documents 唯一键 `(code, source, published_at, title)`（utils/rag/store.py v2）。
失败策略：正文抓不到 → 回退「标题 + 类型 + 公司」，**绝不静默丢公告**；
翻页中途失败 → **保留已取页**（取到几页算几页），不整篇丢弃。
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.rag import store as rag_store      # noqa: E402
from utils.rag.chunker import chunk_document  # noqa: E402

SOURCE = "notice"
LIST_URL = ("https://np-anotice-stock.eastmoney.com/api/security/ann"
            "?sr=-1&page_size={size}&page_index=1&ann_type=A"
            "&client_source=web&stock_list={code}")
CONTENT_URL = ("https://np-cnotice-stock.eastmoney.com/api/content/ann"
               "?art_code={art}&client_source=web&page_index={page_index}")
DETAIL_URL = "https://data.eastmoney.com/notices/detail/{code}/{art}.html"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
_TAG = re.compile(r"<[^>]+>")

# 翻页安全上限（H1 · 2026-10-03 复核后由 60 提到 160）。依据：
#   ① 实测最大总页数 = **114**（泸州老窖 000568 英文半年报 AN202609041829017400，
#      上游返回字段 page_size=114）；次大 = 99（五粮液 000858 英文半年报
#      AN202609111829278393）。60 会把这 2 篇硬截断（E1 实测：均止于 60 页 / truncated）。
#   ② 160 = 114 × 1.40，留 40% 余量（同一份报告的中英文本页数不同，未来年报可能更长）。
#   ③ 单页实测 4.7s（含 0.3s 页间延时 ≈ 5.0s/页）⇒ 160 页最坏 ≈ 800s，仍 < 单篇全局时限
#      TOTAL_TIMEOUT_S=1800s（2.25× 余量）：**上限仍然是那个先触发的闸**，全局时限继续兜底
#      「上游异常慢」。实测 5.07 块/页（99 页→503 块、114 页→578 块）⇒ 160 页 ≈ 810 块/篇
#      （float32 向量 ≈ 3.3MB），可接受。
#   ④ 非法页防护不变：页号仍以**上游自称的 page_size 为准**（取满即停），本上限只用于
#      「上游谎报超大 page_size / 不声明页数」时的硬闸 —— 绝不无限翻。
MAX_PAGES = 160
PAGE_SLEEP = 0.3        # 页间延时（秒）—— 限速，避免打爆上游
EMPTY_RETRIES = 2       # 「上游自称这页存在、却回空」时的重试次数（抖动/限流）
EMPTY_RETRY_BACKOFF = 1.0   # 空页重试退避基数（秒；第 n 次重试等 n×基数）
# C-R1（审计 C-F8）：**单篇全局时限**。没有它时最坏 = max_pages ×(1+2 重试)× timeout 30s
# = 5400s（90 分钟），函数内无上界。默认给足（30 分钟）：实测最慢篇（44 页半年报，
# 走系统代理）588.9s，1800s 有 ~3× 余量；把最坏情况从 90 分钟压到 30 分钟。
# 0/负数 = 不设全局时限（保留旧行为，供驱动层自带 900s 兜底的场景使用）。
TOTAL_TIMEOUT_S = 1800


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


def _as_int(value):
    """宽松转 int（东财字段类型会变：可能是 str/None/float）。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _report_pages(sink, art_code, pages, chars, stop_reason, reported_page_size, retries=0,
                  truncated=False, fetched_pages=None, duplicate_pages=0):
    """把「这篇取了几页 / 几个字 / 为什么停 / 是否被截断」交给调用方（可观测性，V5）。

    C-R1（审计 C-F6②）：新增 `truncated`（是否**有理由怀疑正文不完整**）与
    `fetched_pages` / `duplicate_pages`。这三个字段随 `--body-log` JSONL 长存，
    并在 `main()` 的逐篇输出里以 `⚠️ truncated` 直接可见 —— 硬截断不再「库里像完整文档」。
    """
    if sink is not None:
        sink.append({"art_code": art_code, "pages": pages, "chars": chars,
                     "stop_reason": stop_reason, "retries": retries,
                     "reported_page_size": reported_page_size,
                     "truncated": bool(truncated),
                     "fetched_pages": pages if fetched_pages is None else fetched_pages,
                     "duplicate_pages": duplicate_pages})


def _fetch_page(art_code, page_index, timeout=30):
    """取单页，返回 `(页面文本, 返回体 page_size, 错误描述)`（三者互斥：出错时前两项为 None）。"""
    try:
        data = json.loads(_http_get(
            CONTENT_URL.format(art=art_code, page_index=page_index), timeout=timeout))
    except Exception as e:
        return None, None, "{}: {}".format(type(e).__name__, str(e)[:80])
    dd = data.get("data") or {}
    return clean_body(dd.get("notice_content") or ""), _as_int(dd.get("page_size")), None


def fetch_notice_body(art_code, timeout=30, error_sink=None, stats_sink=None,
                      max_pages=MAX_PAGES, page_sleep=PAGE_SLEEP,
                      empty_retries=EMPTY_RETRIES,
                      empty_retry_backoff=EMPTY_RETRY_BACKOFF,
                      total_timeout_s=TOTAL_TIMEOUT_S):
    """公告正文 —— **翻页聚合**；任何异常返回 None（调用方回退标题，不中断整轮 ingest）。

    2026-10-03 C1 实测：`notice_content` 每次请求**恒定只回 5000 字**（`page_size`
    请求参数放大到 10000/50000/200000 均无效），但 `page_index` 逐页有效，
    返回体自带 `page_size` = **总页数**。旧实现只取第 1 页 ⇒ 大报告正文丢失。

    终止条件（任一命中即停）：
      ① `page_index` 达到返回字段 `page_size`（总页数）
      ② 本页与上一页**整页重复**（**区分两种情形**，见 C-R1 说明）
      ③ 本页为空（**重试后**仍为空 —— 见下）
      ④ 达到安全上限 `max_pages`
      ⑤ 中途请求失败 ⇒ **降级保留已取页**（硬约束：不因某页失败整篇丢弃）
      ⑥ 达到全局时限 `total_timeout_s`（C-R1 新增，小于等于 0 = 不设限）

    ⚠️ **空页必须先重试**（C1 实跑教训）：上游会**偶发**返回空 `notice_content`
    （同一页 10 分钟后原样可取而无需改任何参数）。若一见空页就停，整篇会静默截断 ——
    C1 首次重采实测被卡在 **17/44 页**（46,941 字符），正是本任务要修的那类「静默截断」。
    重试只在**上游自称这一页存在**（`page_size >= page_index`）时发生；否则空响应
    就是真到头，不浪费请求。

    ⚠️ **C-R1（审计 C-F6①）整页重复必须分两种**（原实现一律停 ⇒ 会静默丢页）：
      · **文档内重复**（`reported` 存在且 `pi <= reported`）：上游自称这页存在，
        合法文档也可能相邻两页同文（重复长块/版权页/同文附件）⇒ **继续翻到 reported**。
        重复文本不再拼进正文（与上一页逐字相同 ⇒ 信息零损失），只计入 `duplicate_pages`。
      · **越界/末尾重复**（`reported` 缺失，或 `pi > reported`）：上游对越界页重发末页
        （C1 实测 page_index=45 返回与 44 相同），或未声明总页数时把「相邻重复」当末尾哨兵
        ⇒ **安全停止**（原有语义不变）。

    `stats_sink`：可选 list；每次调用 append 一条
    `{"art_code","pages","chars","stop_reason","retries","reported_page_size",
      "truncated","fetched_pages","duplicate_pages"}`。
    `truncated=True` 表示**有理由怀疑正文不完整**（空页/请求失败/max_pages/总时限/
    越界重复时页数不足），随 `--body-log` 落盘、并在逐篇 stdout 上可见。
    """
    if not art_code:
        if error_sink is not None:
            error_sink.append("EMPTY_ART_CODE")
        _report_pages(stats_sink, art_code, 0, 0, "EMPTY_ART_CODE", None)
        return None

    deadline = None
    if total_timeout_s and total_timeout_s > 0:
        deadline = time.monotonic() + float(total_timeout_s)

    pages, stop_reason, reported, prev, retries, err = [], None, None, None, 0, None
    fetched, duplicates, truncated = 0, 0, False
    for pi in range(1, max_pages + 1):
        if deadline is not None and time.monotonic() >= deadline:
            stop_reason = "总时限 {}s 用尽（**降级保留已取 {} 页**）".format(
                total_timeout_s, len(pages))
            truncated = True
            break
        page, err, attempt = "", None, 0
        while True:
            page, pg_size, err = _fetch_page(art_code, pi, timeout)
            if reported is None and pg_size is not None:
                reported = pg_size
            if err is not None or page:
                break
            # 空页：仅当上游自称该页存在时才退避重试（否则就是真到头）
            if not (reported and pi <= reported) or attempt >= empty_retries:
                break
            attempt += 1
            retries += 1
            time.sleep(empty_retry_backoff * attempt)
        if err is not None:
            if error_sink is not None:
                error_sink.append("p{}{} {}".format(
                    pi, " retry%d" % attempt if attempt else "", err))
            stop_reason = ("请求失败 page_index={}（**降级保留已取 {} 页**）".format(pi, len(pages))
                           if pages else "请求失败 page_index={}: {}".format(pi, err))
            truncated = True
            break
        if not page:
            stop_reason = "空页 page_index={}{}".format(
                pi, "（重试 {} 次仍为空）".format(attempt) if attempt else "")
            # 上游声明这页存在却给空 ⇒ 截断；未声明总页数时的空页 = 真到头
            truncated = bool(reported is not None and pi <= reported)
            break
        fetched += 1

        if prev is not None and page == prev:
            if reported is not None and pi <= reported:
                # 文档内重复：继续翻（不拼重复文本），否则第 3 页起从未被请求
                duplicates += 1
            else:
                stop_reason = "整页重复（越界/末尾） page_index={}".format(pi)
                truncated = bool(reported is not None and len(pages) < reported)
                break
        else:
            prev = page
            pages.append(page)

        if reported and pi >= reported:
            stop_reason = "达到返回字段 page_size={}".format(reported)
            break
        if pi >= max_pages:
            stop_reason = "达到安全上限 max_pages={}".format(max_pages)
            truncated = True
            break
        time.sleep(page_sleep)          # 限速：仅在**还要继续翻页**时休眠

    body = "\n".join(pages)
    _report_pages(stats_sink, art_code, len(pages), len(body), stop_reason, reported, retries,
                  truncated=truncated, fetched_pages=fetched, duplicate_pages=duplicates)
    return body or None


def prune_stale_chunks(conn, doc_id, keep):
    """删掉 doc 中 `seq >= keep` 的旧块（连同其向量），返回删除条数。

    「按 doc 重采」在**块数变少**时必须清理：`insert_chunks` 只按 `(doc_id, seq)` upsert，
    高 seq 的旧块会以「本文档正文」的身份留在检索索引里 —— 静默脏数据。
    （C1 实例：doc#6 此前被 PDF 全文覆盖为 198 块，翻页文本为 183 块 ⇒ 15 个残留块。）
    """
    stale = [r[0] for r in conn.execute(
        "SELECT id FROM chunks WHERE doc_id=? AND seq>=? ORDER BY seq", (doc_id, keep))]
    for cid in stale:
        conn.execute("DELETE FROM embeddings WHERE chunk_id=?", (cid,))
        conn.execute("DELETE FROM chunks WHERE id=?", (cid,))
    if stale:
        conn.commit()
    return len(stale)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="600519")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--size", type=int, default=50, help="列表页大小（东财单页上限 50）")
    ap.add_argument("--db", default=None)
    ap.add_argument("--no-body", action="store_true", help="不抓正文（只标题，冒烟用）")
    ap.add_argument("--no-embed", action="store_true")
    ap.add_argument("--art-code", default=None,
                    help="只重采这一篇（按 art_code 从列表定位 ⇒ 幂等键/文档 id 不变）")
    ap.add_argument("--max-pages", type=int, default=MAX_PAGES, help="翻页安全上限")
    ap.add_argument("--page-sleep", type=float, default=PAGE_SLEEP, help="页间延时（秒）")
    ap.add_argument("--total-timeout-s", type=float, default=TOTAL_TIMEOUT_S,
                    help="单篇全局时限（秒）；<=0 = 不设限（默认 %(default)s）")
    ap.add_argument("--body-log", default=None,
                    help="把每篇的页数/字符数/终止原因追加写 JSONL（V5 事后核对用）")
    args = ap.parse_args(argv)

    skipped = []
    try:
        rows = fetch_notices(args.code, size=args.size, skipped_sink=skipped)
    except Exception as e:
        print("[ingest] 列表拉取失败：{}: {}".format(type(e).__name__, str(e)[:160]))
        print("[ingest] RESULT: NO_DATA")
        return 2
    if args.art_code:
        matched = [r for r in rows if r.get("art_code") == args.art_code]
        if not matched:
            print("[ingest] 列表前 {} 条中没有 art_code={}（可加大 --size）".format(
                len(rows), args.art_code))
            print("[ingest] RESULT: NO_DATA")
            return 2
        rows = matched
        print("[ingest] 单选重采：{}（art_code={}）".format(
            rows[0].get("title", "")[:50], args.art_code))
    rows = rows[: args.limit]
    print("[ingest] 公告列表 {} 条（code={}）".format(len(rows), args.code))
    if skipped and skipped[0]:
        print("[ingest] ⚠️ 列表中有 {} 条因标题为空被跳过".format(skipped[0]))
    if not rows:
        print("[ingest] RESULT: NO_DATA")
        return 2

    conn = rag_store.get_conn(args.db)
    rag_store.ensure_schema(conn)

    pending, body_ok, body_errors, empty_chunks, page_stats = [], 0, [], 0, []
    for row in rows:
        body = None
        if not args.no_body:
            body = fetch_notice_body(row.get("art_code"), error_sink=body_errors,
                                     stats_sink=page_stats, max_pages=args.max_pages,
                                     page_sleep=args.page_sleep,
                                     total_timeout_s=args.total_timeout_s)
            if page_stats:                       # 标注标题，便于日志/JSONL 阅读
                page_stats[-1].setdefault("title", row.get("title", ""))
            if body:
                body_ok += 1
        chunks = chunk_document(build_text(row, body))
        if not chunks:
            empty_chunks += 1
            continue
        doc_id = rag_store.upsert_document(conn, row)
        ids = rag_store.insert_chunks(conn, doc_id, chunks)
        pruned = prune_stale_chunks(conn, doc_id, len(chunks))
        if pruned:
            print("[ingest]   doc#{} 清理多余旧块 {} 个（seq>={}）".format(
                doc_id, pruned, len(chunks)))
        for cid, c in zip(ids, chunks):
            pending.append((cid, c["text"]))
    print("[ingest] 正文命中 {}/{} 条；切块为空 {} 条；落库块 {} 个".format(
        body_ok, len(rows), empty_chunks, len(pending)))
    for st in page_stats:                        # 逐篇可观测：页数 / 字符数 / 终止原因
        print("[ingest]   正文 {}：{} 页 / {} 字符 / 终止={}{}{}".format(
            st["art_code"] or "-", st["pages"], st["chars"], st["stop_reason"],
            "（空页重试 {} 次）".format(st.get("retries") or 0) if st.get("retries") else "",
            " ⚠️ truncated" if st.get("truncated") else ""))
    n_trunc = sum(1 for st in page_stats if st.get("truncated"))
    if n_trunc:
        # C-R1（C-F6②）：硬截断必须自己喊出来 —— 否则「库里的文档看起来是完整的」
        print("[ingest] ⚠️ 有 {} 篇正文被截断（truncated=True，详见逐篇终止原因 / --body-log）"
              .format(n_trunc))
    if args.body_log and page_stats:
        with open(args.body_log, "a", encoding="utf-8") as fh:
            for st in page_stats:
                fh.write(json.dumps(st, ensure_ascii=False) + "\n")
        print("[ingest] 采集日志 -> {}".format(args.body_log))
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

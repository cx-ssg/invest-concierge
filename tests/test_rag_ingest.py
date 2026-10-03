# -*- coding: utf-8 -*-
"""公告**正文**采集回归锁（M1 采集层补全）。

背景（2026-09-16 实测）：
- akshare `stock_individual_notice_report` **只给标题**，语料太短 → 检索质量受限；
- 东财公告正文 API `np-cnotice-stock.eastmoney.com/api/content/ann?art_code=...`
  实测可直接拿到 `notice_content`（样本 1285 字，含「重要内容提示」等结构）。

因此采集层改为：东财列表 API + 正文 API；**正文拿不到时回退标题文本**（不静默丢公告）。
"""
import json
import re

import pytest

from scripts import rag_ingest as ri
from utils.rag import store as rag_store
from utils.rag.embed import DIM as EMBED_DIM


# ==================== 文本组装：正文优先，标题回退 ====================

def test_build_text_prefers_body():
    """有正文时用正文，且标题仍放在头部（标题是最强的检索信号）。"""
    row = {"title": "关于召开业绩说明会的公告", "notice_type": "重大事项", "name": "贵州茅台"}
    text = ri.build_text(row, body="会议召开时间：2026 年 8 月 21 日。" * 5)
    assert text.startswith("关于召开业绩说明会的公告"), "标题必须在开头"
    assert "会议召开时间" in text, "正文必须进文本"
    assert len(text) > 100


def test_build_text_falls_back_to_metadata_when_no_body():
    """正文缺失 → 回退「标题 + 类型 + 公司」，绝不返回空串。"""
    row = {"title": "某某公告", "notice_type": "财务报告", "name": "贵州茅台"}
    text = ri.build_text(row, body=None)
    assert "某某公告" in text
    assert "公告类型：财务报告" in text
    assert "公司：贵州茅台" in text


def test_build_text_treats_blank_body_as_missing():
    """正文是纯空白字符串时同样走回退（脏数据不得产出空文本）。"""
    row = {"title": "某某公告", "notice_type": "财务报告", "name": "贵州茅台"}
    assert "公告类型" in ri.build_text(row, body="   \n\t  ")


# ==================== 正文清洗 ====================

def test_clean_body_strips_html_and_collapses_whitespace():
    raw = "<p>第一段内容</p>\n\n\n\n   第二段\u3000\u3000结束   "
    out = ri.clean_body(raw)
    assert "<p>" not in out and "</p>" not in out, "HTML 标签必须去掉"
    assert "\n\n\n" not in out, "连续空行必须压缩"
    assert "第一段内容" in out and "第二段" in out and "结束" in out


def test_clean_body_keeps_table_like_lines():
    """表格/编号行必须保留（财报数字靠它们，切块器的表格保护依赖 '|'）。"""
    raw = "| 项目 | 金额 |\n| --- | --- |\n| 营业收入 | 100 |"
    out = ri.clean_body(raw)
    assert out.count("|") >= 6, "表格行不得被清洗掉"


# ==================== 网络失败必须可回退 ====================

def test_fetch_notice_body_returns_none_on_network_error(monkeypatch):
    """网络异常 → 返回 None（调用方回退标题），**不得向上抛**中断整轮 ingest。"""
    def _boom(*args, **kwargs):
        raise OSError("blocked")

    monkeypatch.setattr(ri.urllib.request, "urlopen", _boom)
    assert ri.fetch_notice_body("AN202608141827994407") is None


def test_fetch_notice_body_returns_none_on_bad_json(monkeypatch):
    """返回体不是 JSON / 缺字段 → 同样返回 None，不抛异常。"""
    class _Resp:
        def read(self):
            return b"<html>not json</html>"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(ri.urllib.request, "urlopen", lambda *a, **k: _Resp())
    assert ri.fetch_notice_body("AN_BAD") is None


# ==================== 可观测性（critic 审计 2026-09-16 F3/F4）====================

def test_fetch_notice_body_reports_reason_to_sink(monkeypatch):
    """抓取失败必须把**原因**交给调用方。

    否则 `body_ok` 无法区分「这条公告本就无正文」与「抓取失败」—— 两者都是 None，
    运维时看不出是接口挂了还是数据如此。
    """
    def _boom(*args, **kwargs):
        raise OSError("blocked")

    monkeypatch.setattr(ri.urllib.request, "urlopen", _boom)
    sink = []
    assert ri.fetch_notice_body("AN1", error_sink=sink) is None
    assert sink and "OSError" in sink[0], "错误原因必须落进 sink"


def test_fetch_notice_body_without_sink_still_returns_none(monkeypatch):
    """不传 sink 时行为不变（向后兼容，不强制调用方接线）。"""
    def _boom(*args, **kwargs):
        raise OSError("x")

    monkeypatch.setattr(ri.urllib.request, "urlopen", _boom)
    assert ri.fetch_notice_body("AN1") is None


def test_fetch_notices_counts_skipped_blank_titles(monkeypatch):
    """标题为空的记录必须**计数上报**，不得静默丢弃（critic F4）。"""
    payload = {"data": {"list": [
        {"art_code": "A1", "title": "有效公告", "notice_date": "2026-08-15"},
        {"art_code": "A2", "title": "   ", "notice_date": "2026-08-15"},
    ]}}
    monkeypatch.setattr(ri, "_http_get", lambda url, timeout=30: json.dumps(payload))
    sink = []
    rows = ri.fetch_notices("600519", skipped_sink=sink)
    assert len(rows) == 1
    assert sink == [1], "被跳过的条数必须上报，实际 %s" % sink


# ==================== C1：正文翻页聚合（修「大报告只采到第一页」）====================
#
# 2026-10-03 实测根因：正文 API 是**分页**的 —— 单次请求恒定只回 5000 字
# （`page_size` 请求参数放大无效），返回体自带 `page_size` = 总页数
# （《贵州茅台2026年半年度报告》= 44）。旧实现只取 page_index=1 ⇒
# 「第三节 管理层讨论与分析」等正文全部丢失（该篇旧库仅 5 块 / 3040 字）。

def _pager(pages, page_size=None, fail_on=None, calls=None):
    """`_http_get` 替身：pages = {page_index: 页面文本}；未列出的页返回空。"""
    def _get(url, timeout=30):
        pi = int(re.search(r"page_index=(\d+)", url).group(1))
        if calls is not None:
            calls.append(pi)
        if fail_on is not None and pi == fail_on:
            raise OSError("blocked at page {}".format(pi))
        return json.dumps({"data": {"notice_content": pages.get(pi, ""),
                                    "page_size": page_size}})
    return _get


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """测试里不得真的睡 0.3s/页（否则 44 页的用例会拖慢套件）。"""
    monkeypatch.setattr(ri.time, "sleep", lambda *_a, **_k: None)


def test_fetch_notice_body_aggregates_pages_in_order(monkeypatch):
    """★核心：多页必须**按序拼接**取全，而不是只要第 1 页。"""
    pages = {1: "封面与重要提示", 2: "第三节 管理层讨论与分析", 3: "财务数据表"}
    monkeypatch.setattr(ri, "_http_get", _pager(pages, page_size=3))
    stats = []
    body = ri.fetch_notice_body("AN1", stats_sink=stats)
    assert body == "封面与重要提示\n第三节 管理层讨论与分析\n财务数据表"
    assert stats[0]["pages"] == 3
    assert stats[0]["chars"] == len(body)
    assert "page_size=3" in stats[0]["stop_reason"]


def test_fetch_notice_body_stops_at_reported_page_size(monkeypatch):
    """终止条件①：`page_index` 达到返回字段 `page_size`（总页数）即停，不多发请求。"""
    calls = []
    monkeypatch.setattr(ri, "_http_get",
                        _pager({i: "第%d页" % i for i in range(1, 6)}, page_size=2, calls=calls))
    body = ri.fetch_notice_body("AN1")
    assert calls == [1, 2], "超过总页数后不得继续请求，实际 %s" % calls
    assert body == "第1页\n第2页"


def test_fetch_notice_body_stops_on_duplicate_page(monkeypatch):
    """终止条件②：整页与上一页重复 ⇒ 停止，且**不把重复页写进正文**。"""
    calls = []
    monkeypatch.setattr(ri, "_http_get",
                        _pager({1: "A页", 2: "B页", 3: "B页", 4: "C页"}, calls=calls))
    stats = []
    body = ri.fetch_notice_body("AN1", stats_sink=stats)
    assert body == "A页\nB页"
    assert "整页重复" in stats[0]["stop_reason"]
    assert calls == [1, 2, 3], "发现重复页后不得继续翻"


def test_fetch_notice_body_stops_on_empty_page(monkeypatch):
    """终止条件③：空页即停（`page_size` 缺失时的兜底终止；此时不重试，省请求）。"""
    calls = []
    monkeypatch.setattr(ri, "_http_get", _pager({1: "只有一页", 2: ""}, calls=calls))
    stats = []
    body = ri.fetch_notice_body("AN1", stats_sink=stats)
    assert body == "只有一页"
    assert "空页" in stats[0]["stop_reason"]
    assert calls == [1, 2], "上游未声明总页数 ⇒ 空页就是真到头，不得重试，实际 %s" % calls


def test_fetch_notice_body_retries_transient_empty_page(monkeypatch):
    """★C1 实跑教训：上游**偶发**空页 ⇒ 必须重试，否则整篇静默截断。

    实测（2026-10-03）：首次单篇重采被偶发空页卡在 **17/44 页**，
    同一页 10 分钟后原样可取（无需改任何参数）。
    """
    state = {"p2_calls": 0}

    def _get(url, timeout=30):
        pi = int(re.search(r"page_index=(\d+)", url).group(1))
        if pi == 2:
            state["p2_calls"] += 1
            content = "" if state["p2_calls"] == 1 else "第二页正文"   # 第一次空，之后正常
        else:
            content = "第%d页正文" % pi
        return json.dumps({"data": {"notice_content": content, "page_size": 3}})

    monkeypatch.setattr(ri, "_http_get", _get)
    stats = []
    body = ri.fetch_notice_body("AN1", stats_sink=stats)
    assert body == "第1页正文\n第二页正文\n第3页正文", "偶发空页不得截断整篇"
    assert state["p2_calls"] == 2
    assert stats[0]["retries"] == 1
    assert "page_size=3" in stats[0]["stop_reason"]


def test_fetch_notice_body_empty_page_terminal_after_retries(monkeypatch):
    """真·空页：重试次数用尽后仍是空 ⇒ 停止，并在终止原因里写明重试过（可观测）。"""
    calls = []
    monkeypatch.setattr(ri, "_http_get",
                        _pager({1: "第1页", 2: ""}, page_size=2, calls=calls))
    stats = []
    body = ri.fetch_notice_body("AN1", stats_sink=stats)
    assert body == "第1页"
    assert calls == [1, 2, 2, 2], "应重试 %d 次，实际 %s" % (ri.EMPTY_RETRIES, calls)
    assert "重试 2 次仍为空" in stats[0]["stop_reason"]
    assert stats[0]["retries"] == 2


def test_fetch_notice_body_respects_max_pages(monkeypatch):
    """终止条件④：安全上限生效（上游 `page_size` 撒谎时不会无限翻）。"""
    monkeypatch.setattr(ri, "_http_get",
                        _pager({i: "第%d页" % i for i in range(1, 6)}, page_size=99))
    stats = []
    body = ri.fetch_notice_body("AN1", stats_sink=stats, max_pages=2)
    assert body == "第1页\n第2页"
    assert "安全上限" in stats[0]["stop_reason"]


def test_fetch_notice_body_single_page_notice_costs_one_request(monkeypatch):
    """小公告（page_size=1）只发 1 次请求 —— 翻页不得给常规公告加成本。"""
    calls = []
    monkeypatch.setattr(ri, "_http_get",
                        _pager({1: "关于召开业绩说明会的公告正文"}, page_size=1, calls=calls))
    body = ri.fetch_notice_body("AN1")
    assert body == "关于召开业绩说明会的公告正文"
    assert calls == [1]


def test_fetch_notice_body_degrades_keeping_pages_on_midway_failure(monkeypatch):
    """终止条件⑤（硬约束）：中途失败 ⇒ **保留已取页**，不得整篇丢弃。"""
    monkeypatch.setattr(ri, "_http_get",
                        _pager({1: "第一页正文"}, page_size=44, fail_on=2))
    errs, stats = [], []
    body = ri.fetch_notice_body("AN1", error_sink=errs, stats_sink=stats)
    assert body == "第一页正文", "已取到的页必须保留"
    assert stats[0]["pages"] == 1 and "降级保留" in stats[0]["stop_reason"]
    assert errs and "OSError" in errs[0]


def test_fetch_notice_body_sleeps_between_pages_only(monkeypatch):
    """限速：只在「还要继续翻页」时休眠（N 页 = N-1 次），末页后不空等。"""
    slept = []
    monkeypatch.setattr(ri.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(ri, "_http_get", _pager({1: "A", 2: "B", 3: "C"}, page_size=3))
    ri.fetch_notice_body("AN1", page_sleep=0.3)
    assert slept == [0.3, 0.3], "实际 %s" % slept


def test_prune_stale_chunks_removes_blocks_and_vectors(tmp_path):
    """「按 doc 重采」块数变少时必须清掉高 seq 旧块 + 其向量（否则留脏块在索引里）。"""
    conn = rag_store.get_conn(str(tmp_path / "kb.db"))
    rag_store.ensure_schema(conn)
    doc_id = rag_store.upsert_document(conn, {
        "code": "600519", "source": "notice", "title": "半年报",
        "url": "https://x", "published_at": "2026-08-15"})
    chunks = [{"seq": i, "text": "块%d" % i, "is_table": False} for i in range(5)]
    ids = rag_store.insert_chunks(conn, doc_id, chunks)
    rag_store.save_embeddings(conn, ids, [[0.0] * EMBED_DIM] * 5)
    assert rag_store.stats(conn) == {"documents": 1, "chunks": 5, "embedded": 5,
                                     "schema_version": "2"}
    assert ri.prune_stale_chunks(conn, doc_id, 2) == 3
    st = rag_store.stats(conn)
    assert st["chunks"] == 2 and st["embedded"] == 2, "向量不得留下孤儿行"
    meta, matrix = rag_store.load_index(conn)
    assert len(meta) == 2 and matrix.shape == (2, EMBED_DIM)
    conn.close()


def test_main_art_code_reingests_single_document(monkeypatch, tmp_path):
    """CLI 契约：`--art-code` 只重采指定一篇（幂等键不变 ⇒ 不新增文档）。"""
    rows = [{"code": "600519", "source": "notice", "title": t, "url": "https://" + t,
             "published_at": "2026-08-15", "art_code": a, "notice_type": "", "name": "贵州茅台"}
            for t, a in (("公告一", "A1"), ("公告二", "A2"))]
    monkeypatch.setattr(ri, "fetch_notices", lambda *a, **k: rows)

    def _stub_body(art, error_sink=None, stats_sink=None, **kw):
        if stats_sink is not None:
            stats_sink.append({"art_code": art, "pages": 3, "chars": 120,
                               "stop_reason": "stub", "reported_page_size": 3})
        return "翻页后的正文" * 40

    monkeypatch.setattr(ri, "fetch_notice_body", _stub_body)
    db = str(tmp_path / "kb.db")
    assert ri.main(["--art-code", "A2", "--db", db, "--no-embed", "--limit", "5"]) == 0
    conn = rag_store.get_conn(db)
    titles = [r[0] for r in conn.execute("SELECT title FROM documents")]
    conn.close()
    assert titles == ["公告二"], "只应落库被选中的那一篇，实际 %s" % titles


def test_main_art_code_not_found_is_no_data(monkeypatch, tmp_path, capsys):
    """找不到 art_code ⇒ RESULT: NO_DATA（退出码 2），不得静默采别的公告。"""
    monkeypatch.setattr(ri, "fetch_notices", lambda *a, **k: [])
    rc = ri.main(["--art-code", "NOPE", "--db", str(tmp_path / "kb.db"), "--no-embed"])
    assert rc == 2
    assert "NO_DATA" in capsys.readouterr().out


# ==================== C-R1（审计 C-F6/C-F8）：翻页终止边界加固 ====================
# 2026-10-03 两路审计指出两处边界（本次 20 篇未触发，但都是「静默截断」族）：
#   ① 「整页重复」一律停 ⇒ 合法文档相邻两页同文时，第 3 页起**从未被请求**（静默丢页）；
#   ② max_pages=60 硬截断时，DB 与逐篇日志都**没有截断标记**（库里像完整文档）。
# 修法：区分「文档内重复」（继续翻到 reported）与「越界/末尾重复」（安全停）；
#       新增 truncated/fetched_pages/duplicate_pages 随 --body-log 长存并逐篇外显。

def test_fetch_notice_body_internal_duplicate_page_continues_to_reported(monkeypatch):
    """★C-F6①：上游自称 3 页、第 2 页与第 1 页同文 ⇒ **第 3 页必须被请求**（不再静默丢页）。"""
    calls = []
    monkeypatch.setattr(ri, "_http_get",
                        _pager({1: "A页", 2: "A页", 3: "C页"}, page_size=3, calls=calls))
    stats = []
    body = ri.fetch_notice_body("AN1", stats_sink=stats)
    assert calls == [1, 2, 3], "文档内重复不得提前停止，实际 %s" % calls
    assert body == "A页\nC页", "重复页不重复拼进正文（与上一页逐字相同，信息零损失）"
    assert stats[0]["pages"] == 2 and stats[0]["fetched_pages"] == 3
    assert stats[0]["duplicate_pages"] == 1
    assert "page_size=3" in stats[0]["stop_reason"]
    assert stats[0]["truncated"] is False, "取到 reported 页数 ⇒ 未截断"


def test_fetch_notice_body_out_of_range_duplicate_still_safely_stops(monkeypatch):
    """★C-F6①：未声明总页数时的「相邻重复」仍是**末尾哨兵** ⇒ 安全停（原语义不变）。"""
    calls = []
    monkeypatch.setattr(ri, "_http_get",
                        _pager({1: "A页", 2: "B页", 3: "B页", 4: "C页"}, calls=calls))
    stats = []
    body = ri.fetch_notice_body("AN1", stats_sink=stats)
    assert body == "A页\nB页"
    assert calls == [1, 2, 3], "越界/末尾重复后不得继续翻"
    assert "整页重复（越界/末尾）" in stats[0]["stop_reason"], stats[0]["stop_reason"]
    assert stats[0]["truncated"] is False


def test_fetch_notice_body_truncated_flag_on_max_pages(monkeypatch):
    """★C-F6②：`max_pages` 硬截断必须**可观测**（truncated=True），不再「库里像完整文档」。"""
    monkeypatch.setattr(ri, "_http_get",
                        _pager({i: "第%d页" % i for i in range(1, 6)}, page_size=99))
    stats = []
    body = ri.fetch_notice_body("AN1", stats_sink=stats, max_pages=2)
    assert body == "第1页\n第2页"
    assert "安全上限" in stats[0]["stop_reason"]
    assert stats[0]["truncated"] is True


def test_fetch_notice_body_truncated_false_when_reported_reached(monkeypatch):
    """反例锁：正常取到 reported 页数时 **不得**误标 truncated（否则告警泛滥=无告警）。"""
    monkeypatch.setattr(ri, "_http_get", _pager({1: "第1页", 2: "第2页"}, page_size=2))
    stats = []
    ri.fetch_notice_body("AN1", stats_sink=stats)
    assert stats[0]["truncated"] is False
    assert stats[0]["fetched_pages"] == 2 and stats[0]["duplicate_pages"] == 0


def test_fetch_notice_body_truncated_true_on_midway_failure(monkeypatch):
    """中途请求失败 ⇒ 降级保留已取页，且**必须**标 truncated（正文不完整）。"""
    monkeypatch.setattr(ri, "_http_get",
                        _pager({1: "第一页正文"}, page_size=44, fail_on=2))
    stats = []
    body = ri.fetch_notice_body("AN1", stats_sink=stats)
    assert body == "第一页正文"
    assert stats[0]["truncated"] is True
    assert "降级保留" in stats[0]["stop_reason"]


def test_fetch_notice_body_total_timeout_degrades_keeping_pages(monkeypatch):
    """★C-F8：单篇全局时限（total_timeout_s）生效 ⇒ 降级保留已取页 + 可观测（不再最坏 90 分钟）。"""
    clock = {"t": 0.0}
    monkeypatch.setattr(ri.time, "monotonic", lambda: clock["t"])

    def _slow(url, timeout=30):
        pi = int(re.search(r"page_index=(\d+)", url).group(1))
        clock["t"] += 3.0                       # 每次请求耗时 3s
        return json.dumps({"data": {"notice_content": "第%d页" % pi, "page_size": 5}})

    monkeypatch.setattr(ri, "_http_get", _slow)
    stats = []
    body = ri.fetch_notice_body("AN1", stats_sink=stats, total_timeout_s=5)
    assert body == "第1页\n第2页", "总时限用尽前取到的页必须保留"
    assert "总时限 5s 用尽" in stats[0]["stop_reason"]
    assert stats[0]["truncated"] is True
    assert stats[0]["pages"] == 2


def test_fetch_notice_body_generous_default_total_timeout_does_not_fire(monkeypatch):
    """默认总时限必须**给足**：正常多页采集（真实时钟）不得被它打断（20 篇实测最慢 588.9s）。"""
    assert ri.TOTAL_TIMEOUT_S >= 1800, "默认总时限不得小于 30 分钟"
    monkeypatch.setattr(ri, "_http_get", _pager({1: "A", 2: "B", 3: "C"}, page_size=3))
    stats = []
    ri.fetch_notice_body("AN1", stats_sink=stats)
    assert stats[0]["truncated"] is False
    assert "总时限" not in stats[0]["stop_reason"]


def test_main_body_log_records_truncated_and_warns(monkeypatch, tmp_path, capsys):
    """★C-F6②：截断标记必须**随日志长存**（--body-log JSONL）+ 逐篇 stdout 可见。"""
    rows = [{"code": "600519", "source": "notice", "title": "公告一", "url": "https://a",
             "published_at": "2026-08-15", "art_code": "A1", "notice_type": "", "name": "贵州茅台"}]
    monkeypatch.setattr(ri, "fetch_notices", lambda *a, **k: rows)

    def _stub_body(art, error_sink=None, stats_sink=None, **kw):
        if stats_sink is not None:
            stats_sink.append({"art_code": art, "pages": 1, "chars": 10,
                               "stop_reason": "达到安全上限 max_pages=1",
                               "reported_page_size": 44, "retries": 0, "truncated": True})
        return "正文" * 20

    monkeypatch.setattr(ri, "fetch_notice_body", _stub_body)
    log = tmp_path / "body.jsonl"
    rc = ri.main(["--art-code", "A1", "--db", str(tmp_path / "kb.db"), "--no-embed",
                  "--limit", "5", "--body-log", str(log)])
    assert rc == 0
    rec = json.loads(log.read_text(encoding="utf-8").strip())
    assert rec["truncated"] is True, "JSONL 必须落 truncated"
    out = capsys.readouterr().out
    assert "⚠️ truncated" in out and "被截断" in out, out

# -*- coding: utf-8 -*-
"""公告**正文**采集回归锁（M1 采集层补全）。

背景（2026-09-16 实测）：
- akshare `stock_individual_notice_report` **只给标题**，语料太短 → 检索质量受限；
- 东财公告正文 API `np-cnotice-stock.eastmoney.com/api/content/ann?art_code=...`
  实测可直接拿到 `notice_content`（样本 1285 字，含「重要内容提示」等结构）。

因此采集层改为：东财列表 API + 正文 API；**正文拿不到时回退标题文本**（不静默丢公告）。
"""
import json

import pytest

from scripts import rag_ingest as ri


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

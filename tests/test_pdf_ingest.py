# -*- coding: utf-8 -*-
"""双轨语料对照实验（PDF 全文 vs API 正文）的纯逻辑单测 —— **不需要网络**。

背景：东财 `notice_content` 在 ~5000 字截断，完整全文只在 PDF 附件里；
计划见 `docs/M1_PDF_AB_PLAN.md`。本文件只锁两件可离线验证的事：
  ① 巨潮 `hisAnnouncement/query` 响应 → `(title, pdf_url, date)` 的解析（含"摘要"过滤）
  ② 跨语料答案判定用的**数字归一化**（千分位 / 单位后缀 / 全角）
"""
from scripts import rag_ingest_pdf as pdf_ing

# 固定 fixture：巨潮响应的**结构**（字段名取自 2026-10-01 实测响应）
_PAYLOAD = {
    "totalRecordNum": 2,
    "announcements": [
        {"announcementTitle": "贵州茅台2026年半年度报告",
         "adjunctUrl": "finalpage/2026-08-15/1225475868.PDF",
         "adjunctSize": 813, "announcementTime": 1786723200000, "secCode": "600519"},
        {"announcementTitle": "贵州茅台2026年半年度报告摘要",
         "adjunctUrl": "finalpage/2026-08-15/1225475860.PDF",
         "adjunctSize": 145, "announcementTime": 1786723200000, "secCode": "600519"},
    ],
}


def test_parse_cninfo_announcements_builds_absolute_pdf_url():
    """`adjunctUrl` 是相对路径（`finalpage/...`）→ 必须拼成 `static.cninfo.com.cn` 绝对 URL。"""
    rows = pdf_ing.parse_cninfo_announcements(_PAYLOAD)
    assert len(rows) == 2, "两条公告都应解析出来"
    first = rows[0]
    assert first["title"] == "贵州茅台2026年半年度报告"
    assert first["pdf_url"] == ("http://static.cninfo.com.cn/finalpage/2026-08-15/1225475868.PDF")
    assert len(first["date"]) == 10, "日期应是 YYYY-MM-DD（时区不做强断言）"


def test_parse_cninfo_announcements_can_drop_summary():
    """`drop_summary=True` 时剔除「…摘要」条目 —— 摘要是全文的**子集**，留着只是噪声。"""
    rows = pdf_ing.parse_cninfo_announcements(_PAYLOAD, drop_summary=True)
    assert len(rows) == 1, "应只剩 1 条（摘要被剔除）"
    assert "摘要" not in rows[0]["title"]


def test_parse_cninfo_announcements_handles_empty_payload():
    """空响应 / 缺字段都不得抛异常（真实接口会返回 `announcements: null`）。"""
    assert pdf_ing.parse_cninfo_announcements({}) == []
    assert pdf_ing.parse_cninfo_announcements({"announcements": None}) == []
    assert pdf_ing.parse_cninfo_announcements(
        {"announcements": [{"announcementTitle": "无附件公告"}]}) == []


def test_find_numbers_normalizes_for_cross_corpus_matching():
    """跨语料判定答案命中时，**chunk_id 不可对齐** ⇒ 只能用答案里的数字串做锚。

    归一化要处理的三种写法（实测自 A 股财报）：
      - 千分位：`1,234.56` → `1234.56`
      - 单位后缀：`16.74 亿元` → `16.74`
      - 不得把相邻数字粘成一个（`16.75 35.57` 应得两个）
    """
    assert pdf_ing.find_numbers("扣非净利润 16.74 亿元") == {"16.74"}
    assert pdf_ing.find_numbers("本期 1,234.56 万元") == {"1234.56"}
    assert pdf_ing.find_numbers("基本每股收益 16.75 稀释 35.57") == {"16.75", "35.57"}
    assert pdf_ing.find_numbers("无数字文本") == set()
    # 整数也应命中（如股本 / 股数）
    assert pdf_ing.find_numbers("总股本 1,256,197,800 股") == {"1256197800"}

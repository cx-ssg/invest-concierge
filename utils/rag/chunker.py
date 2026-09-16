# -*- coding: utf-8 -*-
"""金融文档切块器（表格整块保护）。

结构参考知识库 `co-planning/scripts/search_wiki.py:45 chunk_markdown()`
（按标题层级切 + 超长拗断，CHUNK_TARGET=600 / CHUNK_MAX=1200），**有意差异**：

1. **新增表格保护** —— 原实现只处理 markdown、无表格语义；财报表格被切断会
   导致数字串错行（`COVERAGE_DESIGN.md` §3.3 决策 2）。
2. **不设 overlap** —— 知识库实现本就无 overlap（靠段落/标题边界自然分隔），
   原设计写的「overlap 80」在复用方案下不适用，已在该文档显式废弃。
3. 返回 `list[dict]`（含 `seq` / `is_table`），便于直接落库。
"""
import re

CHUNK_TARGET = 600        # 目标块大小（硬拗断步长）
CHUNK_HARD_MAX = 1200     # 单块上限
_TABLE_PREFIX = "|"


def _is_table_row(line):
    s = line.strip()
    return s.startswith(_TABLE_PREFIX) and s.count(_TABLE_PREFIX) >= 2


def _split_segments(text):
    """按空行切段；纯标题段并入下一段（保证块自解释）。"""
    segs = []
    pending_title = None
    for seg in re.split(r"\n\s*\n", text or ""):
        s = seg.strip()
        if not s:
            continue
        lines = [ln for ln in s.splitlines() if ln.strip()]
        if lines and all(ln.lstrip().startswith("#") for ln in lines):
            pending_title = s
            continue
        if pending_title:
            s = pending_title + "\n" + s
            pending_title = None
        segs.append(s)
    if pending_title:
        segs.append(pending_title)
    return segs


TABLE_KEEP_HEADER_LINES = 2   # markdown 表头 = 标题行 + 分隔行


def _split_table_keeping_header(lines, max_len, header_lines=TABLE_KEEP_HEADER_LINES):
    """表格整块优先；超过 max_len 时按行拆分，**每段重复表头**。

    spec §4 第 2 条：表格可整块超 target，但不能无限 —— 分段后每段仍须自解释
    （否则片段无法判断列含义），所以表头必须跟着每一段走。
    """
    whole = "\n".join(lines)
    if len(whole) <= max_len:
        return [whole]
    header = lines[:header_lines]
    body = lines[header_lines:]
    header_len = len("\n".join(header)) + 1
    out, cur, cur_len = [], [], header_len
    for ln in body:
        add = len(ln) + 1
        if cur and cur_len + add > max_len:
            out.append("\n".join(header + cur))
            cur, cur_len = [], header_len
        cur.append(ln)
        cur_len += add
    if cur:
        out.append("\n".join(header + cur))
    return out


def chunk_document(text, target=CHUNK_TARGET, hard_max=CHUNK_HARD_MAX):
    """把文档切成块，返回 `[{"seq": int, "text": str, "is_table": bool}]`。

    规则：
    - 连续表格行合成**一个**表格块，不切碎；超 `hard_max*3` 时按行拆分并重复表头；
    - 非表格段落：**聚合到 `target` 附近再成块**（短段不单独成块）；
    - 单段超 `hard_max` → 按 `target` 步长硬拗断。

    ⚠️ **聚合是必需的**（2026-09-16 实测发现）：公告正文是「一行一句 + 空行分隔」格式，
    旧实现「每段独立成块」→ 814 块的中位长度只有 **28 字符**（p25=12、min=1），
    而设计目标是 600 字。碎片块会同时损害三处：
    ＊检索质量（短块没有上下文，命中了也看不出在说什么）；
    ＊BM25 文档长度归一化（dl 方差极大 → 分数被扭曲）；
    ＊向量嵌入语义（短文本的表示本就弱）。
    """
    if not text or not str(text).strip():
        return []

    chunks = []
    buf, buf_len = [], 0

    def flush():
        nonlocal buf, buf_len
        if buf:
            chunks.append({"text": "\n\n".join(buf), "is_table": False})
            buf, buf_len = [], 0

    for seg in _split_segments(str(text)):
        lines = seg.splitlines()
        table_rows = [ln for ln in lines if _is_table_row(ln)]
        is_table = len(table_rows) >= 2 and len(table_rows) >= len(lines) - 1

        if is_table:
            flush()                                   # 表格前先结算普通缓冲
            for tc in _split_table_keeping_header(lines, hard_max * 3):
                chunks.append({"text": tc, "is_table": True})
            continue

        if len(seg) > hard_max:
            flush()
            for i in range(0, len(seg), target):
                chunks.append({"text": seg[i:i + target], "is_table": False})
            continue

        if buf_len and buf_len + len(seg) + 2 > hard_max:
            flush()
        buf.append(seg)
        buf_len += len(seg) + 2
        if buf_len >= target:
            flush()

    flush()

    for i, c in enumerate(chunks):
        c["seq"] = i
        c["token_len"] = len(c["text"])
    return chunks

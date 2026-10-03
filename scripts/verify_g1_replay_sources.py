# -*- coding: utf-8 -*-
"""G1 验收断言（**当前必红**）：历史回放的每条消息都应带 `sources`。

这是任务 G1 的「红」侧证据，也是落地方案通过后直接可用的验收 harness：

    历史回放 = GET /api/agent/sessions/{id}/messages（前端唯一入口）
    期望：助手消息带 `sources`（与 SSE `tool_end.sources` 同构），
          前端才能像新消息那样渲染「来源卡」+ `[n]` 上标回跳。

现状（2026-10-03 实测，见 report-G1.md §根因）：`agent_messages` 表
只有 (id, session_id, role, content, created_at)，**没有**任何列存 sources，
`session_messages()` 也只回 `{role, content}` ⇒ 本断言必红。

用法：
    python scripts/verify_g1_replay_sources.py            # 自动挑最新一条「有检索」的会话
    python scripts/verify_g1_replay_sources.py --session 174
退出码：0 = 绿（历史回放带 sources）；1 = 红（未落库）。
"""
import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from data.database import DB_FILE  # noqa: E402
from services.agent_service import session_messages  # noqa: E402


def newest_session_with_retrieval():
    """挑一个「真的检索过」的会话：tool 行里出现 chunk_id 的最新会话。"""
    conn = sqlite3.connect(DB_FILE)
    try:
        row = conn.execute(
            "select session_id from agent_messages "
            "where role='tool' and content like '%chunk_id%' "
            "order by id desc limit 1").fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", type=int, default=None)
    args = ap.parse_args()

    sid = args.session or newest_session_with_retrieval()
    if sid is None:
        print("[G1] 语料库里没有「检索过」的会话，先跑一条带检索的提问再验。")
        return 2
    msgs = session_messages(sid)
    print("[G1] 会话 #%s：历史回放 %d 条，逐条键 = %s"
          % (sid, len(msgs), [sorted(m.keys()) for m in msgs]))

    cited = [m for m in msgs if m.get("role") == "assistant" and "[" in m.get("content", "")]
    with_sources = [m for m in msgs if "sources" in m]
    print("[G1] 正文含引用编号的助手消息 = %d 条；带 sources 键的消息 = %d 条"
          % (len(cited), len(with_sources)))

    if not cited:
        print("[G1] RESULT: SKIP（该会话没有引用编号，判据不适用）")
        return 2
    if not with_sources:
        print("[G1] RESULT: RED —— 历史回放的助手消息**没有** sources 键：")
        print("      前端拿不到来源 ⇒ 切回旧会话时来源卡必然缺失（G1 缺陷）。")
        return 1
    print("[G1] RESULT: GREEN —— 历史回放带 sources（首条 %s）"
          % with_sources[0].get("sources"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""A2 验收 V5：**真实 SSE 事件流 dump**（真调 `services.agent_service.stream_events`）。

任务书 `task-A2.md` §5 要求：真实提问（如「贵州茅台最近公告说了什么」），把事件序列 JSON
落盘到 `D:/Vault/Handoff/itt-20261002/events-A2.json`，并打印每条 `tool_end` 的 `sources` 条数。

⚠️ 这是一条**真联网**路径：真 LLM（读设置页配置的 Key）+ 真工具（`retrieve_docs` 打本地 kb.db
+ Ollama bge-m3 向量）。它不是单测，属于端到端验收 —— 「单测全绿 ≠ 生产路径可达」是本仓
连续三次踩过的坑，所以这一步必须真跑。

用法：
    python scripts/e2e_citation_dump.py                       # 默认问题 + 默认落盘路径
    python scripts/e2e_citation_dump.py --question "..." --out D:/path/events.json --no-cleanup

⚠️ 落盘路径默认是任务书指定的 `D:/Vault/...`；若该路径不可写（沙箱/权限），
   自动回退到仓库根目录同名文件，并**明确打印实际落盘路径**（不静默）。

## A2-R1 增补（task-A2R1.md §3 V3/V4）

- **V3 引用编号核对（本脚本自己判，不只 grep 到 `[1]` 就算过）**：
  从所有 `tool_end.sources` 汇总「可引用编号集合」（`rank`），再要求回答里的每个 `[n]`
  **都落在该集合内**；一个编号都没有 ⇒ FAIL（A2 首次真跑的失败形态）。
  判定结果打印为 `[V3] 判定：PASS/FAIL`，FAIL 时退出码 **2**（落盘仍照做，便于留证）。
- **V4 副作用清理**：本次运行会落库一条会话（`agent_sessions` + `agent_messages`）⇒
  跑完默认**删除该会话**并打印删除命令/结果/前后行数；`--no-cleanup` 可保留（排查用）。
"""
import argparse
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = "D:/Vault/Handoff/itt-20261002/events-A2.json"
FALLBACK_OUT = os.path.join(REPO_ROOT, "events-A2.json")


def _db_counts():
    """当前 `agent_sessions` / `agent_messages` 行数 —— V4 副作用核对用（跑完必须回到运行前）。"""
    from data.database import get_conn
    conn = get_conn()
    try:
        s = conn.execute("SELECT COUNT(*) FROM agent_sessions").fetchone()[0]
        m = conn.execute("SELECT COUNT(*) FROM agent_messages").fetchone()[0]
    finally:
        conn.close()
    return int(s), int(m)


def _dump(events, out_path, meta):
    """落盘事件序列；目标路径不可写时回退到仓库内同名文件（打印实际路径，不静默）。"""
    payload = dict(meta)
    payload["events"] = events
    blob = json.dumps(payload, ensure_ascii=False, indent=2)

    tried = [out_path]
    if out_path != FALLBACK_OUT:
        tried.append(FALLBACK_OUT)
    last_err = None
    for path in tried:
        try:
            parent = os.path.dirname(os.path.abspath(path))
            if parent and not os.path.isdir(parent):
                os.makedirs(parent, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(blob)
            print("[dump] 实际落盘：{}（{} 字节）".format(path, len(blob.encode("utf-8"))))
            if path != out_path:
                print("[dump] ⚠️ 目标路径不可写，已回退（原目标：{}）".format(out_path))
            return path
        except OSError as e:  # noqa: PERF203 - 逐路径尝试是本函数的目的
            last_err = e
            print("[dump] ⚠️ 写入失败：{} → {}".format(path, e))
    raise SystemExit("BLOCKED: 事件流 dump 无法落盘：{}".format(last_err))


def main():
    ap = argparse.ArgumentParser(description="A2-R1 V3 真实 SSE 事件流 dump + 引用编号核对")
    ap.add_argument("--question", default="贵州茅台最近公告说了什么", help="真实提问")
    ap.add_argument("--out", default=DEFAULT_OUT, help="落盘路径")
    ap.add_argument("--model", default=None, help="模型名（默认取配置）")
    ap.add_argument("--no-cleanup", action="store_true",
                    help="保留本次运行落库的会话（默认删除，见 A2-R1 V4）")
    args = ap.parse_args()

    from services.agent_service import stream_events

    counts_before = _db_counts()
    print("[run] 提问：{}".format(args.question))
    print("[run] 运行前 DB：agent_sessions={} agent_messages={}".format(*counts_before))
    t0 = time.time()
    events = []
    for ev in stream_events(args.question):
        events.append(ev)
        # 实时打印关键事件，便于人眼看着链条动起来
        t = ev.get("type")
        if t == "tool_start":
            print("  → tool_start {}".format(ev.get("name")))
        elif t == "tool_end":
            print("  ← tool_end   {} ok={} elapsed_ms={} sources={}".format(
                ev.get("name"), ev.get("ok"), ev.get("elapsed_ms"),
                len(ev.get("sources") or []) if "sources" in ev else "（无该键）"))
        elif t == "memory_used":
            print("  · memory_used {}".format(ev.get("sources")))
        elif t == "error":
            print("  ✗ error {}".format(ev.get("message")))
    elapsed = round(time.time() - t0, 2)

    # ---- 汇总：每条 tool_end 的 sources 条数与首条真实值 ----
    print("\n===== tool_end 汇总 =====")
    ends = [e for e in events if e.get("type") == "tool_end"]
    if not ends:
        print("（本次运行没有任何 tool_end 事件 —— 模型没调工具，或调用因故未发生）")
    for e in ends:
        n = len(e["sources"]) if "sources" in e else None
        print("- {}: ok={} evidence_level={} sources={}".format(
            e.get("name"), e.get("ok"), e.get("evidence_level", "（无该键）"),
            "（无该键）" if n is None else n))
        for s in (e.get("sources") or [])[:5]:
            print("    [{}] chunk_id={} published_at={} source={}\n        title={}\n        url={}".format(
                s.get("rank"), s.get("chunk_id"), s.get("published_at"), s.get("source"),
                s.get("title"), s.get("url")))

    done = [e for e in events if e.get("type") == "done"]
    session_id = done[0].get("session_id") if done else None
    content = (done[0].get("content") or "") if done else ""

    # ---- V3：引用编号核对（task-A2R1 §3）----
    # 「可引用编号集合」= 所有 tool_end.sources 的 rank（跨多次检索取并集）。
    # 判据：① 回答里至少 1 个 [n]；② 每个 n 都落在该集合内（[3] 而只有 5 条 ⇒ 过；[9] ⇒ 不过）。
    citable = {}
    for e in ends:
        for s in (e.get("sources") or []):
            r = s.get("rank")
            if isinstance(r, int) and r > 0 and r not in citable:
                citable[r] = s
    marks = sorted({int(m) for m in re.findall(r"\[(\d+)\]", content)})
    out_of_range = [m for m in marks if m not in citable]
    no_credential = [m for m in marks
                     if m in citable and not (citable[m].get("url") or citable[m].get("title"))]
    citation_ok = bool(marks) and not out_of_range

    print("\n===== V3 引用编号核对（task-A2R1 §3）=====")
    print("可引用来源编号集合（tool_end.sources 的 rank）：{}".format(sorted(citable) or "（无）"))
    print("回答里出现的引用编号：{}".format(marks or "（无）"))
    for m in marks:
        s = citable.get(m) or {}
        print("  [{}] title={} published_at={} url={}".format(
            m, s.get("title"), s.get("published_at"), s.get("url")))
    if content:
        print("[done] 回答前 200 字：{}".format(content[:200].replace("\n", " ")))
    if not marks:
        print("[V3] ❌ 回答里没有任何 [n] ⇒ 前端上标通路不会被触发（A2 的原始缺陷形态）")
    if out_of_range:
        print("[V3] ❌ 越界编号：{}（有效编号 {}）".format(out_of_range, sorted(citable)))
    if no_credential:
        print("[V3] ⚠️ 被引编号缺 url/title（不可引用档位？）：{}".format(no_credential))
    print("[V3] 判定：{}".format("PASS" if citation_ok else "FAIL"))

    out_path = _dump(events, args.out, {
        "task": "A2-R1 V3 · 真实 SSE 事件流 dump",
        "question": args.question,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_sec": elapsed,
        "event_count": len(events),
        "session_id": session_id,
        "event_types": [e.get("type") for e in events],
        "citation_marks": marks,
        "citable_ranks": sorted(citable),
        "citation_check": "PASS" if citation_ok else "FAIL",
    })
    print("[done] session_id={} 耗时 {}s 事件数 {}".format(session_id, elapsed, len(events)))
    print("[done] dump 路径：{}".format(out_path))

    # ---- V4：副作用清理（task-A2R1 §3 V4）----
    print("\n===== V4 副作用清理 =====")
    if not session_id:
        print("本次运行未产生 session_id ⇒ 无需清理")
    elif args.no_cleanup:
        print("--no-cleanup：保留会话 session_id={}（DB 未回到运行前状态，属预期）".format(session_id))
    else:
        from data.database import delete_agent_session
        print("删除命令：python -c \"from data.database import delete_agent_session; "
              "delete_agent_session({})\"".format(session_id))
        ok = delete_agent_session(int(session_id))
        counts_after = _db_counts()
        print("delete_agent_session({}) -> {}".format(session_id, ok))
        print("agent_sessions：{} → {}；agent_messages：{} → {}".format(
            counts_before[0], counts_after[0], counts_before[1], counts_after[1]))
        if counts_after == counts_before:
            print("[V4] DB 已回到运行前状态 ✅")
        else:
            print("[V4] ⚠️ DB 未回到运行前状态（运行前 {}，清理后 {}）".format(counts_before, counts_after))
            citation_ok = False

    return 0 if citation_ok else 2


if __name__ == "__main__":
    sys.exit(main())

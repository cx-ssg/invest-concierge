# -*- coding: utf-8 -*-
"""B1 验收 V3：**真跑** —— 真实提问 → SSE 事件流（带时间戳）→ 证明三件事。

任务书 `task-B1.md` §2 V3：
  「真跑：真实提问 → 事件流 dump｜证明『`done` 先到、`evidence_judged` 后到』，
    且**回答时延不增加**（开/关判官对比 `done` 时刻）」

本脚本做**两次**真实运行（同一条提问、同一份 kb、真 LLM、真工具）：

1. `judge=on`：正常产线路径（`JUDGE_ENABLED=True`）；
2. `judge=off`：`JUDGE_ENABLED=False` 的对照组 —— 用来回答"判官有没有拖慢回答"
   （判官在 `done` **之后**才跑，故 `done` 时刻之差应当只是 LLM 抖动）。

判定：
- `done` 必须存在；
- `evidence_judged` 必须**在 `done` 之后**（顺序 + 时间戳双重证据）；
- `judge=off` 时**不得**出现 `evidence_judged`（对照组必须是干净的）；
- 两次 `done` 时刻的差值**如实报出**（不说"零成本"，只说"不在回答链路里"）。

⚠️ 真联网路径：真 LLM（设置页/`.env` 的 Key）+ 真工具（本地 `kb.db` + Ollama 向量）。
落盘：`--out` > `AUDIT_OUT_DIR` > 系统临时目录（**不默认写仓库根**）。
用法：`python scripts/e2e_judge_dump.py [--question "..."] [--out path] [--no-cleanup]`
"""
import argparse
import json
import os
import sys
import tempfile
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

OUT_DIR = os.environ.get("AUDIT_OUT_DIR") or tempfile.gettempdir()
DEFAULT_OUT = os.path.join(OUT_DIR, "events-B1-judge.json")


def _db_counts():
    from data.database import get_conn
    conn = get_conn()
    try:
        s = conn.execute("SELECT COUNT(*) FROM agent_sessions").fetchone()[0]
        m = conn.execute("SELECT COUNT(*) FROM agent_messages").fetchone()[0]
    finally:
        conn.close()
    return int(s), int(m)


def _run_once(question, *, enable_judge):
    """跑一次真实 SSE，逐事件记录**相对时刻**；返回运行记录。"""
    from services import agent_service, judge_service

    judge_service.JUDGE_ENABLED = enable_judge
    t0 = time.time()
    events = []
    for ev in agent_service.stream_events(question):
        events.append({"t": round(time.time() - t0, 3), **ev})
    wall = round(time.time() - t0, 3)

    def _first(kind):
        for e in events:
            if e.get("type") == kind:
                return e
        return None

    done, judged = _first("done"), _first("evidence_judged")
    return {
        "judge_enabled": enable_judge,
        "wall_sec": wall,
        "event_types": [e.get("type") for e in events],
        "done_t": done["t"] if done else None,
        "done_session_id": (done or {}).get("session_id"),
        "done_content_head": ((done or {}).get("content") or "")[:120],
        "evidence_judged_t": judged["t"] if judged else None,
        "evidence_judged": judged,
        "events": events,
    }


def main():
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="B1 V3 真实 SSE 事件流 dump（判官开/关对照）")
    ap.add_argument("--question", default="贵州茅台最近公告说了什么", help="真实提问")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--repeat", type=int, default=3,
                    help="开/关对照重复次数（**单次差值会被 LLM 抖动支配**，故重复取中位数）")
    ap.add_argument("--no-cleanup", action="store_true")
    args = ap.parse_args()

    before = _db_counts()
    print("[V3] 提问：{}".format(args.question))
    print("[V3] 运行前 DB：agent_sessions={} agent_messages={}".format(*before))

    runs = []
    for i in range(max(1, args.repeat)):
        on = _run_once(args.question, enable_judge=True)
        off = _run_once(args.question, enable_judge=False)
        runs.append((on, off))
        gap = ((on["evidence_judged_t"] - on["done_t"])
               if (on["evidence_judged_t"] is not None and on["done_t"] is not None) else None)
        ev = on.get("evidence_judged") or {}
        print("\n[V3] #{}/{} judge=on  事件序列：{}".format(i + 1, args.repeat, on["event_types"]))
        print("[V3]      done@{:.2f}s  evidence_judged@{}  判官 latency_ms={} level={} checked={}".format(
            on["done_t"] or -1,
            ("%.2fs" % on["evidence_judged_t"]) if on["evidence_judged_t"] is not None else "（无）",
            ev.get("latency_ms"), ev.get("level"), ev.get("checked")))
        print("[V3]      items={}".format(
            [(x.get("chunk_id"), x.get("verdict"), bool(x.get("quote_rejected")))
             for x in (ev.get("items") or [])]))
        print("[V3]      差值(done) = on{:.2f}s − off{:.2f}s = {:+.2f}s；"
              "判官自身耗时(evidence_judged−done) = {}".format(
                  on["done_t"] or 0, off["done_t"] or 0,
                  ((on["done_t"] or 0) - (off["done_t"] or 0)),
                  ("%.2fs" % gap) if gap is not None else "（无）"))
        if i == 0:
            print("[V3]      done 回答前 120 字：{}".format(on["done_content_head"].replace("\n", " ")))

    # ---- 判定 ----
    ons = [r[0] for r in runs]
    offs = [r[1] for r in runs]
    order_ok = all(
        on["done_t"] is not None and on["evidence_judged_t"] is not None
        and "done" in on["event_types"] and "evidence_judged" in on["event_types"]
        and on["event_types"].index("done") < on["event_types"].index("evidence_judged")
        and on["done_t"] < on["evidence_judged_t"]
        for on in ons)
    off_clean = all("evidence_judged" not in off["event_types"] for off in offs)
    deltas = [round((on["done_t"] or 0) - (off["done_t"] or 0), 3) for on, off in runs]
    deltas_sorted = sorted(deltas)
    med = deltas_sorted[len(deltas_sorted) // 2] if deltas_sorted else None

    # 结构性证据：判官**在 done 之后**才开始 ⇒ (evidence_judged−done) ≥ 判官自身 latency。
    # 若判官在 done 之前就跑，这个差值会**小于** latency_ms（它被回答链路吃掉了）。
    gaps = []
    for on in ons:
        ev = on.get("evidence_judged") or {}
        if on["evidence_judged_t"] is not None and on["done_t"] is not None:
            gaps.append({"gap": round(on["evidence_judged_t"] - on["done_t"], 3),
                         "latency": round((ev.get("latency_ms") or 0) / 1000.0, 3),
                         "bound_ok": (on["evidence_judged_t"] - on["done_t"])
                                     >= (ev.get("latency_ms") or 0) / 1000.0 * 0.9})
    bound_ok = bool(gaps) and all(g["bound_ok"] for g in gaps)

    print("\n===== V3 判定 =====")
    print("[V3] ① done 先到、evidence_judged 后到：{}（{}/{} 次顺序+时间戳均成立）".format(
        "PASS" if order_ok else "FAIL", sum(
            1 for on in ons if "done" in on["event_types"] and "evidence_judged" in on["event_types"]
            and on["event_types"].index("done") < on["event_types"].index("evidence_judged")), len(ons)))
    print("[V3] ② 对照 judge=off 全程无 evidence_judged：{}".format("PASS" if off_clean else "FAIL"))
    print("[V3] ③ 结构性证据（判官在 done **之后**才起跑）：{} —— "
          "每次 (evidence_judged−done) 都 ≥ 判官 latency_ms/1000（低了就说明判官挤进了回答链路）".format(
              "PASS" if bound_ok else "FAIL"))
    for g in gaps:
        print("[V3]      gap={:.2f}s  judge_latency={:.2f}s  bound_ok={}".format(
            g["gap"], g["latency"], g["bound_ok"]))
    print("[V3] ④ done 时刻差（on−off）：{}s；中位数 {}s（n={}）".format(
        deltas, med, len(deltas)))
    print("[V3]      ⚠️ 该差值受 LLM 抖动支配（同一提问逐次本身有秒级波动），"
          "**不作\"零成本\"声明**；真正的保证是 ① 的顺序与 ③ 的结构。")

    # ---- 落盘 ----
    payload = {
        "task": "B1 V3 · 真实 SSE 事件流 dump（判官开/关对照）",
        "question": args.question,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "runs": [{"judge_on": on, "judge_off": off} for on, off in runs],
        "verdict": {"order_ok": order_ok, "off_clean": off_clean,
                    "gap_bound_ok": bound_ok,
                    "done_delta_sec": deltas, "done_delta_median_sec": med,
                    "gaps": gaps},
    }
    blob = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    path = args.out
    try:
        parent = os.path.dirname(os.path.abspath(path))
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(blob)
    except OSError as e:
        path = os.path.join(tempfile.gettempdir(), os.path.basename(args.out))
        with open(path, "w", encoding="utf-8") as f:
            f.write(blob)
        print("[V3] ⚠️ 目标不可写（{}）→ 回退 {}".format(e, path))
    print("[V3] dump -> {}（{} 字节）".format(path, len(blob.encode("utf-8"))))

    # ---- 副作用清理（每次运行各落一条会话）----
    print("\n===== 副作用清理 =====")
    sids = [on["done_session_id"] for on in ons] + [off["done_session_id"] for off in offs]
    sids = [s for s in sids if s]
    if args.no_cleanup:
        print("[V3] --no-cleanup：保留会话 {}".format(sids))
    else:
        from data.database import delete_agent_session
        for sid in sids:
            print("[V3] delete_agent_session({}) -> {}".format(
                sid, delete_agent_session(int(sid))))
        after = _db_counts()
        print("[V3] agent_sessions：{} → {}；agent_messages：{} → {}".format(
            before[0], after[0], before[1], after[1]))
        print("[V3] DB 回到运行前状态：{}".format("YES" if after == before else "NO"))

    ok = order_ok and off_clean and bound_ok
    print("\n[V3] RESULT: {}".format("PASS" if ok else "FAIL"))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())

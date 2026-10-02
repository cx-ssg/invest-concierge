# -*- coding: utf-8 -*-
"""A2-R2 验收：真实浏览器（headless Edge + CDP）跑「新会话 / 已有会话」两条问答通路。

任务书 task-A2R2.md §3 要求：浏览器级前后对照，证明 BUG-001「新会话回答不显示」。
本脚本不吃单测、不吃 SSR 断言，走**完整生产路径**：真前端（vite dev）+ 真后端（uvicorn）
+ 真 LLM + 真 DOM 断言。

场景：
  NEW     ：打开 #/（新会话，activeId=null）→ 发问 → 起点即空会话
  CONTROL ：点侧栏历史会话（activeId != null）→ 发问（对照组，不得回归）

判据（全部来自真实 DOM / 真实后端 DB，非模拟）：
  - serverHasAssistantAnswer ：后端 DB 里确实落了助手回答（证明"回答生成了"）
  - answerVisible            ：该回答文本真的出现在**消息区** DOM 里（核心断言）
  - userBubbleVisible        ：用户气泡在消息区
  - runViewSeenDuringStream  ：流式过程中出现过运行视图（思考/工具链）
  - runViewVisibleAtEnd      ：流结束后运行视图仍在（BUG-001 的红/绿分界）
  - supCount / sourceCards   ：A2 引用上标 + 来源卡（V6）

用法：
    python scripts/verify_a2r2_chat_visible.py --scenario both --tag red
产物：verify-a2r2-<tag>.json + shot-a2r2-<tag>-<scenario>.png（仓库根目录）
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from websocket import create_connection

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = r"D:/work/python1/fund_agent"
FRONT = os.path.join(REPO, "frontend")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
BACKEND = "http://127.0.0.1:8000"
FRONTEND = "http://127.0.0.1:5173"
CDP_PORT = 9333


def no_proxy_opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_json(url, timeout=10):
    with no_proxy_opener().open(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def http_ok(url, timeout=3):
    try:
        with no_proxy_opener().open(url, timeout=timeout) as r:
            return r.status < 500
    except Exception:
        return False


def wait_http(url, seconds=90, label=""):
    t0 = time.time()
    while time.time() - t0 < seconds:
        if http_ok(url):
            print("[ok] %s up (%.1fs)" % (label or url, time.time() - t0))
            return True
        time.sleep(1.5)
    print("[!!] %s NOT up after %ds" % (label or url, seconds))
    return False


def start_backend():
    if http_ok(BACKEND + "/api/agent/config"):
        print("[ok] backend already running")
        return None
    log = open(os.path.join(REPO, "a2r2-backend.log"), "w", encoding="utf-8", errors="replace")
    p = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server.main:app",
         "--host", "127.0.0.1", "--port", "8000"],
        cwd=REPO, stdout=log, stderr=subprocess.STDOUT)
    wait_http(BACKEND + "/api/agent/config", 120, "backend")
    return p


def _fetch_json(url, timeout=3):
    with no_proxy_opener().open(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def start_edge(url):
    """起 headless Edge + CDP，并用**浏览器级 Target.createTarget** 建一个稳定的页面 target。

    直接吃启动时那个页面 target 会踩竞态（首个 tab 被替换 → WS 10054），
    所以走 browser endpoint 显式建 tab 再连它。
    """
    tmp = tempfile.mkdtemp(prefix="edge-a2r2-")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    p = subprocess.Popen(
        [EDGE, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
         "--remote-debugging-port=%d" % CDP_PORT, "--remote-allow-origins=*",
         "--user-data-dir=" + tmp,
         "--window-size=1600,1000", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=flags)
    ver = None
    t0 = time.time()
    while time.time() - t0 < 40:
        try:
            ver = _fetch_json("http://127.0.0.1:%d/json/version" % CDP_PORT)
            break
        except Exception:
            time.sleep(1)
    if not ver:
        print("[!!] edge cdp not reachable")
        return p, None
    print("[ok] edge cdp up (%.1fs)" % (time.time() - t0))
    browser = CDP(ver["webSocketDebuggerUrl"])
    for attempt in range(4):
        try:
            tid = browser.call("Target.createTarget", {"url": url}).get("result", {}).get("targetId")
            time.sleep(1.5)
            for t in _fetch_json("http://127.0.0.1:%d/json/list" % CDP_PORT):
                if t.get("id") == tid and t.get("webSocketDebuggerUrl"):
                    cdp = CDP(t["webSocketDebuggerUrl"])
                    cdp.call("Page.enable")
                    cdp.call("Runtime.enable")
                    print("[ok] attached page target %s (attempt %d)" % (tid, attempt + 1))
                    return p, cdp
        except Exception as ex:
            print("[warn] attach attempt %d failed: %s" % (attempt + 1, str(ex)[:120]))
        time.sleep(1.5)
    return p, None


class CDP:
    def __init__(self, ws_url):
        self.ws = create_connection(ws_url, timeout=60, suppress_origin=True)
        self.i = 0

    def call(self, method, params=None):
        self.i += 1
        mid = self.i
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                return msg

    def eval(self, expr):
        r = self.call("Runtime.evaluate",
                      {"expression": expr, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in r.get("result", {}):
            return {"__exception__": str(r["result"]["exceptionDetails"])[:400]}
        return r.get("result", {}).get("result", {}).get("value")

    def shot(self, path):
        r = self.call("Page.captureScreenshot", {"format": "png"})
        data = r.get("result", {}).get("data")
        if not data:
            return False
        Path(path).write_bytes(base64.b64decode(data))
        return True


JS_PROBE = r"""(() => {
  const btns = [...document.querySelectorAll('button')];
  const label = (t) => btns.some(b => b.innerText.trim() === t);
  const scroller = [...document.querySelectorAll('div')].find(
    d => d.className.includes('overflow-y-auto') && d.className.includes('flex-1'));
  const sups = [...document.querySelectorAll('sup button')];
  const anchors = [...document.querySelectorAll('[id^="src-"]')];
  return {
    msgText: scroller ? scroller.innerText : '',
    wholeText: document.body.innerText,
    hasStopBtn: label('停止'),
    hasSendBtn: label('发送'),
    streamingHints: /Agent 思考中|组织最终回答|生成中|分析中/.test(document.body.innerText),
    runViewHints: document.body.innerText.includes('工具链时间线') || document.body.innerText.includes('Agent 思考中'),
    supCount: sups.length,
    srcIds: anchors.map(a => a.id),
    activeSessionHeader: (document.body.innerText.match(/会话（新）|会话 #\d+/) || [''])[0]
  };
})()"""

JS_TYPE = """(() => {
  const ta = document.querySelector('textarea');
  if (!ta) return 'NO_TEXTAREA';
  const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
  setter.call(ta, %s);
  ta.dispatchEvent(new Event('input', { bubbles: true }));
  return 'TYPED';
})()"""

JS_SEND = """(() => {
  const b = [...document.querySelectorAll('button')].find(x => x.innerText.trim() === '发送');
  if (!b) return 'NO_SEND_BUTTON';
  if (b.disabled) return 'SEND_DISABLED';
  b.click();
  return 'SENT';
})()"""


def snapshot_sessions():
    """{sid: (assistant条数, 最后一条assistant内容)} —— 完成后对比增量定位「本次运行落库的回答」。"""
    snap = {}
    try:
        sess = http_json(BACKEND + "/api/agent/sessions") or []
    except Exception:
        return snap
    for s in sess:
        sid = s.get("id")
        if sid is None:
            continue
        try:
            msgs = http_json(BACKEND + "/api/agent/sessions/%s/messages" % sid) or []
        except Exception:
            continue
        a = [m for m in msgs if m.get("role") == "assistant" and (m.get("content") or "").strip()]
        snap[sid] = (len(a), (a[-1].get("content") if a else ""), len(msgs))
    return snap


def snippet_of(text, n=14):
    """取回答里一段有辨识度的连续中文（用于 DOM 包含断言）。"""
    runs = re.findall(r"[\u4e00-\u9fff，。、；：！？%0-9A-Za-z]{14,}", text or "")
    if not runs:
        return (text or "").strip()[:n]
    best = max(runs, key=len)
    return best[:n]


def run_scenario(cdp, scenario, question, snap_before, timeout, out_png):
    """执行一次问答，返回观测 dict。"""
    obs = {"scenario": scenario, "question": question, "steps": []}
    if scenario == "control":
        # 点侧栏历史会话（对照组：activeId != null）
        clicked = cdp.eval("""(() => {
          const items = [...document.querySelectorAll('div[role="button"]')]
            .filter(d => (d.className||'').includes('rounded-tile') && d.innerText.trim());
          const hist = items.filter(d => !['历史会话','回收站'].includes(d.innerText.trim()));
          if (!hist.length) return 'NO_SESSION_ITEM';
          const t = hist[0].innerText.trim();
          hist[0].click();
          return 'CLICKED:' + t;
        })()""")
        obs["steps"].append("click_session=%s" % clicked)
        time.sleep(2.5)
    p0 = cdp.eval(JS_PROBE) or {}
    obs["header_before"] = p0.get("activeSessionHeader")
    obs["steps"].append("header_before=%s" % obs["header_before"])

    typed = cdp.eval(JS_TYPE % json.dumps(question, ensure_ascii=False))
    obs["steps"].append("type=%s" % typed)
    time.sleep(1.2)
    sent = cdp.eval(JS_SEND)
    obs["steps"].append("send=%s" % sent)
    if sent != "SENT":
        obs["error"] = "send_failed:%s" % sent
        return obs

    # --- 等待后端真的落库助手回答（真完成信号，不靠 UI 自证）---
    t0 = time.time()
    grew_sid = None
    answer = None
    saw_stream = False
    while time.time() - t0 < timeout:
        p = cdp.eval(JS_PROBE) or {}
        if p.get("hasStopBtn") or p.get("streamingHints"):
            saw_stream = True
        snap = snapshot_sessions()
        for sid, (n_a, last_a, n_all) in snap.items():
            prev = snap_before.get(sid)
            prev_a = prev[0] if prev else 0
            if n_a > prev_a and last_a:
                grew_sid = sid
                answer = last_a
                break
        if answer is not None:
            time.sleep(2.0)  # 让客户端消费完 done 事件
            break
        time.sleep(1.0)
    obs["i_elapsed"] = round(time.time() - t0, 1)
    obs["server_session_id"] = grew_sid
    obs["saw_streaming"] = saw_stream
    obs["server_answer_len"] = len(answer or "")
    obs["server_answer_head"] = (answer or "")[:200]

    # --- 结束态 DOM 断言 ---
    pend = cdp.eval(JS_PROBE) or {}
    obs["header_after"] = pend.get("activeSessionHeader")
    obs["end_hasStopBtn"] = pend.get("hasStopBtn")
    obs["end_sendBtn"] = pend.get("hasSendBtn")
    obs["supCount"] = pend.get("supCount", 0)
    obs["sourceCards"] = pend.get("srcIds", [])
    obs["endWholeTextTail"] = (pend.get("wholeText") or "")[-500:]

    msg = pend.get("msgText") or ""
    obs["msg_region_text_len"] = len(msg)
    obs["msg_region_text"] = msg[:1500]
    obs["userBubbleVisible"] = question[:10] in msg
    snip = snippet_of(answer) if answer else ""
    obs["answer_snippet"] = snip
    obs["answerVisible"] = bool(snip) and snip in msg
    obs["answerVisibleInWholePage"] = bool(snip) and snip in (pend.get("wholeText") or "")
    obs["runViewVisibleAtEnd"] = bool(pend.get("supCount")) or bool(pend.get("srcIds")) \
        or ("工具链时间线" in msg) or ("来源" in msg)
    if len(obs["msg_region_text"]) < 600:
        obs["msg_region_text"] = msg  # 短则给全量，便于报告贴原样

    obs["screenshot"] = out_png
    obs["screenshot_saved"] = cdp.shot(out_png)
    return obs


def results_for(obs):
    checks = {}
    if obs.get("error"):
        checks["no_error"] = False
        return checks
    checks["server_has_assistant_answer"] = obs.get("server_answer_len", 0) > 0
    checks["user_bubble_visible"] = obs.get("userBubbleVisible") is True
    checks["answer_visible_in_message_region"] = obs.get("answerVisible") is True
    if obs.get("scenario") == "control":
        import re as _re
        checks["control_active_session_nonnull"] = bool(
            _re.match(r"会话 #\d+", obs.get("header_before") or ""))
    if obs.get("scenario") == "new":
        checks["new_session_started_null"] = obs.get("header_before") == "会话（新）"
    return checks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="both", choices=["new", "control", "both"])
    ap.add_argument("--tag", default="run", help="产物后缀（red / green）")
    ap.add_argument("--question", default="贵州茅台最近公告说了什么")
    ap.add_argument("--control-question", default="用一句话说明什么是基金定投")
    ap.add_argument("--timeout", type=int, default=240)
    ap.add_argument("--frontend", default=FRONTEND)
    ap.add_argument("--no-start-backend", action="store_true")
    args = ap.parse_args()

    front = args.frontend
    assert not front.endswith("/")
    res = {"tag": args.tag, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "frontend": front, "scenarios": {}}
    procs = []
    try:
        if not args.no_start_backend:
            b = start_backend()
            if b:
                procs.append(b)
        try:
            cfg = http_json(BACKEND + "/api/agent/config")
        except Exception as e:
            cfg = {"error": str(e)}
        res["agent_config"] = {k: v for k, v in (cfg or {}).items()
                               if k in ("api_key_configured", "demo_mode", "chat_model", "reasoner_model")}
        print("[cfg] %s" % json.dumps(res["agent_config"], ensure_ascii=False))

        sessions_before = [s.get("id") for s in (http_json(BACKEND + "/api/agent/sessions") or [])]
        res["sessions_before"] = sessions_before
        res["snapshot_before"] = {str(k): v[0] for k, v in snapshot_sessions().items()}
        snap_before = snapshot_sessions()

        e, cdp = start_edge(front + "/#/")
        procs.append(e)
        if cdp is None:
            res["error"] = "no cdp page"
            return finish(res, args)

        scenarios = ["new", "control"] if args.scenario == "both" else [args.scenario]
        for sc in scenarios:
            # 每个场景都重新加载页面：新会话场景必须保证 activeId=null（zustand 初始态）
            cdp.call("Page.navigate", {"url": front + "/#/"})
            time.sleep(3.5)
            t0 = time.time()
            while time.time() - t0 < 30:
                if cdp.eval("!!document.querySelector('textarea')"):
                    break
                time.sleep(1)
            if sc == "control":
                # 对照组用「已有会话」：先把新会话脏状态清掉（刷新已复位），再点历史会话
                pass
            q = args.question if sc == "new" else args.control_question
            png = os.path.join(REPO, "shot-a2r2-%s-%s.png" % (args.tag, sc))
            snap_before = snapshot_sessions()
            obs = run_scenario(cdp, sc, q, snap_before, args.timeout, png)
            obs["checks"] = results_for(obs)
            res["scenarios"][sc] = obs
            print("\n===== scenario=%s =====" % sc)
            print("steps=%s" % obs.get("steps"))
            print("server_session_id=%s server_answer_len=%s answer_snippet=%r"
                  % (obs.get("server_session_id"), obs.get("server_answer_len"), obs.get("answer_snippet")))
            print("header_before=%s header_after=%s" % (obs.get("header_before"), obs.get("header_after")))
            print("saw_streaming=%s runViewVisibleAtEnd=%s supCount=%s srcIds=%s"
                  % (obs.get("saw_streaming"), obs.get("runViewVisibleAtEnd"),
                     obs.get("supCount"), obs.get("sourceCards")))
            print("msgRegionText(first 300)=%r" % (obs.get("msg_region_text") or "")[:300])
            print("checks=%s" % json.dumps(obs["checks"], ensure_ascii=False))
    finally:
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
    return finish(res, args)


def finish(res, args):
    out = os.path.join(REPO, "verify-a2r2-%s.json" % args.tag)
    Path(out).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    allchecks = {}
    for sc, obs in res.get("scenarios", {}).items():
        for k, v in obs.get("checks", {}).items():
            allchecks["%s.%s" % (sc, k)] = v
    ok = bool(allchecks) and all(allchecks.values())
    print("\nRESULT=%s %s" % ("ALL_TRUE" if ok else "SEE_JSON",
                              json.dumps(allchecks, ensure_ascii=False)))
    print("json=%s" % out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

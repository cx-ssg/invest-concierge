# -*- coding: utf-8 -*-
"""B1 验收 V5：**真实浏览器**跑 —— 判官标注真的渲染出来（headless Edge + CDP）。

任务书 `task-B1.md` §2 V5：「前端 `npx tsc -b` + `npm run build` + 浏览器真跑截图｜
判官标注真的渲染出来」。

链路全是**真实**的（不吃单测、不吃 SSR）：

    vite build 产物 (frontend/dist) → FastAPI 托管（同源 /api）
      → 真 SSE（POST /api/agent/chat/stream，含 B1 的 `evidence_judged`）
      → 真 ChatArea 渲染来源卡上的 `[data-judge]` 标注
      → headless Edge 从 DOM 读回来 + 截图

判据（全部来自真实 DOM）：
  - `judgeBadges >= 1`：页面里真的出现了判官标注（`[data-judge]`）；
  - `labels` 全部落在「判官：相关 / 无关 / 未确认 / 未确认（引文未通过校验）」四态里；
  - `unsafe == 0`：**引文被拒 / 未确认的卡片不得**显示成「相关」（B1 的核心安全性质）。

用法：
    python scripts/verify_b1_judge_ui.py [--port 8010] [--question "..."]
产物：`b1-judge-ui.json` + `shot-b1-judge.png`（`AUDIT_OUT_DIR` 或系统临时目录）。
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from websocket import create_connection

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.environ.get("AUDIT_OUT_DIR") or tempfile.gettempdir()
EDGE = os.environ.get("B1_EDGE") or r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def _free_port():
    """挑一个空闲本地端口。

    ⚠️ 为什么不能写死：上一次 headless Edge 若没被收干净，会继续占着 CDP 端口，
    而它的浏览器 WS 端点已失效 ⇒ 新实例绑不上、脚本却连到**僵尸浏览器**上，
    表现为 `WinError 10054`（2026-10-03 实测踩到，误判成"环境不支持"）。
    """
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


CDP_PORT = int(os.environ.get("B1_CDP_PORT") or _free_port())


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_json(url, timeout=10):
    with _opener().open(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def http_ok(url, timeout=3):
    try:
        with _opener().open(url, timeout=timeout) as r:
            return r.status < 500
    except Exception:
        return False


def wait_http(url, seconds=90, label=""):
    t0 = time.time()
    while time.time() - t0 < seconds:
        if http_ok(url):
            print("[ok] %s up (%.1fs)" % (label or url, time.time() - t0))
            return True
        time.sleep(1.0)
    print("[!!] %s NOT up after %ds" % (label or url, seconds))
    return False


class CDP:
    def __init__(self, ws_url):
        self.ws = create_connection(ws_url, timeout=90, suppress_origin=True)
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


def start_edge(url):
    tmp = tempfile.mkdtemp(prefix="edge-b1-")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    p = subprocess.Popen(
        [EDGE, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
         "--remote-debugging-port=%d" % CDP_PORT, "--remote-allow-origins=*",
         "--user-data-dir=" + tmp,
         "--window-size=1600,1100", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    ver = None
    t0 = time.time()
    while time.time() - t0 < 40:
        try:
            ver = http_json("http://127.0.0.1:%d/json/version" % CDP_PORT)
            break
        except Exception:
            time.sleep(1)
    if not ver:
        print("[!!] edge cdp 不可达（headless Edge 起不来？）")
        return p, None
    print("[ok] edge cdp up (%.1fs)" % (time.time() - t0))
    browser = CDP(ver["webSocketDebuggerUrl"])
    for attempt in range(4):
        try:
            tid = browser.call("Target.createTarget", {"url": url}).get(
                "result", {}).get("targetId")
            time.sleep(1.5)
            for t in http_json("http://127.0.0.1:%d/json/list" % CDP_PORT):
                if t.get("id") == tid and t.get("webSocketDebuggerUrl"):
                    cdp = CDP(t["webSocketDebuggerUrl"])
                    cdp.call("Page.enable")
                    cdp.call("Runtime.enable")
                    cdp.call("Network.enable")
                    cdp.call("Network.setCacheDisabled", {"cacheDisabled": True})
                    print("[ok] attached page target %s（第 %d 次尝试）" % (tid, attempt + 1))
                    return p, cdp
        except Exception as ex:
            print("[warn] attach 第 %d 次失败：%s" % (attempt + 1, str(ex)[:120]))
        time.sleep(1.5)
    return p, None


JS_PROBE = r"""(() => {
  const cards = [...document.querySelectorAll('[id^="src-"]')].map(el => ({
    id: el.id,
    judge: el.querySelector('[data-judge]')?.getAttribute('data-judge') ?? null,
    judgeText: el.querySelector('[data-judge]')?.textContent?.trim() ?? null,
    title: (el.querySelector('span.flex-1')?.textContent || '').trim().slice(0, 40),
  }));
  return {
    judgeBadges: cards.filter(c => c.judge !== null).length,
    cards,
    text: document.body.innerText,
    hasJudgeWord: document.body.innerText.includes('判官：'),
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

#: 四态文案（服务端 `judge_service` + 前端 `judgeLabel` 的共同契约）
KNOWN_LABELS = ('判官：相关', '判官：无关', '判官：未确认', '判官：未确认（引文未通过校验）')


def main():
    ap = argparse.ArgumentParser(description="B1 V5 浏览器真跑：判官标注渲染")
    ap.add_argument("--port", type=int, default=8010)
    ap.add_argument("--question", default="贵州茅台最近公告说了什么")
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--sessions", type=int, default=20)
    args = ap.parse_args()

    base = "http://127.0.0.1:%d" % args.port
    page = base + "/#/"
    proc = None
    edge = None
    cdp = None
    created = []
    try:
        if not http_ok(base + "/api/agent/config"):
            log = open(os.path.join(OUT_DIR, "b1-ui-backend.log"), "w",
                       encoding="utf-8", errors="replace")
            print("[..] 起后端 uvicorn（server.main:app，托管 frontend/dist）@%d" % args.port)
            proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "server.main:app",
                 "--host", "127.0.0.1", "--port", str(args.port)],
                cwd=REPO, stdout=log, stderr=subprocess.STDOUT)
            wait_http(base + "/api/agent/config", 120, "backend")
        else:
            print("[ok] 后端已在本端口运行")

        before = {s.get("id") for s in (http_json(base + "/api/agent/sessions") or [])}

        edge, cdp = start_edge(page)
        if cdp is None:
            print("[V5] RESULT: FAIL（浏览器/CDP 不可用 —— 环境限制，非产品缺陷）")
            return 3
        time.sleep(3.0)
        p0 = cdp.eval(JS_PROBE) or {}
        print("[V5] 页面已加载：判官标注基线 = %s 条" % p0.get("judgeBadges"))

        typed = cdp.eval(JS_TYPE % json.dumps(args.question, ensure_ascii=False))
        time.sleep(1.2)
        sent = cdp.eval(JS_SEND)
        print("[V5] 提问=%s type=%s send=%s" % (args.question, typed, sent))
        if sent != "SENT":
            print("[V5] RESULT: FAIL（发不出去：%s）" % sent)
            return 2

        # 等判官标注出现（判官在 done 之后才到 ⇒ 这里等的是**迟到的第二段事件**）
        t0 = time.time()
        last = {}
        while time.time() - t0 < args.timeout:
            last = cdp.eval(JS_PROBE) or {}
            if last.get("judgeBadges", 0) > 0:
                break
            time.sleep(1.0)
        elapsed = round(time.time() - t0, 1)
        print("[V5] 轮询 %.1fs 后：判官标注 %s 条" % (elapsed, last.get("judgeBadges")))
        for c in last.get("cards", []):
            if c.get("judge"):
                print("      %s title=%s → %s (%s)" % (
                    c["id"], c["title"], c["judge"], c["judgeText"]))

        png = os.path.join(OUT_DIR, "shot-b1-judge.png")
        if cdp.shot(png):
            print("[V5] 截图 -> %s（%d 字节）" % (png, os.path.getsize(png)))

        after = http_json(base + "/api/agent/sessions") or []
        created = [s.get("id") for s in after if s.get("id") not in before]

        cards = [c for c in last.get("cards", []) if c.get("judge")]
        bad_labels = [c for c in cards if c.get("judgeText") not in KNOWN_LABELS]
        unsafe = [c for c in cards
                  if c.get("judge") in ("uncertain", "rejected")
                  and '相关' in (c.get("judgeText") or '')]
        badges_ok = len(cards) >= 1
        print("\n===== V5 判定 =====")
        print("[V5] ① 判官标注真的渲染出来：%s（%d 条 [data-judge]）" % (
            "PASS" if badges_ok else "FAIL", len(cards)))
        print("[V5] ② 文案落在四态契约内：%s（越界 %d 条）" % (
            "PASS" if not bad_labels else "FAIL", len(bad_labels)))
        print("[V5] ③ 未确认/引文被拒的卡片不得显示为「相关」：%s（违规 %d 条）" % (
            "PASS" if not unsafe else "FAIL", len(unsafe)))

        payload = {
            "task": "B1 V5 · 浏览器真跑：判官标注渲染（headless Edge + CDP）",
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "base_url": base, "question": args.question,
            "elapsed_sec": elapsed,
            "judge_badges": len(cards),
            "cards": last.get("cards", []),
            "has_judge_word": last.get("hasJudgeWord"),
            "created_sessions": created,
            "screenshot": png if os.path.exists(png) else None,
            "dom_text_tail": (last.get("text") or "")[-1200:],
            "verdict": {"badges_ok": badges_ok,
                        "labels_ok": not bad_labels,
                        "no_unsafe_relevant": not unsafe},
        }
        out = os.path.join(OUT_DIR, "b1-judge-ui.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        print("[V5] dump -> %s" % out)

        ok = badges_ok and not bad_labels and not unsafe
        print("[V5] RESULT: %s" % ("PASS" if ok else "FAIL"))
        return 0 if ok else 2
    finally:
        if cdp is not None:
            try:
                cdp.ws.close()
            except Exception:
                pass
        for p in (edge, proc):
            if p is not None:
                try:
                    p.terminate()
                except Exception:
                    pass
        for sid in created:
            try:
                req = urllib.request.Request(
                    base + "/api/agent/sessions/%d" % int(sid), method="DELETE")
                _opener().open(req, timeout=5).read()
                print("[V5] 清理会话 %s" % sid)
            except Exception as ex:
                print("[V5] 清理会话 %s 失败：%s" % (sid, str(ex)[:80]))


if __name__ == "__main__":
    sys.exit(main())

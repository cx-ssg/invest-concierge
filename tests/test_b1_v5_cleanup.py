# -*- coding: utf-8 -*-
"""B1 V5 验证脚本的会话清理（B-R1 · 审计 B-F8）。

旧实现把清理放在 `p.terminate()` **之后** ⇒ 后端已死，DELETE 只会拿到连接错误，
**会话残留而脚本仍报 PASS**。本文件锁三件事：

1. `cleanup_sessions()` 对**活着的**后端真的发 DELETE 且逐条回报 `ok`；
2. 后端不可达时**如实回报失败**（不静默、不假装成功）—— 结果要能进结果 JSON；
3. 脚本源码里"清理调用"出现在 `terminate()` **之前**（顺序锁 = B-F8 的缺陷形态）。

⚠️ 顺序锁是**静态**的（防"顺序被改回去"这一类回归）；真跑证据（真后端 + 真 DB +
无残留）见 `report-B-R1.md` §B-F8 的 harness 输出（headless Edge 在本沙箱起不来，
用假 CDP 层驱动同一段真代码）。
"""
import http.server
import importlib.util
import os
import re
import socket
import threading

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO, "scripts", "verify_b1_judge_ui.py")


def _load_mod():
    spec = importlib.util.spec_from_file_location("verify_b1_judge_ui_mod", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Handler(http.server.BaseHTTPRequestHandler):
    deletes = []

    def do_DELETE(self):  # noqa: N802 - http.server 的接口名
        type(self).deletes.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, *a):  # 静音（避免污染 pytest 输出）
        pass


@pytest.fixture()
def stub_backend():
    _Handler.deletes = []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield "http://127.0.0.1:%d" % srv.server_address[1], _Handler.deletes
    finally:
        srv.shutdown()
        srv.server_close()


def test_cleanup_sessions_deletes_and_reports_ok(stub_backend):
    """活着的后端 ⇒ 逐条 DELETE，结果带 `ok=True`（结果 JSON 的 cleanup 字段来源）。"""
    base, deletes = stub_backend
    mod = _load_mod()
    res = mod.cleanup_sessions(base, [131, 132])
    assert res == [{"session_id": 131, "ok": True}, {"session_id": 132, "ok": True}]
    assert deletes == ["/api/agent/sessions/131", "/api/agent/sessions/132"]


def test_cleanup_sessions_reports_failure_when_backend_is_down():
    """后端不可达 ⇒ **如实**回报失败（含原因），绝不静默当成清理成功。"""
    mod = _load_mod()
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()                       # 端口空着 = 连不上（模拟"terminate 之后再 DELETE"）
    res = mod.cleanup_sessions("http://127.0.0.1:%d" % port, [7])
    assert len(res) == 1
    assert res[0]["session_id"] == 7
    assert res[0]["ok"] is False
    assert res[0]["error"], "失败必须带原因（供结果 JSON 留痕）"


def test_cleanup_runs_before_backend_terminate():
    """**顺序锁**（B-F8 的缺陷形态）：清理调用必须排在 `terminate()` 之前。

    后端一停，DELETE 就只剩连接错误 ⇒ 会话残留。静态锁 + 上面的行为用例 +
    report 里的真跑 harness 输出三者合起来才是完整防线。

    ⚠️ 锚点只认**代码行**（行首缩进 + `p.terminate()` 独占一行）——`cleanup_sessions`
    的 docstring 里也出现了这个词（讲缺陷成因），不能用裸 `index("p.terminate()")`。
    """
    with open(SCRIPT, encoding="utf-8") as f:
        src = f.read()
    i_cleanup = src.index("cleanup = cleanup_sessions(base, created)")
    calls = [m for m in re.finditer(r"(?m)^[ \t]*p\.terminate\(\)[ \t]*$", src)]
    assert calls, "脚本里找不到 `p.terminate()` 的代码行（锚点失效，锁会静默失灵）"
    assert i_cleanup < calls[0].start(), (
        "会话清理必须排在 terminate 之前（B-F8：后端已停时 DELETE 只会拿到连接错误）")

# -*- coding: utf-8 -*-
"""M0 服务层单测：缓存锁 / to_jsonable / 结构化进度 hook / 演示模式 flag / 结构化对比 / dca 纯函数"""
import datetime
import json
import threading
from unittest.mock import patch

import pandas as pd

from data import database, fund_api
from data.cache import MemoryCache
from services._json import to_jsonable
from utils import agent_core, ai_helper
from utils.agent_core import agent_run


def _tool_call(name, args=None, call_id="call_1"):
    return {"type": "tool_call", "content": [{
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args or {}, ensure_ascii=False)},
    }]}


# ==================== M0 #3：MemoryCache 线程安全 ====================


def test_memory_cache_concurrent_get_clear_no_runtime_error():
    """双线程并发 get/clear_by_prefix：无 RLock 时 dict 遍历期删除会抛 RuntimeError"""
    c = MemoryCache()
    c.set("k1", 1, ttl=60)
    c.set("prefix:2", 2, ttl=60)
    errors = []

    def reader():
        try:
            for _ in range(3000):
                c.get("k1")
                c.contains("k1")
        except RuntimeError as e:
            errors.append(e)

    def clearer():
        for _ in range(1000):
            c.set("prefix:n", 3, ttl=60)
            c.clear_by_prefix("prefix")

    t1, t2 = threading.Thread(target=reader), threading.Thread(target=clearer)
    t1.start(); t2.start()
    t1.join(); t2.join()
    assert errors == []


# ==================== M0 #9：to_jsonable ====================


def test_to_jsonable_handles_nan_inf_df_timestamp():
    df = pd.DataFrame([{"a": 1.5, "b": float("nan")}])
    out = {
        "nan": float("nan"),
        "inf": float("inf"),
        "df": df,
        "ts": pd.Timestamp("2026-09-03 10:00:00"),
        "dt": datetime.date(2026, 9, 3),
        "nested": [{"x": float("nan")}],
    }
    cleaned = json.loads(json.dumps(to_jsonable(out)))
    assert cleaned["nan"] is None
    assert cleaned["inf"] is None
    assert cleaned["df"] == [{"a": 1.5, "b": None}]
    assert cleaned["ts"].startswith("2026-09-03")
    assert cleaned["dt"] == "2026-09-03"
    assert cleaned["nested"] == [{"x": None}]


def test_to_jsonable_str_fallback_for_unknown_objects():
    class Weird:
        def __str__(self):
            return "WEIRD"
    out = to_jsonable({"x": Weird()})
    assert json.dumps(out) == '{"x": "WEIRD"}'


# ==================== M0 #4：structured_progress 事件对 ====================


def _fake_llm_one_tool_then_text(messages, tools=None, model=None, temperature=0.7):
    if len([m for m in messages if m.get("role") == "tool"]) == 0:
        return _tool_call("get_stock_diagnosis", {"stock_code": "600519"})
    return {"type": "text", "content": "好了"}


def test_agent_run_structured_progress_off_by_default():
    """默认 structured_progress=False：on_progress 只收字符串 detail，零回归"""
    seen = []
    with patch.object(ai_helper, "API_KEY", "sk-test"), \
         patch.object(ai_helper, "call_llm", side_effect=_fake_llm_one_tool_then_text), \
         patch.object(agent_core, "execute_ai_tool_v2",
                      side_effect=lambda n, a: json.dumps({"ok": True}, ensure_ascii=False)):
        agent_run("x", on_progress=lambda s, d: seen.append((s, d)))
    assert seen, "进度回调应有事件"
    assert all(isinstance(d, str) for _, d in seen)


def test_agent_run_structured_progress_emits_start_end_pair():
    """structured_progress=True：tool_start/tool_end 成对，含 name/arguments/ok/elapsed_ms"""
    seen = []
    with patch.object(ai_helper, "API_KEY", "sk-test"), \
         patch.object(ai_helper, "call_llm", side_effect=_fake_llm_one_tool_then_text), \
         patch.object(agent_core, "execute_ai_tool_v2",
                      side_effect=lambda n, a: json.dumps({"ok": True}, ensure_ascii=False)):
        agent_run("x", structured_progress=True,
                  on_progress=lambda s, d: seen.append((s, d)))

    starts = [e for e in seen if e[0] == "tool_start"]
    ends = [e for e in seen if e[0] == "tool_end"]
    assert len(starts) == 1 and len(ends) == 1
    assert starts[0][1]["name"] == "get_stock_diagnosis"
    assert starts[0][1]["arguments"] == {"stock_code": "600519"}
    assert ends[0][1]["ok"] is True
    assert isinstance(ends[0][1]["elapsed_ms"], int) and ends[0][1]["elapsed_ms"] >= 0


def test_agent_run_structured_progress_bad_tool_marks_not_ok():
    """工具输出以「工具执行失败」开头 → tool_end.ok=False"""

    def fake_execute(name, arguments):
        return "工具执行失败：数据源不可用"

    seen = []
    with patch.object(ai_helper, "API_KEY", "sk-test"), \
         patch.object(ai_helper, "call_llm", side_effect=_fake_llm_one_tool_then_text), \
         patch.object(agent_core, "execute_ai_tool_v2", side_effect=fake_execute):
        agent_run("x", structured_progress=True,
                  on_progress=lambda s, d: seen.append((s, d)))

    end = [e for e in seen if e[0] == "tool_end"]
    assert end and end[0][1]["ok"] is False


# ==================== M0 #7：演示模式进程级 flag ====================


def test_demo_mode_module_flag():
    """set_demo_mode(True) → _is_demo_mode() 为 True（无 st.session_state 也生效）"""
    from utils.ai_helper import (
        set_demo_mode, _is_demo_mode, _load_funds_from_store, DEMO_FUNDS,
    )
    try:
        set_demo_mode(True)
        assert _is_demo_mode() is True
        # 演示模式下持仓读取走内置 seed
        funds = _load_funds_from_store()
        assert set(funds.keys()) == set(DEMO_FUNDS.keys())
    finally:
        set_demo_mode(False)
    assert _is_demo_mode() is False


# ==================== M0 #6：compare_funds_structured ====================


def test_compare_funds_structured_shape():
    """结构化对比：ok/metrics 键齐全；失败时 ok=False 不抛"""
    info = {"name": "白酒", "dwjz": 1.12, "gsz": 1.13, "gszzl": 0.5, "gztime": "2026-09-03"}
    with patch.object(fund_api, "get_fund_info", side_effect=[info, info]):
        r = fund_api.compare_funds_structured("161725", "110022")
    assert r["ok"] is True
    keys = {m["key"] for m in r["metrics"]}
    assert {"dwjz", "gsz", "gszzl", "gztime"} <= keys

    with patch.object(fund_api, "get_fund_info", side_effect=[None, None]):
        r = fund_api.compare_funds_structured("000000", "999999")
    assert r["ok"] is False and "失败" in r["error"]


# ==================== M0 #5：dca_result 纯函数 ====================


def test_dca_result_pure_dict(monkeypatch):
    """dca_result：无 st 依赖，字段齐全；数据不足返 None"""
    dates = [datetime.date(2026, i, 1) for i in range(1, 9)]
    values = [1.0, 1.1, 0.9, 1.2, 1.0, 1.3, 1.1, 1.4]
    monkeypatch.setattr(fund_api, "get_fund_history", lambda code, days: (dates, values))
    monkeypatch.setattr(fund_api, "backtest_strategy",
                        lambda code, amt, months, s: {
                            "final_value": 1000.0, "profit": 100.0, "profit_rate": 11.1,
                            "dates": dates, "values": values, "invest_line": [900.0] * 8,
                        })
    monkeypatch.setattr(fund_api, "get_fund_info", lambda code: {"name": "测试基金"})

    r = fund_api.dca_result("161725", 500, 8)
    assert isinstance(r, dict)
    assert r["fund_name"] == "测试基金"
    assert r["total_invest"] == 4000
    assert r["profit"] == 100.0
    assert r["months"] == 8 and len(r["values"]) == 8

    # backtest 失败 → None（不渲染、不抛）
    monkeypatch.setattr(fund_api, "backtest_strategy", lambda *a, **k: None)
    assert fund_api.dca_result("000000", 500, 8) is None


# ==================== M0 #8：delete_diary_entry ====================


def test_delete_diary_entry_by_id(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "t.db"))
    database.init_db()
    from utils.diary_tool import add_diary
    r1 = add_diary(fund_code="161725", action="买入", amount=1000)
    r2 = add_diary(fund_code="110022", action="卖出", amount=2000)
    assert r1.get("success") and r2.get("success")

    assert database.delete_diary_entry(r2["id"]) is True
    # 删不存在的行 → False
    assert database.delete_diary_entry(99999) is False
    # 剩下那条还在
    left = database.load_diary()
    assert len(left) == 1 and left[0]["fund_code"] == "161725"

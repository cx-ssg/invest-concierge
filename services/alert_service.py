# -*- coding: utf-8 -*-
"""
预警服务（v1.1 粘性三件套 A）：规则 CRUD + 触发判定 + 低频轮询调度 + 通知桥。

设计要点（docs/V1.1_PLAN.md A 节）：
- 三重限频防东财风控：仅交易时段 + 仅启用标的 + 单标的 10 分钟最小间隔；
  行情读取全部走 fund_api/stock_api 现有 TTL 缓存，不新增直连。
- 数据口径：基金用盘中估值涨跌幅（gszzl）、股票用现价——快照值触发；
  通知文案固定标注「按盘中估值，非最终净值」（预期管理前置，评审补强①）。
- 同一自然日同一条规则不重复触发。
- 通知桥：调度器在 uvicorn 进程内，托盘在桌面壳进程——同进程不同层，
  用回调注册解耦（launcher 注册 tray.notify）；无回调时只落库+应用内角标。
"""

import datetime
import threading
import time
import traceback

from data.database import (
    create_alert,
    delete_alert,
    list_alert_events,
    list_alerts,
    mark_all_alert_events_read,
    record_alert_event,
    unread_alert_events_count,
    update_alert_enabled,
    update_alert_last_triggered,
)
from services._json import to_jsonable

# 单标的最小轮询间隔（秒）——东财风控红线，激进模式留给 v1.2
POLL_INTERVAL_PER_SYMBOL = 600
# 调度线程巡检间隔（秒）：只做"到点没"判断，不拉行情
SCHEDULER_TICK = 60
TRADING_START = datetime.time(9, 30)
TRADING_END = datetime.time(15, 0)

_kind_valid = {"fund": "estimate_pct", "stock": "price"}


# ==================== 通知桥 ====================

_notify_callback = None
_notify_lock = threading.Lock()


def register_notifier(fn):
    """桌面壳启动时注册 tray.notify；浏览器/无头模式不注册（只落库+角标）。"""
    global _notify_callback
    with _notify_lock:
        _notify_callback = fn


def _notify(message):
    with _notify_lock:
        cb = _notify_callback
    if cb is None:
        return
    try:
        cb(message)
    except Exception:  # noqa: BLE001 - 通知失败不影响触发落库
        traceback.print_exc()


# ==================== 交易时段与判定（纯函数，可测） ====================

def is_trading_time(now=None):
    """周一至五 9:30-15:00（不含节假日表——v1.1 用轻量口径，节假日空跑无害）。"""
    now = now or datetime.datetime.now()
    if now.weekday() >= 5:
        return False
    return TRADING_START <= now.time() <= TRADING_END


def evaluate_alert(op, threshold, observed):
    """单点判定：above=observed>=threshold，below=observed<=threshold。"""
    if observed is None:
        return False
    if op == "above":
        return observed >= threshold
    if op == "below":
        return observed <= threshold
    return False


def validate_rule(kind, symbol, metric, op, threshold):
    """规则校验：kind/metric 配对 + op 合法 + threshold 数值。返回错误文案或 ""。"""
    if kind not in _kind_valid:
        return "kind 必须是 fund 或 stock"
    if _kind_valid[kind] != metric:
        return "fund 只支持 estimate_pct，stock 只支持 price"
    if op not in ("above", "below"):
        return "op 必须是 above 或 below"
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
        return "threshold 必须是数字"
    if not str(symbol or "").strip():
        return "symbol 不能为空"
    return ""


# ==================== 行情快照（走现有 TTL 缓存） ====================

def fetch_observation(alert):
    """按规则拉快照值；不可得返回 None（判定跳过，不误报）。"""
    kind, symbol = alert.get("kind"), alert.get("symbol")
    try:
        if kind == "fund":
            from data.fund_api import get_fund_info
            info = get_fund_info(str(symbol))
            if not info:
                return None
            gszzl = info.get("gszzl")
            try:
                return float(gszzl)
            except (TypeError, ValueError):
                return None
        if kind == "stock":
            from data.stock_api import get_stock_info
            info = get_stock_info(str(symbol))
            if not info:
                return None
            price = info.get("price")
            try:
                return float(price)
            except (TypeError, ValueError):
                return None
    except Exception:  # noqa: BLE001 - 单条行情失败不炸轮询
        return None
    return None


# ==================== 轮询与调度 ====================

_last_polled = {}          # symbol -> ts（进程内节流，与 DB 无关）
_poll_lock = threading.Lock()
_scheduler_thread = None
_scheduler_stop = threading.Event()


def _fmt_unit(kind):
    return "%" if kind == "fund" else "元"


def poll_once(now=None, force=False):
    """巡检一轮：判定全部启用规则并落事件/通知。返回触发条数（测试断言用）。"""
    now = now or datetime.datetime.now()
    if not force and not is_trading_time(now):
        return 0
    alerts = [a for a in list_alerts(enabled_only=True) if a.get("enabled")]
    if not alerts:
        return 0

    today = now.strftime("%Y-%m-%d")
    fired = 0
    for alert in alerts:
        symbol = str(alert.get("symbol", ""))
        # 同自然日去重
        if str(alert.get("last_triggered_date", "")) == today:
            continue
        # 单标的节流
        with _poll_lock:
            last = _last_polled.get(symbol, 0)
            if not force and time.time() - last < POLL_INTERVAL_PER_SYMBOL:
                continue
            _last_polled[symbol] = time.time()

        observed = fetch_observation(alert)
        if observed is None:
            continue
        if not evaluate_alert(alert.get("op"), alert.get("threshold"), observed):
            continue

        unit = _fmt_unit(alert.get("kind"))
        name = alert.get("name") or symbol
        op_text = "高于" if alert.get("op") == "above" else "低于"
        message = (
            "🔔 预警触发：{name} 当前 {observed:g}{unit}，已{op_text}你设定的 "
            "{threshold:g}{unit}。（按盘中估值，非最终净值）"
        ).format(name=name, observed=observed, unit=unit,
                 op_text=op_text, threshold=float(alert.get("threshold", 0)))
        record_alert_event(alert["id"], symbol, name, observed,
                           float(alert.get("threshold", 0)), message)
        update_alert_last_triggered(alert["id"], today)
        _notify(message)
        fired += 1
    return fired


def scheduler_loop():
    while not _scheduler_stop.is_set():
        try:
            poll_once()
        except Exception:  # noqa: BLE001 - 调度线程永不退出
            traceback.print_exc()
        _scheduler_stop.wait(SCHEDULER_TICK)


def start_scheduler():
    """FastAPI startup 调用；幂等。"""
    global _scheduler_thread
    if _scheduler_thread is not None and _scheduler_thread.is_alive():
        return
    _scheduler_stop.clear()
    _scheduler_thread = threading.Thread(
        target=scheduler_loop, name="alert-scheduler", daemon=True)
    _scheduler_thread.start()


def stop_scheduler():
    _scheduler_stop.set()


# ==================== CRUD 包装（路由层用） ====================

def alerts_list():
    return to_jsonable(list_alerts())


def alerts_create(kind, symbol, name, metric, op, threshold):
    err = validate_rule(kind, symbol, metric, op, threshold)
    if err:
        return {"ok": False, "error": err}
    alert_id = create_alert(kind, str(symbol).strip(), str(name or ""), metric, op, float(threshold))
    if alert_id is None:
        return {"ok": False, "error": "写入失败（库不可用或参数非法）"}
    return {"ok": True, "id": alert_id}


def alerts_patch(alert_id, enabled):
    if not isinstance(enabled, bool):
        return {"ok": False, "error": "enabled 必须是布尔"}
    ok = update_alert_enabled(alert_id, enabled)
    return {"ok": bool(ok)} if ok else {"ok": False, "error": "规则不存在"}


def alerts_delete(alert_id):
    ok = delete_alert(alert_id)
    return {"ok": bool(ok)} if ok else {"ok": False, "error": "规则不存在"}


def alerts_events(limit=50):
    events = to_jsonable(list_alert_events(limit=limit))
    return {"ok": True, "events": events, "unread": unread_alert_events_count()}


def alerts_events_mark_read():
    mark_all_alert_events_read()
    return {"ok": True, "unread": 0}

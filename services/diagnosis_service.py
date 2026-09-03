# -*- coding: utf-8 -*-
"""
诊断服务（M0）：/api/stocks/{code}/diagnosis 数据源。

冷启 15-40s（财报引擎无缓存）→ 服务端不塞 ThreadPoolExecutor 里的
同步等待进事件循环：router 层用 run_in_threadpool 调本模块。
响应统一过 to_jsonable（诊断 payload 含 DataFrame/Timestamp/NaN）。
"""

from services._json import to_jsonable


def get(stock_code):
    """GET /api/stocks/{code}/diagnosis：6 引擎 payload（data.diagnosis 24h TTL 缓存）"""
    code = str(stock_code or "").strip()
    if not code or not code.isdigit() or len(code) != 6:
        return {"ok": False, "error": "股票代码须为 6 位数字（如 600519）"}

    from data.diagnosis import build_diagnosis_payload
    try:
        payload = build_diagnosis_payload(code)
    except Exception as e:  # noqa: BLE001 - 单引擎失败已在 payload.errors 内，这里是编排级兜底
        return {"ok": False, "error": "诊断构建失败：{}".format(e), "code": code}

    payload["ok"] = True
    return to_jsonable(payload)

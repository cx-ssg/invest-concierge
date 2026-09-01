# -*- coding: utf-8 -*-
"""
投资日记 AI 工具封装 - load_diary → append → save_diary（database 只有整表 save）。

MVP 接受多写并发"最后写入覆盖"的边界风险（文档已标注），本封装不做并发控制。
供 agent_core TOOL_REGISTRY 注册为 add_diary 工具。

实现依据：docs/AGENT_MVP_DESIGN.md §3 首批工具清单（P2）。
"""
import sys
from datetime import date as _date

from data.database import load_diary, save_diary

# Windows 控制台 GBK 防护
if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def add_diary(date=None, fund_code="", fund_name="", action="", amount=0.0, note=""):
    """写入一条投资日记：load → append → save。

    参数：
        date: 交易日 YYYY-MM-DD，缺省为今天
        fund_code / fund_name / action / amount / note: 日记字段

    返回：
        {"success": True, "id": 新 id, "entry": {...}} 或 {"error": "..."}（可被 Agent 识别）
    """
    try:
        entries = load_diary() or []
        if not date:
            date = _date.today().isoformat()
        new_id = max([int(e.get("id") or 0) for e in entries], default=0) + 1
        entry = {
            "id": new_id,
            "date": str(date),
            "fund_code": str(fund_code or ""),
            "fund_name": str(fund_name or ""),
            "action": str(action or ""),
            "amount": float(amount or 0),
            "note": str(note or ""),
        }
        entries.append(entry)
        if not save_diary(entries):
            return {"error": "日记保存失败，请重试"}
        return {"success": True, "id": new_id, "entry": entry}
    except Exception as e:  # noqa: BLE001
        return {"error": "添加日记失败：{}".format(e)}
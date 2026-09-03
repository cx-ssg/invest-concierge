# -*- coding: utf-8 -*-
"""
日记服务（M0）：投资日记 list / add / delete。

并发纪律：diary 是整表读改写（save_diary 全表替换），同样走进程内锁；
单条删除走新增的 database.delete_diary_entry(id)（参数绑定 DELETE，不做整表替换）。
"""

import threading

from data.database import load_diary, delete_diary_entry
from services._json import to_jsonable

# diary 整表读改写的进程内互斥锁
_diary_lock = threading.Lock()


def list_entries():
    """GET /api/diary：全量日记（date DESC）"""
    with _diary_lock:
        entries = list(load_diary())
    return to_jsonable(entries)


def add_entry(date=None, fund_code="", fund_name="", action="", amount=0.0, note=""):
    """POST /api/diary：追加一条（经 utils.diary_tool.add_diary 写入）

    返回 {"ok": True, "id": 新 id}（把 add_diary 的 success/id 归一为 ok 形态）。
    """
    from utils.diary_tool import add_diary
    with _diary_lock:
        result = add_diary(
            date=date,
            fund_code=fund_code,
            fund_name=fund_name,
            action=action,
            amount=amount,
            note=note,
        )
    if not result or not result.get("success"):
        return {"ok": False, "error": (result or {}).get("error", "日记写入失败")}
    return {"ok": True, "id": result.get("id")}


def remove_entry(entry_id):
    """DELETE /api/diary/{id}：单条删除（参数绑定）"""
    with _diary_lock:
        deleted = delete_diary_entry(entry_id)
    if not deleted:
        return {"ok": False, "error": "日记不存在或已删除：{}".format(entry_id)}
    return {"ok": True, "id": entry_id}

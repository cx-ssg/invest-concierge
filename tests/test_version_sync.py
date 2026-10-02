# -*- coding: utf-8 -*-
"""版本号一致性锁（A-R2 / claude 二路审计 F6，既有偏差）。

应用内版本只有一个来源：`services/status_service.VERSION` ——
`/api/health`、`/api/status`、`/api/settings`、前端状态栏与设置页都读它。
但它此前写 `1.0.0`，而仓库实际版本（`frontend/package.json` = `1.2.0`，对齐 `git tag v1.2.0`）
是另一套 ⇒ 同一产品里用户/审计读到两个版本号。

本锁把两边钉在一起：改版本号必须同时改 package.json（或反过来），否则 CI 红。
"""
import json
from pathlib import Path

from services import settings_service, status_service

REPO = Path(__file__).resolve().parents[1]


def _frontend_package_version():
    """`frontend/package.json` 的 version（仓库版本口径的单一事实源）。"""
    raw = (REPO / "frontend" / "package.json").read_text(encoding="utf-8")
    return json.loads(raw)["version"]


def test_app_version_matches_frontend_package_json():
    assert status_service.VERSION == _frontend_package_version(), (
        "应用内版本（status_service.VERSION={}）与 frontend/package.json（{}）不一致："
        "同一产品不得有两个版本号（F6）".format(
            status_service.VERSION, _frontend_package_version()))


def test_version_is_not_the_stale_display_value():
    """回归锁：设置页/状态栏显示的值必须是仓库版本，而不是历史上的 `1.0.0`。"""
    assert status_service.VERSION != "1.0.0", \
        "应用内版本回退到 1.0.0（与 tag/package.json 的 1.2.0 口径冲突，F6 复发）"


def test_health_and_settings_report_the_same_version():
    assert status_service.health()["version"] == status_service.VERSION
    assert settings_service.get_settings()["version"] == status_service.VERSION


def test_openapi_app_version_matches_status_version():
    """`server.main` 的 `FastAPI(version=)` 与状态栏版本同源（第三个版本面）。"""
    from server.main import app

    assert app.version == status_service.VERSION, \
        "OpenAPI/接口文档版本（{}）与状态栏版本（{}）不一致（F6）".format(
            app.version, status_service.VERSION)

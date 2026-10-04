# -*- coding: utf-8 -*-
"""版本号一致性锁（A-R2 / claude 二路审计 F6；2026-10-04 交付线扩面）。

应用内版本只有一个来源：`services/status_service.VERSION` ——
`/api/health`、`/api/status`、`/api/settings`、前端状态栏与设置页都读它。

两代问题（同一族，后者是前者的升级版）：
1. **2026-10-03（F6）**：它是 `1.0.0`，而仓库版本是 `1.2.0` ⇒ 同一产品两个版本号。
2. **2026-10-04 交付线实测**：三面统一到 `1.2.0` 之后，**对外 tag 已经到 `v1.5.0`**
   而三面一动不动 ⇒ **旧锁只钉「内部自洽」，钉不住「与发布版本对应」**
   —— 一个从未被任何改动触碰过的锁，越绿越假。
   现扩为 **六面**：应用内 / `frontend/package.json` / `FastAPI(version=)` /
   **`CHANGELOG.md` 顶部** / **安装器 `.iss`** / **打包脚本输出名**。
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


# ==================== 2026-10-04 交付线扩面：仓库外三面 ====================
# 起因：三面统一到 1.2.0 后，对外 tag 已到 v1.5.0 而三面不动 —— 旧锁只证明「内部自洽」。
# 这三条把「对外/构建口径」也钉住（缺一条都会再现「发布版本 ≠ 应用版本」）。


def _changelog_latest_version():
    """`CHANGELOG.md` 顶部第一个语义化版本标题（跳过 `## [Unreleased]`）。"""
    import re

    for line in (REPO / "CHANGELOG.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^## \[(\d+\.\d+\.\d+)\]", line)
        if m:
            return m.group(1)
    raise AssertionError("CHANGELOG.md 里找不到 `## [x.y.z]` 版本标题（对外口径缺失）")


def _iss_app_version():
    """安装器 `.iss` 的 `#define MyAppVersion`（安装包文件名与「关于」信息读它）。"""
    import re

    iss = REPO / "desktop" / "installer" / "invest-concierge-setup.iss"
    for line in iss.read_text(encoding="utf-8").splitlines():
        m = re.match(r'^#define\s+MyAppVersion\s+"([^"]+)"', line)
        if m:
            return m.group(1)
    raise AssertionError(f"{iss.name} 里找不到 `#define MyAppVersion`")


def _installer_output_version():
    """打包脚本 `.bat` 里安装包的输出名 `invest-concierge-setup-v{x.y.z}.exe`。"""
    import re

    bat = REPO / "scripts" / "build_installer.bat"
    m = re.search(r"invest-concierge-setup-v(\d+\.\d+\.\d+)\.exe",
                  bat.read_text(encoding="utf-8"))
    if not m:
        raise AssertionError(f"{bat.name} 里找不到 `invest-concierge-setup-v<x.y.z>.exe`")
    return m.group(1)


def test_version_matches_changelog_latest():
    """对外版本面（CHANGELOG 顶部）与应用内版本同源。

    失败通常意味着「改了应用版本但没写 CHANGELOG」——即发布说明与产品实际不一致。
    """
    assert status_service.VERSION == _changelog_latest_version(), \
        "应用内版本（{}）与 CHANGELOG 顶部版本（{}）不一致".format(
            status_service.VERSION, _changelog_latest_version())


def test_version_matches_installer_and_build_script():
    """构建/安装口径（`.iss` + `.bat` 输出名）与应用内版本一致。

    失败通常意味着「安装包文件名还是上一版」——用户装完看到的版本号是错的。
    """
    assert status_service.VERSION == _iss_app_version(), \
        "应用内版本（{}）与安装器 MyAppVersion（{}）不一致".format(
            status_service.VERSION, _iss_app_version())
    assert status_service.VERSION == _installer_output_version(), \
        "应用内版本（{}）与安装包输出名版本（{}）不一致".format(
            status_service.VERSION, _installer_output_version())

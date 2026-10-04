# -*- coding: utf-8 -*-
"""构建后校验：字符串晚绑定（动态导入）面是否真的进了 PyInstaller 包。

────────────────────────────────────────────────────────────────────────
为什么需要这个脚本（2026-10-04 critic 审计 F1 的实证，不是推测）
────────────────────────────────────────────────────────────────────────
本仓有大量的**字符串晚绑定**：
  · `utils/agent_core.py` 的工具注册表写 `module="data.dragon_api"`，
    真正 import 发生在其 `importlib.import_module(mod_name)`（mod_name 是**变量**）
  · `services/market_service.py` 同款：`_call("data.market_api", "fn")` / `_call_all([...])`

PyInstaller 只做静态分析 ⇒ **这些模块不会被收进包**，而且：
  · 构建**不报错**、`warn-*.txt` **不提示**（静默缺失）
  · 源码态 `pytest` **全绿**（模块就在磁盘上，`import` 得到）
  · 只有**用户装完 exe 之后**才炸：工具调用返回「No module named 'data.dragon_api'」

实测（v1.5.0 产物 `build_v150/invest-concierge/PYZ-00.toc`）：缺
`data.dragon_api` / `data.moneyflow_api` / `data.limit_up_api` ⇒ **9 个 Agent 工具
（含 v1.5.0 全部 7 个龙虎工具）在安装版不可用**，而当时 674 条测试与 CI 全绿。

⇒ 结论：**源码态测试原理上无法覆盖这一类风险，必须在构建产物上校验**。

────────────────────────────────────────────────────────────────────────
覆盖面声明（**如实收窄**，2026-10-04 第二轮审计要求）
────────────────────────────────────────────────────────────────────────
本脚本用 AST 抓三类**字面量**：
  ① 关键字参数 `module="…"`（工具注册表形态）
  ② `import_module("…")` / `__import__("…")`
  ③ **任意字符串字面量**若形如合规模块名（`data.x` / `utils.x.y` …）——
     覆盖 `_call("data.market_api", "fn")` 这类「普通函数的位置参数字面量」
**抓不到**（原理限制，请人工 review）：
  · 拼接式：`import_module("data." + name)`
  · 别名：`from importlib import import_module as im`
  · 由配置/DB 值驱动的模块名（运行时才知道）
另外本脚本只读**中间产物** `PYZ-00.toc`；要证「exe 里就是这份代码」请配合
`scripts/verify_exe.py`（直接解开 exe 读模块数与版本常量）。脚本内已加
**TOC 新鲜度断言**，用于防「改完没重建 ⇒ 校验旧 TOC 报 ✓」的假绿。

用法：
    python scripts/verify_bundle.py                    # 自动定位最新的 build_*/invest-concierge/PYZ-00.toc
    python scripts/verify_bundle.py --toc build_v150/invest-concierge/PYZ-00.toc
退出码：0 全部命中；1 有失败项（逐条列出，可直接贴进构建日志）
"""
import argparse
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 扫描范围：运行时真正会走的代码（排除 tests / scripts / pages / 旧 streamlit 壳）
SCAN_DIRS = ("utils", "services", "server", "data", "desktop")

OWN_PREFIXES = ("data.", "services.", "server.", "utils.")

# 形如合规模块名的字符串字面量（形态 ③）
MODULE_LIKE = re.compile(r"^(data|services|server|utils)(\.[A-Za-z_][A-Za-z0-9_]*)+$")

SKIP_PARTS = ("build_env", ".venv", "venv", "node_modules", "site-packages", "__pycache__")


def iter_source_files():
    for d in SCAN_DIRS:
        base = ROOT / d
        if not base.is_dir():
            continue
        for py in sorted(base.rglob("*.py")):
            if any(s in py.parts for s in SKIP_PARTS):
                continue
            yield py


def collect_dynamic_import_targets():
    """AST 扫出「字符串形式的模块引用」，返回 {模块名: [出处 file:line]}。"""
    targets = {}

    def record(name, where):
        if isinstance(name, str) and name.startswith(OWN_PREFIXES):
            targets.setdefault(name, []).append(where)

    for py in iter_source_files():
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        rel = py.relative_to(ROOT).as_posix()
        for node in ast.walk(tree):
            # 形态 ① / ②：Call 上的关键字参数或动态导入函数
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == "module" and isinstance(kw.value, ast.Constant):
                        record(kw.value.value, f"{rel}:{node.lineno}")
                fname = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                if fname in ("import_module", "__import__") and node.args:
                    if isinstance(node.args[0], ast.Constant):
                        record(node.args[0].value, f"{rel}:{node.lineno}")
            # 形态 ③：任何形如模块名的字符串字面量（覆盖位置参数形态）
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if MODULE_LIKE.match(node.value):
                    record(node.value, f"{rel}:{node.lineno}")
    return targets


def load_toc_modules(toc_path):
    """从 PYZ-00.toc 提取包内模块名集合（TOC 是 python 字面量列表文本）。"""
    text = Path(toc_path).read_text(encoding="utf-8", errors="replace")
    return set(re.findall(r"\('([A-Za-z_][\w.]*)'", text))


def find_toc(explicit=None):
    if explicit:
        return Path(explicit)
    candidates = sorted(ROOT.glob("build_*/invest-concierge/PYZ-00.toc"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        candidates = sorted(ROOT.glob("build/*/PYZ-00.toc"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def newest_source_mtime():
    """最新项目源码 mtime（排除 venv / 构建环境）。"""
    newest, newest_path = 0.0, None
    for py in iter_source_files():
        t = py.stat().st_mtime
        if t > newest:
            newest, newest_path = t, py
    return newest, newest_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--toc", default="", help="PYZ-00.toc 路径（默认自动定位最新的 build_*）")
    ap.add_argument("--list", action="store_true",
                    help="打印抓到的全部模块引用与出处（用于核对覆盖面是否如你所想）")
    args = ap.parse_args()

    toc = find_toc(args.toc)
    if toc is None or not toc.exists():
        print("[verify_bundle] ✗ 找不到 PYZ-00.toc —— 先跑一次 PyInstaller 构建")
        return 1

    packaged = load_toc_modules(toc)
    targets = collect_dynamic_import_targets()

    print(f"[verify_bundle] TOC = {toc}（{len(packaged)} 个模块）")
    print(f"[verify_bundle] 动态导入面 = {len(targets)} 个模块引用"
          f"（AST 扫描 {'/'.join(SCAN_DIRS)}；覆盖 ①②③ 三类**字面量**，"
          f"拼接式/别名/配置驱动抓不到）")

    if args.list:
        print("[verify_bundle] 抓到的模块引用（模块 ← 出处）：")
        for m in sorted(targets):
            in_pkg = "IN " if m in packaged else "MISSING"
            print(f"    [{in_pkg}] {m:34s} ← {', '.join(sorted(set(targets[m]))[:4])}")

    failures = []

    # 0. TOC 新鲜度（防「改完没重建 ⇒ 校验旧 TOC 报 ✓」）
    newest, newest_path = newest_source_mtime()
    if newest and toc.stat().st_mtime < newest:
        failures.append(
            "TOC 比源码旧 ⇒ 它是**上一次构建**的产物，本次校验无效。"
            f"（TOC {toc.stat().st_mtime:.0f} < {newest_path.relative_to(ROOT)} {newest:.0f}）"
            " 修法：`rm -rf <workpath>` 后重新构建（PyInstaller 会复用已存在的 PYZ-00.pyz）")
    else:
        print(f"[verify_bundle] ✓ TOC 新鲜（晚于最新源码 {newest_path.relative_to(ROOT) if newest_path else '-'}）")

    missing = {m: w for m, w in targets.items() if m not in packaged}
    if missing:
        failures.append("以下模块被字符串引用但**不在包内**（运行时将 ModuleNotFoundError）：")
        for m, where in sorted(missing.items()):
            failures.append(f"    - {m}   ← {', '.join(sorted(set(where)))}")

    if failures:
        print("\n[verify_bundle] ✗ 未通过：")
        for f in failures:
            print(("    " if not f.startswith("    ") else "") + f)
        print("\n修法：desktop/invest-concierge.spec 的 hiddenimports 收进来"
              "（data 包已用 collect_submodules 覆盖；新包同理）")
        print("补完请重跑，并配 `python scripts/verify_exe.py` 确认 exe 本体。")
        return 1

    print(f"\n[verify_bundle] ✓ 全部命中：动态导入面 {len(targets)}/{len(targets)} 在包内")
    print("    ⚠️ 覆盖面仅限上述三类字面量；拼接式 / 别名 / 配置驱动形态请人工确认。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

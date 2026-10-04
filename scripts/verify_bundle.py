# -*- coding: utf-8 -*-
"""构建后校验：字符串晚绑定（动态导入）面是否真的进了 PyInstaller 包。

────────────────────────────────────────────────────────────────────────
为什么需要这个脚本（2026-10-04 critic 审计 F1 的实证，不是推测）
────────────────────────────────────────────────────────────────────────
本仓有大量的**字符串晚绑定**：
  · `utils/agent_core.py` 的工具注册表写 `module="data.dragon_api"`，
    真正 import 发生在其 `importlib.import_module(mod_name)`（mod_name 是**变量**）
  · `services/market_service.py` 同款：`_resolve(module_name, fn_name)` → `import_module(...)`

PyInstaller 只做静态分析 ⇒ **这些模块不会被收进包**，而且：
  · 构建**不报错**、`warn-*.txt` **不提示**（静默缺失）
  · 源码态 `pytest` **全绿**（模块就在磁盘上，`import` 得到）
  · 只有**用户装完 exe 之后**才炸：工具调用返回「No module named 'data.dragon_api'」

实测（v1.5.0 产物 `build_v150/invest-concierge/PYZ-00.toc`）：缺
`data.dragon_api` / `data.moneyflow_api` / `data.limit_up_api` ⇒ **9 个 Agent 工具
（含 v1.5.0 全部 7 个龙虎工具）在安装版不可用**，而当时 674 条测试与 CI 全绿。

⇒ 结论：**源码态测试原理上无法覆盖这一类风险，必须在构建产物上校验**。

用法：
    python scripts/verify_bundle.py                    # 自动定位最新的 build_*/invest-concierge/PYZ-00.toc
    python scripts/verify_bundle.py --toc build_v150/invest-concierge/PYZ-00.toc
退出码：0 全部命中；1 有缺失（逐条列出，可直接贴进构建日志）
"""
import argparse
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 扫描范围：运行时真正会走的代码（排除 tests / scripts / pages / 旧 streamlit 壳）
SCAN_DIRS = ("utils", "services", "server", "data")

# 只关心本仓自己的包前缀（第三方模块名不作为断言对象）
OWN_PREFIXES = ("data.", "services.", "server.", "utils.")


def collect_dynamic_import_targets():
    """AST 扫出所有「字符串形式的模块引用」，返回 {模块名: [出处 file:line]}。

    覆盖三种形态（与 `tests/test_rag_llm_judge.py` 的动态导入锁同思路）：
      1. 关键字参数 `module="data.xxx"`（工具注册表风格）
      2. `importlib.import_module("data.xxx")` / `from importlib import import_module` 别名
      3. `__import__("data.xxx")`
    只能识别**字面量**：拼接式（`"data." + name`）原理上无法静态覆盖 —— 见脚本末说明。
    """
    targets = {}

    def record(name, where):
        if isinstance(name, str) and name.startswith(OWN_PREFIXES):
            targets.setdefault(name, []).append(where)

    for d in SCAN_DIRS:
        base = ROOT / d
        if not base.is_dir():
            continue
        for py in sorted(base.rglob("*.py")):
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            rel = py.relative_to(ROOT).as_posix()
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                # 形态 1：module="..."
                for kw in node.keywords:
                    if kw.arg == "module" and isinstance(kw.value, ast.Constant):
                        record(kw.value.value, f"{rel}:{node.lineno}")
                # 形态 2 / 3：import_module("...") / __import__("...")
                fname = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                if fname in ("import_module", "__import__") and node.args:
                    if isinstance(node.args[0], ast.Constant):
                        record(node.args[0].value, f"{rel}:{node.lineno}")
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--toc", default="", help="PYZ-00.toc 路径（默认自动定位最新的 build_*）")
    args = ap.parse_args()

    toc = find_toc(args.toc)
    if toc is None or not toc.exists():
        print("[verify_bundle] ✗ 找不到 PYZ-00.toc —— 先跑一次 PyInstaller 构建")
        return 1

    packaged = load_toc_modules(toc)
    targets = collect_dynamic_import_targets()

    print(f"[verify_bundle] TOC = {toc}（{len(packaged)} 个模块）")
    print(f"[verify_bundle] 动态导入面 = {len(targets)} 个模块引用（AST 扫描 {', '.join(SCAN_DIRS)}）")

    missing = {m: w for m, w in targets.items() if m not in packaged}
    if missing:
        print("\n[verify_bundle] ✗ 以下模块被字符串引用但**不在包内**"
              "（运行时将 ModuleNotFoundError）：")
        for m, where in sorted(missing.items()):
            print(f"    - {m}   ← {', '.join(sorted(set(where)))}")
        print("\n修法：desktop/invest-concierge.spec 的 hiddenimports 收进来"
              "（已用 collect_submodules(\"data\") 覆盖 data 包；新包同理）")
        return 1

    print("\n[verify_bundle] ✓ 全部命中：动态导入面 %d/%d 在包内"
          % (len(targets), len(targets)))
    print("    注意：拼接式动态导入（如 \"data.\" + name）原理上无法静态覆盖，"
          "新增此类写法时请手动确认。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

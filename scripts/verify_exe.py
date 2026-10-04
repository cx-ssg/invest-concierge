# -*- coding: utf-8 -*-
"""exe 端到端自证：直接解开 onefile 产物内部，验证「打进去的就是当前代码」。

────────────────────────────────────────────────────────────────────────
为什么需要（2026-10-04 交付线的两轮审计结论）
────────────────────────────────────────────────────────────────────────
1. **`verify_bundle.py` 只看 `PYZ-00.toc`（构建中间产物）** —— 而 PyInstaller 会**复用已存在的
   `PYZ-00.pyz`**（改完 spec 不删 workpath 时，`Analysis/PKG/EXE-00.toc` 会刷新、PYZ 却不重建）
   ⇒ 只读 TOC 可能在**旧 TOC** 上报 ✓（审计指出的「自我欺骗主路径」）。
2. 交付时被问到的关键问题其实有两层：
   · 「工具依赖的模块在不在包里」→ `PYZ-00.toc` 能答
   · 「**exe 里那几行常量到底是什么**」→ 只有**解开 exe** 才答得了
   此前只能靠「打包时间晚于改动时间」推断版本号，属**推断不是事实**（审计 W1）。

本脚本回答第二层，并顺带把第 1 点的假绿堵住（校验的是 exe 本体，不是中间 TOC）。

校验项：
  A. exe 内 PYZ 模块数 与 磁盘 `PYZ-00.toc` 模块数一致（防「TOC 与 exe 不同源」）
  B. `utils/agent_core.py` 注册表里**每个** `module="..."` 都在 exe 内（F1 那一类）
  C. `services.status_service` 的字节码常量里的版本号 == 仓库源码里的 `VERSION`（W1）
  D. exe mtime 晚于 `utils/ services/ server/ data/ desktop/` 下最新源码 mtime（防拿旧包充当新包）

用法：
    python scripts/verify_exe.py                         # 自动找最新 dist_*/invest-concierge.exe
    python scripts/verify_exe.py --exe dist_v150/invest-concierge.exe
退出码：0 全过；1 有失败项（逐条打印）
"""
import argparse
import ast
import marshal
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("utils", "services", "server", "data", "desktop")
OWN_PREFIXES = ("data.", "services.", "server.", "utils.")


# ── 小工具 ────────────────────────────────────────────────────────────────
def find_exe(explicit=""):
    if explicit:
        return Path(explicit)
    cands = sorted(ROOT.glob("dist_*/invest-concierge.exe"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0] if cands else None


def tool_registry_modules():
    """AST 提取 `agent_core.py` 注册表里所有 module="..." 的字面量。"""
    src = (ROOT / "utils" / "agent_core.py").read_text(encoding="utf-8")
    mods = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "module" and isinstance(kw.value, ast.Constant):
                    if isinstance(kw.value.value, str) and kw.value.value.startswith(OWN_PREFIXES):
                        mods.add(kw.value.value)
    return sorted(mods)


def source_version():
    """读 `services/status_service.py` 的 VERSION（正则，不 import，避免副作用）。"""
    src = (ROOT / "services" / "status_service.py").read_text(encoding="utf-8")
    m = re.search(r'^VERSION\s*=\s*"([^"]+)"', src, re.MULTILINE)
    return m.group(1) if m else None


def newest_source_mtime():
    """最新**项目源码** mtime（排除 venv / 构建环境 / node_modules —— 否则会命中
    `desktop/build_env/Lib/site-packages/...` 里的第三方包，使新鲜度判断失去意义）。"""
    skip = ("build_env", ".venv", "venv", "node_modules", "site-packages", "__pycache__")
    newest, newest_path = 0.0, None
    for d in SCAN_DIRS:
        base = ROOT / d
        if not base.is_dir():
            continue
        for py in base.rglob("*.py"):
            if any(s in py.parts for s in skip):
                continue
            t = py.stat().st_mtime
            if t > newest:
                newest, newest_path = t, py
    return newest, newest_path


# ── exe 读取 ──────────────────────────────────────────────────────────────
def load_exe_modules(exe: Path):
    """解开 onefile exe → 返回 (模块名集合, {模块名: code object}, exe 内 PYZ 字节数)。"""
    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

    arc = CArchiveReader(str(exe))
    pyz_names = sorted(str(n) for n in arc.toc if str(n).upper().startswith("PYZ"))
    if not pyz_names:
        raise RuntimeError("exe 归档里找不到 PYZ 条目")
    data = arc.extract(pyz_names[0])
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pyz")
    tmp.write(data)
    tmp.close()
    zy = ZlibArchiveReader(tmp.name)
    mods = {str(n): zy.extract(str(n)) for n in sorted(zy.toc)}
    return mods, len(data)


def code_string_consts(code, depth=0):
    """递归收集 code object 里的字符串常量（含嵌套函数/类）。"""
    out = []
    for k in getattr(code, "co_consts", ()):
        if isinstance(k, str):
            out.append(k)
        elif hasattr(k, "co_consts") and depth < 6:
            out.extend(code_string_consts(k, depth + 1))
    return out


def toc_module_count():
    tocs = sorted(ROOT.glob("build_*/invest-concierge/PYZ-00.toc"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    if not tocs:
        return None, None
    text = tocs[0].read_text(encoding="utf-8", errors="replace")
    return len(set(re.findall(r"\('([A-Za-z_][\w.]*)'", text))), tocs[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default="", help="exe 路径（默认自动找最新 dist_*/）")
    args = ap.parse_args()

    exe = find_exe(args.exe)
    if exe is None or not exe.exists():
        print("[verify_exe] ✗ 找不到 exe —— 先跑一次 PyInstaller 构建")
        return 1

    print(f"[verify_exe] exe = {exe}（{exe.stat().st_size / 1e6:.2f} MB 十进制）")
    mods, pyz_bytes = load_exe_modules(exe)
    print(f"[verify_exe] exe 内 PYZ：{len(mods)} 个模块 / {pyz_bytes / 1e6:.1f} MB")

    failures = []

    # A. exe 内模块数 vs 中间 TOC（防「TOC 是旧的」假绿）
    toc_n, toc_path = toc_module_count()
    if toc_n is None:
        print("[verify_exe] · A 跳过：没找到 build_* 下的 PYZ-00.toc")
    elif toc_n == len(mods):
        print(f"[verify_exe] ✓ A TOC 与 exe 同源（各 {toc_n} 模块）")
    else:
        failures.append(f"A：TOC({toc_n}) 与 exe({len(mods)}) 模块数不一致 ⇒ "
                        f"exe 不是该 TOC 构建的（或 TOC 是旧的）")

    # B. 工具注册表依赖的模块都在 exe 内
    wanted = tool_registry_modules()
    missing = [m for m in wanted if m not in mods]
    if missing:
        failures.append("B：工具注册表依赖模块不在 exe 内：" + ", ".join(missing))
    else:
        print(f"[verify_exe] ✓ B 工具注册表依赖的 {len(wanted)} 个模块全部在 exe 内")

    # C. exe 内版本常量 == 仓库源码常量
    src_ver = source_version()
    ss = "services.status_service"
    if ss not in mods:
        failures.append(f"C：exe 内找不到 {ss}")
    else:
        consts = code_string_consts(mods[ss])
        ver_like = sorted({c for c in consts
                           if c and c[0].isdigit() and c.count(".") == 2 and len(c) <= 8})
        if src_ver and src_ver in ver_like:
            print(f"[verify_exe] ✓ C exe 内 VERSION = {src_ver}（与源码一致）")
        else:
            failures.append(f"C：exe 内版本常量 {ver_like} 与源码 VERSION={src_ver} 不一致")

    # D. exe 比源码新（防拿旧包充当新包）
    newest, newest_path = newest_source_mtime()
    if newest and exe.stat().st_mtime >= newest:
        print(f"[verify_exe] ✓ D exe 晚于最新源码（对比 {newest_path.relative_to(ROOT)}）")
    else:
        failures.append("D：exe mtime 早于最新源码 ⇒ 可能是旧包")

    print()
    if failures:
        print("[verify_exe] ✗ 未通过：")
        for f in failures:
            print("    - " + f)
        return 1
    print("[verify_exe] ✓ 全部通过：exe 就是当前代码构建的产物（A/B/C/D）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

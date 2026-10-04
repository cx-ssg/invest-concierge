# v1.5.1 —— 桌面版打包链修复 + 版本口径统一 + 交付物重出

> 本版为 **patch 版**：v1.5.0 的功能面不变（仍是 **31 个 Agent 工具 / 9 个页面**），
> 修的是**打包链**与**对外口径**，并**首次附带安装包**。

## ⚠️ 为什么会有这一版（先说清楚）

`v1.5.0` 的 Release **创建时未附任何 assets**（`gh release view v1.5.0 → assets: []`），
而且它的 tag 指向的源码里有一个**只在打包后才暴露**的缺陷（见下）。所以本版按 semver 升 patch 重发，
使 **tag 与 assets 同源** —— 你下载到的安装包，就是本 tag 的源码构建出来的。

## 🔴 修掉的关键缺陷：桌面版缺 9 个工具所需的模块

- **现象**：用 v1.5.0 的 spec 打出的 exe，在**安装版环境**里调用 `data.dragon_api` /
  `data.moneyflow_api` / `data.limit_up_api` 会抛 `ModuleNotFoundError` ⇒
  **9 个 Agent 工具不可用**（含 v1.5.0 的主打：全部 7 个龙虎/打板工具）。
- **机理**：工具注册表用**字符串晚绑定**（`module="data.dragon_api"`，真正 import 在其
  `importlib.import_module(变量)`），PyInstaller 只做静态分析 ⇒ 收不到这些模块，
  而且**构建不报错、`warn-*.txt` 不提示、源码态 674 条测试全绿**（模块就在磁盘上）。
- **修法**：spec 注入项目根到 `sys.path` 后 `collect_submodules("data")`（16 个模块，
  **与磁盘 `data/*.py` 逐一对账**，对不上直接构建失败）；并新增两条**构建后校验**（见下）。
- **连带修复**：打包环境 `desktop/build_env` 此前**未安装 langgraph** ⇒ 构建日志有 4 条
  `ERROR: Hidden import 'langgraph.*' not found`，exe 实际不含**图编排**能力。已补齐依赖。
- **代价**：体积 83.19 → **87.11 MiB**（langgraph 全家 + data 包全量）。

## 🆕 新增：两条「构建后校验」

| 脚本 | 回答什么问题 |
|---|---|
| `scripts/verify_bundle.py` | 被字符串引用的模块**是否都进了包**（三类字面量；含 **TOC 新鲜度断言**，防「改完没重建却报 ✓」） |
| `scripts/verify_exe.py` | **解开 exe 本体**：模块数与 TOC 同源 / 工具依赖模块齐全 / **exe 内 VERSION 常量 == 源码** / exe 比源码新 |

> 为什么必须放在构建后：这一类缺陷**源码态测试原理上覆盖不到**（上面 674 条测试全绿就是证明）。

## 🔧 版本口径统一

- 此前应用内 `/api/health` 报 **1.2.0**、`frontend/package.json` 也是 1.2.0，而对外 tag 已到 v1.5.0
  —— **自洽但不反映发布版本**（旧的一致性测试只钉「内部一致」，钉不住「与发布版本对应」）。
- 现统一为 **1.5.1**，并把「仓库外三面」也纳入回归锁：
  应用内 / package.json / FastAPI(version=) / **CHANGELOG 顶部** / **安装器 `.iss`** / **打包 `.bat` 输出名**（六面）。
- `server/main.py` 改为**引用同一常量**（不再硬编码版本号）。

## ✅ 验收（本机实跑，非推断）

```
pytest -q                     → 676 passed
python scripts/verify_bundle.py → exit 0（TOC 新鲜 / 动态导入面 15-15 在包内）
python scripts/verify_exe.py    → exit 0（A/B/C/D 全过；exe 内 VERSION = 1.5.1）
spec 构建期打印                 → data 包 16 个模块（磁盘 16 个，全数对齐）
```

产物：`invest-concierge.exe` **87.11 MiB**（`CEABD44D…` 系，本版重出）｜
`invest-concierge-setup-v1.5.1.exe` **87.81 MiB**（同上）

## ⚠️ 已知边界（如实列出）

- **未做 GUI 双击冒烟**：项目有 GUI 禁令（禁起桌面壳/exe、禁强杀 GUI 进程），
  按 `docs/PACKAGING.md §5` 的六项清单需**人工**执行；本轮替代验证是源码态 `/api/health`
  + 上述两条构建后校验。
- 检索/判官等**指标未复测**（`A3a 0.863`、`judge_fp 0.097` 等仍为历史结论，README 与 ROADMAP §5 均带限定）。
- 演示素材（`assets/demo/`）是**由实机截图合成的页面巡览**，不是屏幕录制。
- 数据来自免费公开接口，可能延迟或错误；本项目不构成任何投资建议。

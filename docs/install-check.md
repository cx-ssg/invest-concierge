# 安装验证留痕（干净环境）

> 目的：证明新用户「clone → pip install → 启动」全链路可用。发布前每次改依赖后重跑本验证。

- 日期：2026-09-01
- 方法：全新 Python venv（非项目开发环境，Windows 11 + Python 3.11）
- 步骤与结果：

| 步骤 | 结果 |
|---|---|
| `python -m venv` + `pip install -r requirements.txt pytest` | ✅ **189 秒**（PyPI 直连；预算 ≤10 分钟） |
| 依赖导入冒烟 | ✅ streamlit 1.63.0 / akshare 1.18.94 / numpy 2.4.6 / pandas / requests / openai / matplotlib |
| 核心模块导入 | ✅ web_agent / agent_core（11 工具）/ stock_fundamentals / common（clean_number 等） |
| 真实启动 + 前端校验 | ✅ headless 启动；夜航蓝 tokens 生效（`.stApp` 背景 = #0C111E）；双轨切换金色选中态生效 |

## 已知版本注意

- **streamlit ≥1.6x 重构了 segmented control 的 DOM**（React Aria，`data-variant` + `aria-checked`，
  不再是 1.58 的 `kind` 属性）。`styles.py` 已内置 **1.58 / ≥1.6x 两套兼容选择器**（含高特异度覆盖），
  两个版本下双轨切换均为金色药丸选中态。
- numpy 2.x 可用（页面已规避 pyarrow 图表渲染）；若本地装有旧版 pyarrow/numexpr 请升级：
  `pip install -U pyarrow numexpr bottleneck`。
- CI（GitHub Actions，全新 ubuntu runner）以 3.9/3.11 双版本矩阵跑同一套测试，作为第二重干净环境留痕。

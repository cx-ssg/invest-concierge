# invest-concierge · 投资私人管家

[![CI](https://github.com/cx-ssg/invest-concierge/actions/workflows/ci.yml/badge.svg)](https://github.com/cx-ssg/invest-concierge/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.9%2B-blue)]()
[![License](https://img.shields.io/badge/License-MIT-green)]()

> A股/基金 AI 私人顾问 —— 开源 · 免费数据 · 桌面版 / 网页版双形态
>
> **投资私人管家（Invest Concierge）**：把财报、估值、资金面翻译成普通人能看懂的话。

## 🖼️ 界面预览

| AI 对话（快捷问题直发） | 基金 · 资产总览 |
|:---:|:---:|
| ![AI 对话](assets/screenshots/chat-light.png) | ![资产总览](assets/screenshots/dashboard-dark.png) |

| 基金 · 持仓管理 | 股票 · 综合诊断 |
|:---:|:---:|
| ![持仓管理](assets/screenshots/portfolio-light.png) | ![综合诊断](assets/screenshots/diagnosis-dark.png) |

*浅色 / 暗色双主题，右上角一键切换。*

## ⚠️ 免责声明（请先阅读）

- 本项目仅供**学习与技术研究**，不构成任何投资建议或操作依据。
- 股市有风险，入市需谨慎；据此操作，风险自担。
- 项目数据来自公开免费接口（AkShare / 天天基金 / 新浪财经等），可能存在延迟或错误，请以官方披露信息为准。

## ✨ 这是什么

一个开源的 **A股与基金分析助手**：不依赖任何收费数据源，克隆下来就能跑。内置 AI 能力（可选接入 DeepSeek），把财报、估值、资金面翻译成普通人能看懂的话。

当前已上线 6 个 live 页面（React 前端，桌面壳 / 浏览器双入口）：

| 页面 | 说明 |
|---|---|
| 💬 AI 对话（首页） | 投资问答助手：SSE 流式输出 + 模型原生思考流 + 工具调用时间线（**24 个工具**：行情/财报/估值/资金流/搜索/回测/文档检索…） |
| 📊 基金 · 资产总览 | 总资产与持仓收益一览 |
| 💼 基金 · 持仓管理 | 录入持仓，自动追踪收益与当日实时估值 |
| 📔 基金 · 投资日记 | 记录每笔操作的理由，与未来的自己对话 |
| 🩺 股票 · 综合诊断 | 基本面 / 排雷 / 护城河 / 估值 / 财报三表 / AI 辩论 六引擎体检 |
| ⚙️ 设置 | API Key 状态 / 应用信息 |

> 🚧 **开发中**：回测 / 定投 / 基金对比 / 涨停复盘等能力**工具层已就绪**（AI 对话可直接调用），对应独立页面仍在路线图中迭代（见 [docs/ROADMAP.md](docs/ROADMAP.md)，欢迎提 issue）。

## 🚀 快速开始（三种方式，任选其一）

### 方式一：下载安装包（推荐，零 Python 环境）

到 [Releases](https://github.com/cx-ssg/invest-concierge/releases/latest) 下载：

- **`invest-concierge-setup-v*.exe`** —— 安装器，双击安装（用户级，无需管理员），开始菜单/桌面生成快捷方式
- **`invest-concierge.exe`** —— 绿色单文件，双击即用

启动后进入设置页填入 DeepSeek API Key（[免费注册领取](https://platform.deepseek.com/)）即可使用 AI 功能；不填 Key 也能看行情、记持仓、写日记（AI 对话会友好引导）。

> exe 未做代码签名，首次运行如遇 SmartScreen 提示，点"更多信息 → 仍要运行"。
> 需要 Edge WebView2 Runtime（Win10/11 一般自带）；无图形环境自动回退浏览器模式，功能零损失。

### 方式二：源码运行（已装 Python）

环境要求：**Python 3.9+**（Windows 安装时勾选 *Add Python to PATH*）。

```bash
# 1. 安装依赖（只需一次）
pip install -r requirements.txt
```

```text
# 2. 双击 desktop\start.bat
```

启动后会自动完成：内嵌 FastAPI 后端（127.0.0.1:8000，被占用时自动换空闲端口）→ 打开原生桌面窗口（pywebview 渲染前端）→ 关窗最小化到系统托盘，托盘「退出」结束程序。

- 桌面上不了/没图形环境也别慌：`desktop\launcher.py` 会自动回退为**浏览器模式**，功能零损失。

### 方式三：开发者 —— 源码直接跑

```bash
git clone https://github.com/cx-ssg/invest-concierge.git
cd invest-concierge
pip install -r requirements.txt

# 构建前端（生产模式产物 frontend/dist；构建用相对 /api，任意端口同源可用）
cd frontend
npm install
npm run build
cd ..

# 桌面版（等价于 start.bat）
python desktop\launcher.py

# 或纯浏览器模式（不起 GUI）
python desktop\launcher.py --browser
```

前端开发（热更新）：

```bash
# 终端 1：起 Vite dev server
cd frontend && npm run dev

# 终端 2：桌面壳指向 dev server
python desktop\launcher.py --mode dev
```

### 方式三：纯网页模式

```bash
# 前提：已执行过 npm run build（frontend/dist 存在）
uvicorn server.main:app --host 127.0.0.1 --port 8000
```

浏览器打开 **http://127.0.0.1:8000** 即可。

## 🔑 配置 DeepSeek API Key（可选）

AI 对话与诊断 AI 辩论需要 Key；**不配置时其余功能完整可用**（无 Key 会在页面显示引导卡，不会报错）。

1. 到 [DeepSeek 开放平台](https://platform.deepseek.com/) 注册并创建 API Key（有免费额度）。
2. **复制 `.env.example` 为根目录 `.env`，填入你的 Key：**

```bash
# Windows PowerShell
Copy-Item .env.example .env
# 然后编辑 .env，把 DEEPSEEK_API_KEY= 后面填上你的 Key
```

```text
DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx
```

> ✅ `.env` 已被 `.gitignore` 忽略，真实 Key **不会**被提交到仓库；`.env.example` 只是不含密钥的模板。
> 💡 也可以不建 `.env`，直接设置系统环境变量 `DEEPSEEK_API_KEY`（系统变量优先级更高）。

## 🏗️ 架构

```mermaid
flowchart LR
    subgraph 客户端
      A[桌面壳<br/>pywebview + 托盘]
      B[浏览器]
    end
    A --> C[FastAPI · 127.0.0.1]
    B --> C
    C --> H[frontend/dist<br/>React 静态页面]
    C --> D[services/ 业务服务层]
    D --> E[data/ 数据层<br/>AkShare/新浪/腾讯/东财 免费源]
    D --> R[utils/rag 私域知识层<br/>BM25 + 向量 + 证据分档]
    D --> F[(SQLite 本地库)]
    D --> G[DeepSeek API · 可选]
```

- **前端**：React 19 单页应用（三区壳：标题栏 / 侧边栏 / 状态栏），通过 HTTP + SSE 与后端通信。
- **后端**：FastAPI 提供 REST（持仓/日记/诊断/设置）+ SSE（AI 对话流式事件：`status → reasoning → tool_start/tool_end → done`）。
- **数据层**：`data/` 模块统一走缓存 + fallback 降级（弱网自动切备用源，失败显示「--」不崩溃）。
- **Agent 引擎**：`utils/agent_core.py` 工具注册表（**24 个工具**，晚绑定 importlib）+ 8 轮规划循环，`utils/agent_memory.py` 会话摘要注入。
- **知识层（M1）**：`utils/rag/` 私域文档检索（切块 / 向量 / BM25+RRF 混合 / 证据分档弃权），采集与评测脚本在 `scripts/rag_*.py`。

## 📁 目录结构

```text
invest-concierge/
├─ server/            FastAPI 路由 + frontend/dist 静态托管（入口：server.main:app）
├─ services/          业务服务层（agent / diagnosis / holdings / diary / settings / status）
├─ frontend/          React 19 前端（Vite + TypeScript + Tailwind v4）→ 构建产物 dist/
├─ desktop/           桌面壳（launcher.py 主入口 / backend.py 内嵌 uvicorn / tray.py 托盘 / start.bat）
├─ data/              数据层（AkShare 等免费数据源 + SQLite 持久化 + 缓存/降级）
├─ utils/             AI 引擎与 Agent（ai_helper / agent_core 工具注册表 / agent_memory）
├─ utils/rag/         私域知识层（chunker / embed / bm25 / hybrid / evidence / retrieve / store）
├─ scripts/           采集与评测脚本（rag_ingest*.py / rag_eval.py / rag_rerank_probe.py）
├─ pages/             旧 Streamlit 页面（保留备查，不参与新 UI；入口 app.py）
├─ tests/             **353 个 pytest 用例**（工具契约 / 排雷与估值 / 记忆 / RAG 检索与评测 / 采集与切块）
├─ assets/            设计素材（mockups）
├─ .env.example       环境变量模板（复制为 .env 使用）
├─ requirements.txt   Python 依赖
└─ docs/              文档（架构 / 路线图 / 贡献指南 / 验收记录）
```

## 🧰 技术栈

| 层 | 技术 |
|---|---|
| 后端 | FastAPI + uvicorn + pydantic（REST + SSE 流式） |
| 前端 | React 19 + Vite 8 + TypeScript + Tailwind CSS v4 |
| 桌面 | pywebview（WebView2）+ pystray 托盘 + Pillow |
| 数据 | AkShare（免费行情/财报/估值）+ SQLite + pandas / numpy |
| AI | DeepSeek API（OpenAI 兼容 SDK；工具调用 + 推理思考流） |

## ⚠️ 已知限制

- **免费数据源波动**：行情/财报来自免费公开接口，网络弱或被限速时自动 fallback（如腾讯/新浪/百度），数据可能延迟或缺失；akshare 接口随版本变动，页面一律降级显示「--」，不会崩溃。
- **桌面壳平台**：依赖 Edge WebView2 Runtime（Win10/11 一般自带）；无图形环境或缺少 pywebview 时自动回退浏览器模式。Linux/macOS 建议直接使用**网页模式**。
- **AI 依赖网络与 Key**：未配置 DeepSeek Key 时 AI 功能展示引导卡；弱网下 AI 流式对话可能超时。
- **知识库需自行构建**：语料库与向量（`*.db` / `corpus/`）不随仓库分发，需跑 `scripts/rag_ingest*.py`；未构建时文档检索会如实告知「未找到」。
- **本地向量模型**：默认走本地 Ollama `bge-m3`；未安装 Ollama 时可改用 OpenAI 兼容端点。
- **当前 live 页面 6 个**：其余规划页（回测/定投/基金对比等）数据层函数已就绪，见 [docs/ROADMAP.md](docs/ROADMAP.md)。

## 🔍 私域知识层（带引用的文档检索）

除了实时行情，项目内置**本地文档检索**：把上市公司公告 / 研报 / 财报原文按语义切块、向量化入库，
回答时**返回原文片段 + 来源 + 日期**，而不是让模型凭空作答。

| | 说明 |
|---|---|
| 工具 | `retrieve_docs`（AI 对话里直接问「某公司某期经营现金流多少」） |
| 存储 | 本地 SQLite（`kb.db`）+ `bge-m3` 向量（1024 维，走本地 Ollama，可降级到 OpenAI 兼容端点） |
| 检索 | 混合检索：BM25 + 向量 → RRF 融合，支持同文档限额 |
| **证据分档** | 相关度不足时**主动弃权**，返回「证据不足档 —— 引用前请自行核验」，而不是硬答 |
| 采集 | 巨潮资讯公告 API / **PDF 全文抽取**（`pypdf`）→ 切块 → 向量化，全程脚本可复现 |

**为什么强调引用与弃权**：投资领域最怕「听起来很确定的胡说」。
这里的取舍是 —— **宁可回答「没找到」，也不编一段**。

### 首次使用（知识库不入库，需自行构建）

语料库与向量（`*.db` / `corpus/`）**不随仓库分发**（体积与版权原因），首次使用请：

```bash
# 1. 确保本地 Ollama 已就绪并拉取向量模型
ollama pull bge-m3

# 2. 采集 + 入库（示例：拉取一只股票的公告）
python scripts/rag_ingest.py --code 600519

# 3. 大文档（半年报/年报全文）走巨潮 PDF
python scripts/rag_ingest_pdf.py --code 600519
```

### 评测（可复现）

评测集随仓库分发（`tests/golden/rag/`），可自行复跑：

```bash
python scripts/rag_eval.py --split holdout          # 指标总览
python scripts/rag_eval.py --split tuning --scan    # 阈值敏感性扫描
python scripts/rag_rerank_probe.py --split holdout  # LLM 重排探针（会调模型）
```

当前指标（holdout 21 条正例）：`Recall@5 = 0.952`、`MRR@10 = 0.706 ~ 0.738`（启用 LLM 重排后）。
**未达标项与已知边界同样记录在案**，见 [docs/M1_EVAL_REPORT.md](docs/M1_EVAL_REPORT.md) 与
[docs/COVERAGE_DESIGN.md](docs/COVERAGE_DESIGN.md)。

> ⚠️ **诚实边界**：内置能力用于打通链路与评测，语料需按自己的标的自行构建；
> 文档检索只负责「**找得到、可核对**」，**不构成投资建议**。

## 🧭 差异化

| | invest-concierge | 常见行情工具 |
|---|---|---|
| 数据源 | 全免费（AkShare 等） | 常需付费 Key |
| 上手 | 克隆即跑，零配置可用 | 需自己搭环境 |
| AI | 多角色辩论 + 工具调用 + 思考流（非单问答） | 多为单轮问答 |
| 体检 | 排雷 + 护城河 6 维 + 估值分位 + 三表 | 多为单指标展示 |

## 🧪 测试与质量

- 后端：`pytest tests/`（**353 例**，全部通过）
- 前端：`cd frontend && npm run build`（tsc 类型检查 + vite 构建）
- 桌面壳：`python desktop\smoke_test.py`（依赖 / dist 产物 / 端口策略 / 内嵌后端 / GUI·托盘冒烟）
- CI：GitHub Actions 双矩阵（Python 3.9 / 3.11）+ gitleaks 密钥扫描

## 🤖 Built with AI

本项目通过多 Agent 协作开发（AI 辅助编程工作流）：规划拆解 → 模块化实现 → 测试先行 → 独立审计。

## 📜 文档

- [架构详解](docs/ARCHITECTURE.md)
- [路线图](docs/ROADMAP.md)
- [贡献指南](docs/CONTRIBUTING.md)
- [诊断页验收记录](docs/verification.md)
- [M1 检索评测报告](docs/M1_EVAL_REPORT.md)（指标、未达标项与证据）
- [能力覆盖与边界](docs/COVERAGE_DESIGN.md)

## 📄 许可证

[MIT](LICENSE)

---

*数据仅供参考，不构成投资建议。市场有风险，投资需谨慎。*
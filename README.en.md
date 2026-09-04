# Invest Concierge

[![CI](https://github.com/cx-ssg/invest-concierge/actions/workflows/ci.yml/badge.svg)](https://github.com/cx-ssg/invest-concierge/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.9%2B-blue)]()
[![License](https://img.shields.io/badge/License-MIT-green)]()

> Open-source A-share & fund AI personal investment assistant. Free data, desktop & web.
> 中文版：[README.md](README.md)

## ⚠️ Disclaimer

For **educational and research purposes only**. Not investment advice. Data comes from free public APIs (AkShare / Eastmoney / Sina etc.) and may be delayed or inaccurate.

## What

An open-source assistant for China A-share stocks & funds — no paid data feeds, works out of the box. Optional DeepSeek AI translates financial reports, valuation and fund flows into plain language.

Live pages (React frontend, desktop shell / browser):

- 💬 **AI Chat** (home): SSE streaming with native reasoning chain + tool-call timeline (11 tools: quotes, reports, holdings, diary…).
- 📊 **Fund dashboard**: total assets & P&L overview.
- 💼 **Portfolio**: track holdings, P&L and daily estimates.
- 📔 **Investment diary**: record the reasoning behind every trade.
- 🩺 **Stock diagnosis**: fundamentals / financial minefields / moat / valuation / reports / AI debate — six engines.
- ⚙️ **Settings**: API key status.

## Quick Start (pick one)

Requirements: **Python 3.9+**.

### Option 1 — Desktop app (recommended for end users)

```bash
pip install -r requirements.txt
```

Then double-click `desktop\start.bat`. It starts an embedded FastAPI backend (127.0.0.1:8000, auto-picks a free port) and opens a native pywebview window; closing the window minimizes to the system tray. Falls back to browser mode automatically when no GUI is available.

### Option 2 — Run from source (developers)

```bash
git clone https://github.com/cx-ssg/invest-concierge.git
cd invest-concierge
pip install -r requirements.txt

cd frontend
npm install
npm run build
cd ..

python desktop\launcher.py            # desktop app
python desktop\launcher.py --browser  # browser mode only
```

Dev mode with hot reload: `cd frontend && npm run dev` (terminal 1), then `python desktop\launcher.py --mode dev` (terminal 2).

### Option 3 — Pure web

```bash
# requires frontend/dist (run npm run build first)
uvicorn server.main:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000** in your browser.

## DeepSeek API Key (optional)

Everything works without a key (AI features show a guide card). To enable AI:

1. Create a key at <https://platform.deepseek.com/> (free quota available).
2. **Copy `.env.example` to `.env`** in the project root and fill in `DEEPSEEK_API_KEY=sk-...`.

The system environment variable `DEEPSEEK_API_KEY` takes precedence. `.env` is gitignored — real keys are never committed; `.env.example` is a keyless template.

## Architecture

````text
desktop shell (pywebview + tray) ─┐
browser ──────────────────────────┴─► FastAPI (127.0.0.1)
                                        ├─► frontend/dist (React static)
                                        ├─► services/ (business layer)
                                        ├─► data/ (AkShare/Sina/Tencent free feeds)
                                        ├─► SQLite (local storage)
                                        └─► DeepSeek API (optional)
````

- **Frontend**: React 19 SPA (title bar / sidebar / status bar), REST + SSE.
- **Backend**: FastAPI REST (holdings / diary / diagnosis / settings) + SSE agent stream (`status → reasoning → tool_start/tool_end → done`).
- **Agent engine**: `utils/agent_core.py` tool registry (late-binding importlib) + 8-round planning loop; `utils/agent_memory.py` session summarization.

## Tech Stack

FastAPI + uvicorn · React 19 + Vite 8 + TypeScript + Tailwind v4 · pywebview + pystray · SQLite · AkShare · DeepSeek API · pandas / numpy

## Known Limitations

- Free data feeds can be flaky: automatic fallback (Tencent/Sina/Baidu) on weak networks; pages degrade to `--` instead of crashing.
- Desktop shell requires Edge WebView2 Runtime (usually preinstalled on Win10/11) and prefers Windows; use the web mode on Linux/macOS.
- 6 pages live today; the roadmap (backtest, DCA, fund compare…) has data-layer functions ready but UI pending — see `docs/ROADMAP.md`.
- `pages/` still contains the legacy Streamlit app (`app.py`) — kept for reference, not part of the new UI.

## Testing

- Backend: `pytest tests/` (110 cases)
- Frontend: `cd frontend && npm run build`
- Desktop shell: `python desktop\smoke_test.py`
- CI: GitHub Actions double matrix (Python 3.9 / 3.11) + gitleaks secret scan.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Roadmap](docs/ROADMAP.md)
- [Contributing](docs/CONTRIBUTING.md)
- [Verification notes](docs/verification.md)

## License

MIT — see [LICENSE](LICENSE).

*Not financial advice. Trade at your own risk.*
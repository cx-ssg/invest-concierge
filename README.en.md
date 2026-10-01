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

- 💬 **AI Chat** (home): SSE streaming with native reasoning chain + tool-call timeline (**24 tools**: quotes, reports, valuation, fund flows, search, backtest, document retrieval…).
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
                                        ├─► utils/rag (private document layer: BM25 + vectors + evidence tiers)
                                        ├─► SQLite (local storage)
                                        └─► DeepSeek API (optional)
````

- **Frontend**: React 19 SPA (title bar / sidebar / status bar), REST + SSE.
- **Backend**: FastAPI REST (holdings / diary / diagnosis / settings) + SSE agent stream (`status → reasoning → tool_start/tool_end → done`).
- **Agent engine**: `utils/agent_core.py` tool registry (**24 tools**, late-binding importlib) + 8-round planning loop; `utils/agent_memory.py` session summarization.
- **Knowledge layer**: `utils/rag/` hybrid document retrieval (chunk / embed / BM25+RRF / evidence tiers); ingestion & eval scripts under `scripts/rag_*.py`.

## Private Document Retrieval

Beyond live quotes, the project ships a **local document retrieval layer**: filings / research notes / financial-report text are chunked, embedded and stored locally, and answers come back with **the source snippet + document + date** instead of being invented by the model.

| | |
|---|---|
| Tool | `retrieve_docs` |
| Store | local SQLite (`kb.db`) + `bge-m3` vectors (1024-d, via local Ollama, with an OpenAI-compatible fallback) |
| Retrieval | hybrid: BM25 + vectors → RRF fusion, with a per-document cap |
| **Evidence tiers** | when relevance is insufficient it **abstains** ("evidence insufficient — verify before quoting") instead of guessing |
| Ingestion | CNINFO announcement API / **PDF full-text extraction** (`pypdf`), fully scriptable |

Design stance: in investing, a confident-sounding hallucination is the worst failure mode — so **"not found" beats a fabricated answer**.

> ⚠️ The corpus and vectors (`*.db` / `corpus/`) are **not shipped** with the repo. Build them yourself:
> `ollama pull bge-m3` → `python scripts/rag_ingest.py --code 600519` (add `scripts/rag_ingest_pdf.py` for full reports).
> The evaluation set **is** shipped (`tests/golden/rag/`) and can be re-run with `python scripts/rag_eval.py --split holdout`.
> Current production: `Recall@5 = 0.952`, `MRR@10 = 0.605` (**LLM rerank is NOT wired into production** —
> `0.706 ~ 0.738` comes from the offline probe `scripts/rag_rerank_probe.py` only; `git grep -i rerank -- utils/ services/ frontend/src` → 0 hits).
> ⚠️ Read those numbers **per state**, never across rows: baseline (old corpus, no cap) `1.000 / 0.702`
> → PDF full-text corpus `0.857 / 0.593` → +per-doc cap (current) `0.952 / 0.605` — **neither recall nor ranking
> is fully back to baseline**.
> ⚠️ Eval-set nature: holdout **negatives** are a clean holdout (`v2`, enforced), but the **21 positives are still `v1` —
> already seen during threshold tuning** (the script itself prints "holdout — already used for threshold selection").
> `max_per_doc=2` is itself a holdout-scanned value ⇒ **`0.952` carries fitting, not a generalisation promise**.
> Missing targets are documented in [docs/M1_EVAL_REPORT.md](docs/M1_EVAL_REPORT.md).

## Tech Stack

FastAPI + uvicorn · React 19 + Vite 8 + TypeScript + Tailwind v4 · pywebview + pystray · SQLite · AkShare · DeepSeek API · pandas / numpy

## Known Limitations

- Free data feeds can be flaky: automatic fallback (Tencent/Sina/Baidu) on weak networks; pages degrade to `--` instead of crashing.
- Desktop shell requires Edge WebView2 Runtime (usually preinstalled on Win10/11) and prefers Windows; use the web mode on Linux/macOS.
- 6 pages live today; the remaining features (backtest, DCA, fund compare, limit-up review) are **already available as agent tools** — dedicated pages are still on the roadmap, see `docs/ROADMAP.md`.
- **Knowledge base must be built locally**: the corpus and vectors (`*.db` / `corpus/`) are not shipped; without them document retrieval will honestly report "not found".
- **Local embedding model**: defaults to Ollama `bge-m3`; an OpenAI-compatible endpoint can be used instead.
- `pages/` still contains the legacy Streamlit app (`app.py`) — kept for reference, not part of the new UI.

## Testing

- Backend: `pytest tests/` (**353 cases**)
- Frontend: `cd frontend && npm run build`
- Desktop shell: `python desktop\smoke_test.py`
- CI: GitHub Actions double matrix (Python 3.9 / 3.11) + gitleaks secret scan.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Roadmap](docs/ROADMAP.md)
- [Contributing](docs/CONTRIBUTING.md)
- [Verification notes](docs/verification.md)
- [M1 retrieval evaluation report](docs/M1_EVAL_REPORT.md)
- [Capability coverage & boundaries](docs/COVERAGE_DESIGN.md)

## License

MIT — see [LICENSE](LICENSE).

*Not financial advice. Trade at your own risk.*
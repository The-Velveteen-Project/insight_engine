# Velveteen Insight Engine

Velveteen Insight Engine is an AI-assisted editorial and portfolio system inside **The Velveteen Project**, a founder-led applied decision systems lab.

The goal is not generic content automation. The system is meant to turn signals such as news, papers, voice notes, repo progress, and technical reflections into structured portfolio and editorial opportunities with a sober, technical, anti-hype tone.

## Philosophy

- No hype
- No overengineering
- No empty automation
- Prefer deterministic rules when they are enough
- The LLM does not control system logic
- Clean, typed, maintainable code

## Current Scope

Implemented through **Phase 7**:

- **Phase 1**: FastAPI app, Telegram webhook, SQLite persistence
- **Phase 2**: Telegram parsing, reply and URL detection, deterministic classifier
- **Phase 3**: Voice note intake, Telegram audio download, non-fatal transcription
- **Phase 4**: External discovery service with arXiv and Hacker News, heuristic ranking, signal persistence
- **Phase 5**: GitHub insight service for public repos, heuristic ranking, signal persistence
- **Phase 6**: Structured editorial planning from persisted signals, deterministic decision logic plus optional LLM-assisted drafting
- **Phase 7**: Editorial plan persistence and minimal human approval workflow

## What The System Does Today

The engine can currently:

- ingest Telegram text and voice inputs
- persist normalized messages in SQLite
- discover external signals from arXiv and Hacker News
- inspect public GitHub repos for portfolio-relevant signals
- persist all discovered signals in `signals`
- generate a structured editorial plan from one to three signals
- persist editorial plans in `editorial_plans`
- move editorial plans through a small human review workflow:
  - `draft`
  - `approved`
  - `saved`
  - `discarded`

It does **not** yet publish to LinkedIn, sync to a website, or automate final approval decisions.

## Tech Stack

- Python 3.11+
- FastAPI
- Pydantic
- httpx
- SQLite
- OpenAI API
- pytest
- ruff
- mypy
- uv

## Project Structure

```text
app/
├── api/routes/        # FastAPI endpoints
├── core/              # Settings and shared config
├── db/                # SQLite setup and queries
├── domain/            # Internal domain models
├── integrations/      # Telegram, GitHub, arXiv, HN, OpenAI clients
├── prompts/           # Minimal prompt layer for structured generation
├── schemas/           # Pydantic request/response contracts
├── services/          # Discovery, GitHub insights, editorial planning
└── utils/

tests/                 # Unit and integration-style tests with mocked network
scripts/               # DB setup and webhook helper scripts
```

## Key Data Models

- `messages`: normalized Telegram intake events
- `signals`: persisted external and internal signals
- `editorial_plans`: persisted editorial proposals plus human-review status

## Main API Endpoints

### Health

- `GET /api/v1/health`

### Telegram

- `POST /api/v1/telegram/webhook`

### Discovery

- `GET /api/v1/discovery/suggest`

This endpoint is stateful: it discovers, ranks, persists returned signals, and then responds.

### GitHub Insights

- `GET /api/v1/github/insights/suggest`

This endpoint is also stateful: it suggests repo insights and persists them to `signals`.

### Editorial Planning

- `POST /api/v1/editorial/plan`
- `GET /api/v1/editorial/plans/{id}`
- `POST /api/v1/editorial/plans/{id}/approve`
- `POST /api/v1/editorial/plans/{id}/save`
- `POST /api/v1/editorial/plans/{id}/discard`

`POST /api/v1/editorial/plan` now generates the proposal, persists it as a draft, and returns the stored record.

State transitions:

- `draft -> approved | saved | discarded`
- `approved -> saved`
- `saved` is terminal
- `discarded` is terminal

## Local Setup

### 1. Install dependencies

```bash
make install
```

### 2. Configure environment

```bash
cp .env.example .env
```

At minimum, review:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_WEBHOOK_SECRET`
- `OPENAI_API_KEY` if you want real transcription or structured generation
- `GITHUB_TOKEN` if you want higher GitHub API limits

### 3. Initialize the database

```bash
make setup-db
```

### 4. Run the API locally

```bash
make dev
```

The app will be available at [http://localhost:8000](http://localhost:8000).

## Quality Checks

```bash
make test
make lint
make typecheck
```

## Example Workflow

1. Ingest a Telegram note or voice memo.
2. Discover related external signals from papers, news, or repo activity.
3. Persist those signals.
4. Generate a structured editorial plan from selected signal ids.
5. Review the saved draft plan through the editorial workflow.
6. Approve, save, or discard it manually.

## Status

The project is intentionally still small and API-first. The emphasis so far is:

- deterministic logic first
- good separation of concerns
- non-fragile integrations
- auditable editorial decisions

Later phases can build on this base for approval tooling, exports, and publication workflows without forcing the LLM to own the system.

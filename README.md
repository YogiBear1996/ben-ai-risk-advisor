# Ben — AI Governance Assistant

Ben is a chat assistant for AI governance practitioners, risk owners and project teams. People
talk to Ben on **Telegram** or **WhatsApp**. Ben answers questions on AI risk, controls, policy and
regulation from a **private document library** and cites its sources.

> "Which controls in our library apply to a GenAI customer-service chatbot?"
> "Is a CV-screening model high-risk under the EU AI Act, and what obligations follow?"
> "Draft a one-page risk assessment for this use case: …"

Ben is itself a governed AI system. It has an allowlist, webhook verification, an audit log with
CSV export, PII redaction, data retention, rate limiting, prompt-injection hygiene, an eval
suite and a [model card](docs/MODEL_CARD.md).

---

## Contents

1. [How it works](#how-it-works)
2. [Quick start (local, 5 minutes)](#quick-start-local-5-minutes)
3. [Configuration](#configuration)
4. [Knowledge library and ingestion](#knowledge-library-and-ingestion)
5. [Telegram setup](#telegram-setup)
6. [WhatsApp Cloud API setup](#whatsapp-cloud-api-setup)
7. [Running locally with webhooks (ngrok / cloudflared)](#running-locally-with-webhooks)
8. [Deploying with Docker](#deploying-with-docker)
9. [Governance features](#governance-features)
10. [Evaluation](#evaluation)
11. [Development](#development)
12. [CLI reference](#cli-reference)

---

## How it works

```
Telegram ─┐                                   ┌─ search_library ─► LanceDB (vectors + BM25, RRF fusion)
          ├─► ChannelAdapter ─► BenService ─► BenAgent (Claude + tools) ─┼─ get_control ────► SQL control table
WhatsApp ─┘   (parse/format/     allowlist        │                      └─ list_frameworks
               chunk replies)    rate limit       ▼
                                 commands     memory (last N turns) + audit log (SQLite/Postgres)
```

- **Channel adapters** (`src/ben/channels/`) turn platform webhooks into a normalised
  `IncomingMessage` and send replies with the right formatting, split to fit message limits.
  The core never knows which channel it is on.
- **BenService** (`src/ben/core/service.py`) runs the pipeline: dedupe → allowlist → rate
  limit → command or agent → memory → audit.
- **BenAgent** (`src/ben/core/agent.py`) calls Claude with three tools (`search_library`,
  `get_control`, `list_frameworks`) in a capped tool-use loop. The system prompt is in
  [`prompts/system.md`](prompts/system.md). Retrieved text is passed back inside
  `<retrieved_data>` tags as *data*, never as instructions.
- **Knowledge** (`src/ben/knowledge/`) loads PDF, DOCX, Markdown and XLSX. It splits documents
  on headings and on Article/Clause/Section markers, embeds them locally with fastembed
  (`BAAI/bge-small-en-v1.5`), and stores them in **LanceDB**. The AI Risk & Control Library XLSX
  is also stored as **structured rows** for exact `get_control` lookups and `ai_type` filtering.

**Why LanceDB:** it is embedded (just a directory on the data volume, no server to run), it
keeps chunk metadata next to the vectors, and it has a native BM25 full-text index. That makes
hybrid search simple: Ben runs vector search and keyword search, then fuses the two rankings
with Reciprocal Rank Fusion. Keyword search matters for exact terms such as "Annex III" or
"6.1.4".

## Quick start (local, 5 minutes)

Prerequisites: [uv](https://docs.astral.sh/uv/) (installs Python 3.12 for you) and an
Anthropic API key.

```bash
git clone <this repo> && cd ben-ai-risk-advisor
uv sync                          # installs Python 3.12 + dependencies
cp .env.example .env             # then set ANTHROPIC_API_KEY
uv run ben ingest ./knowledge    # index the sample library (downloads the embedding model once)
uv run ben chat                  # talk to Ben in your terminal
```

`ben chat` runs the same pipeline as Telegram and WhatsApp (memory, commands, audit), so you
can test Ben before connecting any channel. Try `/frameworks`, then ask *"Which controls apply to
a GenAI customer-service chatbot?"* and follow up with `/sources`.

> **No internet for the model download?** Set `EMBEDDING_BACKEND=hash` for an offline,
> keyword-only embedder. Use it only for smoke tests, because retrieval quality is lower.

## Configuration

All settings come from environment variables or `.env`. [`.env.example`](.env.example) lists
and explains every variable. The main ones:

| Variable | Purpose |
| --- | --- |
| `ANTHROPIC_API_KEY` | Claude API key |
| `BEN_MODEL` | Model ID (default `claude-sonnet-5`). This is the only place the model is set |
| `BEN_EFFORT` | Reasoning effort (`low`…`max`, default `medium`) |
| `BEN_HISTORY_TURNS` | Turns of conversation memory per chat (default 10) |
| `TELEGRAM_*` | Bot token, webhook secret, public URL, allowed user IDs |
| `WHATSAPP_*` | Access token, phone number ID, app secret, verify token, allowed numbers |
| `DATABASE_URL` | Empty = SQLite in `DATA_DIR`. For Postgres, use `postgresql+psycopg://…` |
| `REDACT_BEFORE_LOGGING` / `REDACT_BEFORE_MODEL` | PII redaction switches |
| `RETENTION_DAYS` / `AUDIT_RETENTION_DAYS` | Data retention |
| `RATE_LIMIT_PER_MINUTE` / `RATE_LIMIT_BURST` | Per-user rate limit |

**Switching to Postgres** only needs a config change: set `DATABASE_URL` and install the driver
with `uv sync --extra postgres` (the Docker image already includes it). Tables are created on
start-up.

## Knowledge library and ingestion

Put your documents in a folder (default `./knowledge`) and run:

```bash
uv run ben ingest ./knowledge          # only new or changed files are processed
uv run ben ingest ./knowledge --force  # re-process everything
uv run ben frameworks                  # what's loaded
```

- **Supported formats:** `.pdf` (with page numbers), `.docx` (heading styles and tables), `.md`,
  `.txt`, `.xlsx`.
- **Chunking** follows the document's structure. Headings and lines such as `Article 6`,
  `Clause 6.1.4`, `Section 3` or `Annex III` start a new section. A chunk never spans two
  sections, so every citation points to one clause.
- **Idempotent:** each file's SHA-256 and the embedding model are recorded. Re-runs skip
  unchanged files, re-index changed files and remove deleted files. Changing `EMBEDDING_MODEL`
  triggers a full re-index.
- **Live updates:** a running server picks up a new ingest within a few seconds. No restart
  needed.
- **Tips for better citations:** give each document a clear title (DOCX: *File → Properties →
  Title*; PDF: metadata title; Markdown: a single `# H1`). Ben cites titles as written.

### The AI Risk & Control Library (XLSX)

Any XLSX with a control-ID column is treated as the control library. Column names are matched
without regard to case or spacing, and common synonyms are accepted:

| Field | Accepted headers (examples) |
| --- | --- |
| `control_id` | Control ID, Control Ref |
| `risk_category` | Risk Category, Category, Risk Domain |
| `ai_type` | AI Type (`Traditional` / `GenAI` / `Both`) |
| `risk_description` | Risk Description, Risk |
| `control_description` | Control Description, Control |
| `owner` | Owner, Control Owner |
| `framework_mapping` | Framework Mapping, Frameworks |

Extra columns are kept and shown to Ben. The header row can be anywhere in the first 10 rows.
Other XLSX files are indexed as text, one passage per row.

The sample files in `knowledge/` are **placeholders** (paraphrased summaries). Replace them with
your real documents. `scripts/make_sample_knowledge.py` regenerates the sample DOCX, PDF and
XLSX.

## Telegram setup

1. **Create the bot.** In Telegram, open **@BotFather** and send `/newbot`. Choose a display
   name (e.g. *Ben – AI Governance*) and a username ending in `bot`. BotFather replies with a
   token: this is `TELEGRAM_BOT_TOKEN`.
2. **Add the command menu (optional).** Send `/setcommands` to BotFather, pick your bot, and paste:
   ```
   start - Introduction
   help - How to use Ben
   reset - Start a new conversation
   sources - Sources behind my last answer
   frameworks - Documents in my library
   ```
   Consider `/setjoingroups` → *Disable* so the bot can't be added to groups.
3. **Allow users.** Ben refuses anyone who isn't on `TELEGRAM_ALLOWED_USER_IDS`. The refusal
   message includes the person's numeric user ID, so the easiest way to onboard someone is to ask
   them to message Ben and send you the ID. (Alternatively, they can message **@userinfobot**.)
4. **Local development:** run `uv run ben telegram-poll`. This uses long polling, so no public
   URL is needed.
5. **Production:** set a random `TELEGRAM_WEBHOOK_SECRET` and your public
   `TELEGRAM_WEBHOOK_URL`, start the server, then run:
   ```bash
   uv run ben set-webhook            # registers <URL>/webhooks/telegram with the secret token
   uv run ben set-webhook --delete   # to go back to polling
   ```
   Telegram sends the secret in `X-Telegram-Bot-Api-Secret-Token` with every update, and Ben
   rejects requests without it (HTTP 401).

## WhatsApp Cloud API setup

1. **Create a Meta app.** Go to [developers.facebook.com](https://developers.facebook.com) →
   *My Apps* → *Create app*, choose the **Business** type, and link it to your Meta Business
   account.
2. **Add WhatsApp.** In the app dashboard, add the **WhatsApp** product. Under *WhatsApp → API
   Setup* you'll find a test phone number and its **Phone number ID** (`WHATSAPP_PHONE_NUMBER_ID`,
   not the phone number itself). Add your own number as a test recipient.
3. **Access token.** The token on the API Setup page expires after 24 hours. For anything
   lasting, create a **System User** in *Business Settings → Users → System users*, assign it the
   app and WhatsApp account, and generate a token with `whatsapp_business_messaging` and
   `whatsapp_business_management` permissions. Use it as `WHATSAPP_ACCESS_TOKEN`.
4. **App secret.** Go to *App settings → Basic → App secret* and use it as
   `WHATSAPP_APP_SECRET`. Ben uses it to verify the `X-Hub-Signature-256` header on every
   webhook.
5. **Webhook.** Pick any random string for `WHATSAPP_VERIFY_TOKEN`, start Ben, then go to
   *WhatsApp → Configuration → Webhook → Edit*:
   - Callback URL: `https://<your-host>/webhooks/whatsapp`
   - Verify token: the same `WHATSAPP_VERIFY_TOKEN`
   - Click **Verify and save**. Meta calls `GET /webhooks/whatsapp` and Ben echoes the challenge.
   - Under *Webhook fields*, **subscribe to `messages`**.
6. **Allow users.** Add numbers to `WHATSAPP_ALLOWED_NUMBERS` in international format (e.g.
   `+971501234567`).
7. **Going live.** Add a real business phone number, complete Business Verification and set the
   app to *Live*. Ben only replies to messages users send first, so it stays within WhatsApp's
   24-hour customer-service window and needs no message templates.

## Running locally with webhooks

Webhooks need a public HTTPS URL. Two options:

```bash
uv run ben serve --port 8000
# in another terminal, either:
ngrok http 8000
# or:
cloudflared tunnel --url http://localhost:8000
```

Use the HTTPS URL the tunnel prints as `TELEGRAM_WEBHOOK_URL` (then run `uv run ben set-webhook`)
and as the WhatsApp callback base URL. Check `https://<tunnel>/health`.

## Deploying with Docker

The target is a generic Linux VM with Docker Compose and Caddy for TLS.

```bash
# on the VM
git clone <this repo> && cd ben-ai-risk-advisor
cp .env.example .env && nano .env              # secrets, allowlists, TELEGRAM_WEBHOOK_URL
# put your documents in ./knowledge (mounted read-only into the container)

docker compose build                            # bakes the embedding model into the image
docker compose run --rm ben ben ingest /knowledge
docker compose up -d                            # Ben on 127.0.0.1:8000

# TLS + public exposure: point DNS at the VM, set your domain in deploy/Caddyfile, then
docker compose --profile proxy up -d
docker compose exec ben ben set-webhook         # Telegram
```

- **Data** (SQLite DB, vector index, exports) lives in the `ben-data` volume. Back it up. Use
  Postgres via `DATABASE_URL` for managed backups.
- The Caddyfile exposes only `/webhooks/*` and `/health`. The container runs as a non-root user.
- **Update documents:** change `./knowledge`, then run
  `docker compose run --rm ben ben ingest /knowledge`. The running server picks up the changes.
- **Audit export:** `docker compose run --rm ben ben audit-export --out /data/audit.csv`, then
  `docker compose cp ben:/data/audit.csv .`
- **Scaling:** the rate limiter, duplicate-delivery check and retention scheduler run in-process,
  so run a **single replica**. Running several would need Redis and an external scheduler.
- **Air-gapped build:** `docker compose build --build-arg PRELOAD_EMBEDDINGS=0` skips the model
  download. The model is then fetched on first ingest.

## Governance features

| Control | Implementation |
| --- | --- |
| Access control | `TELEGRAM_ALLOWED_USER_IDS` / `WHATSAPP_ALLOWED_NUMBERS`. An empty list denies everyone. Refusals are polite and audited |
| Webhook authenticity | Telegram secret-token header; WhatsApp `X-Hub-Signature-256` HMAC and verify-token handshake. All comparisons are constant-time |
| Audit trail | Every exchange: timestamp, user, channel, question, retrieved sources, tool calls, model ID, token usage (including cache), latency, answer, status, and any unverified control IDs. Export with `ben audit-export --since 2026-01-01 --out audit.csv` |
| PII redaction | Emails, phone numbers, Emirates ID, passport numbers and booking references (PNRs) are redacted before logging and memory (`REDACT_BEFORE_LOGGING`). Redacting before the model call is optional (`REDACT_BEFORE_MODEL`) |
| Retention | Conversations are deleted after `RETENTION_DAYS` and audit entries after `AUDIT_RETENTION_DAYS`, by a daily job inside `ben serve` (`CLEANUP_HOUR_UTC`) or on demand with `ben cleanup` |
| Prompt-injection hygiene | Library text goes back to the model inside `<retrieved_data>` tags, with closing tags escaped. The system prompt tells Ben to treat library and pasted content as data. Tool rounds are capped |
| Hallucination checks | Control-ID-shaped tokens in answers are checked against the library, and unknown IDs are flagged in the audit log and evals |
| Rate limiting | Per-user token bucket (`RATE_LIMIT_PER_MINUTE`, `RATE_LIMIT_BURST`) |
| Secrets | Loaded only from the environment as `SecretStr`. Never logged; `httpx` request logging is silenced because Telegram URLs contain the token |

See [`docs/MODEL_CARD.md`](docs/MODEL_CARD.md) for purpose, limitations and risks.

## Evaluation

[`evals/questions.yaml`](evals/questions.yaml) holds 21 sample questions for the sample
library: library questions, a drafting task, control lookups, out-of-scope questions and a
prompt-injection attempt. Replace them with real questions once your documents are loaded.

```bash
uv run ben eval                      # full run (needs ANTHROPIC_API_KEY)
uv run ben eval --judge              # also grade groundedness with Claude (structured output)
uv run ben eval --retrieval-only     # no model calls: are the right sources retrieved?
uv run ben eval --only cv-screening-high-risk
```

The report prints a summary and writes JSON to `reports/`. Metrics:

- **citation_accuracy:** share of expected sources and control IDs that were both retrieved and
  cited in the answer.
- **grounded_rate:** the answer has no invented control IDs, and every `[bracketed citation]`
  maps to a source the tools actually returned.
- **key_point_coverage:** expected facts present in the answer.
- **decline_accuracy:** Ben says so when the library doesn't cover a question, and refuses
  injection attempts.
- **retrieval_recall:** the expected sources and sections are among the retrieved passages.
- Plus latency and token totals.

## Development

```bash
uv sync                               # includes dev tools
uv run pytest                         # all tests (Claude and channel APIs are mocked)
uv run ruff check . && uv run ruff format --check .
uv run python scripts/make_sample_knowledge.py
```

Tests use a deterministic hash embedder and scripted fake Claude responses, so they run offline
in a few seconds.

```
src/ben/
  config.py            settings (pydantic-settings)
  cli.py               `ben` commands
  runtime.py           wiring
  core/                agent, tools, service pipeline, commands, models
  channels/            ChannelAdapter, Telegram, WhatsApp, formatting
  knowledge/           loaders, chunker, control library, embeddings, LanceDB store, ingest
  storage/             SQLModel tables, repositories (memory, audit, retention)
  security/            allowlist, rate limiter, PII redaction
  web/app.py           FastAPI: /health, /webhooks/telegram, /webhooks/whatsapp
  evals/runner.py      eval scoring
prompts/system.md      Ben's behaviour
```

## CLI reference

| Command | What it does |
| --- | --- |
| `ben chat` | Terminal chat through the full pipeline |
| `ben ingest [PATH] [--force]` | Index documents (idempotent) |
| `ben frameworks` | List loaded documents |
| `ben serve [--host] [--port]` | Webhook server |
| `ben telegram-poll` | Telegram long polling for development |
| `ben set-webhook [--delete]` | Register or remove the Telegram webhook |
| `ben audit-export [--since] [--until] [--out]` | Audit log to CSV |
| `ben cleanup` | Apply retention now |
| `ben eval [--retrieval-only] [--judge] [--only ID]` | Run the eval set |

---

*Ben is not legal advice. Legal/Compliance must confirm regulatory interpretations.*

# Model Card: Ben (AI Governance Assistant)

| | |
| --- | --- |
| **System** | Ben, a retrieval-augmented chat assistant for AI governance |
| **Owner** | AI Governance team *(fill in the accountable owner)* |
| **Version** | 0.1.0 |
| **AI type** | GenAI (LLM with tool use and retrieval) |
| **Underlying model** | Anthropic Claude, configured through `BEN_MODEL` (default `claude-sonnet-5`) |
| **Embedding model** | `BAAI/bge-small-en-v1.5` (fastembed, runs locally) |
| **Channels** | Telegram, WhatsApp (Cloud API), local CLI |
| **Status** | Internal pilot *(update as appropriate)* |

## Purpose and intended use

Ben helps AI governance practitioners, risk owners and project teams at the airline to:

- find which internal controls apply to an AI use case;
- understand what regulations and standards say (for example the EU AI Act, ISO/IEC 42001 and
  UAE PDPL), with citations to the source documents;
- draft first versions of governance artefacts, such as a one-page risk assessment.

**Intended users:** named, allowlisted employees in AI governance, risk, compliance and delivery
teams.

**Out of scope:**
- Legal advice or final regulatory determinations. Legal/Compliance must confirm them.
- Automated decisions about individuals.
- Processing of passenger or staff personal data. Users are told not to share it.
- Customer-facing use.

## How it works

1. A user's message arrives through a verified webhook. The sender is checked against an
   allowlist and a rate limit.
2. Claude receives Ben's system prompt, the recent conversation (default: last 10 turns) and
   three tools:
   - `search_library`: hybrid (BM25 + vector) search over the ingested documents;
   - `get_control`: exact lookup in the AI Risk & Control Library;
   - `list_frameworks`: lists the loaded documents.
3. Claude searches, reads the returned passages (marked as data), and writes a concise answer.
   It is instructed to cite every substantive claim.
4. The exchange is written to the audit log after PII redaction.

## Data sources

- **Document library:** only documents that administrators ingest with `ben ingest`
  (regulations, standards, internal policies, the AI Risk & Control Library). Record here which
  documents and versions are loaded, and who approved them. `ben frameworks` lists them.
- **User messages:** used for the conversation and kept in memory for up to `RETENTION_DAYS`,
  redacted by default.
- **No training:** Ben does not train or fine-tune any model. Under Anthropic's commercial terms,
  API inputs and outputs are not used for model training by default. Confirm this against your
  contract and data-processing agreement.
- **Data flows:** message text and retrieved passages are sent to the Anthropic API. Telegram
  and WhatsApp (Meta) carry messages in transit. Embeddings and the index stay on Ben's host.
  Confirm these cross-border transfers with the DPO (see the UAE PDPL guidance).

## Limitations

- **Coverage:** Ben is only as good as the library. Where the library is silent, Ben should say
  so and label general knowledge, but it may still be incomplete.
- **Hallucination:** LLMs can state incorrect facts or wrong clause numbers with confidence.
  Citations reduce this risk but don't remove it. Always check important claims against the
  cited source.
- **Retrieval errors:** the search can miss relevant passages, especially in scanned PDFs (no
  OCR), in tables, or where the wording differs a lot from the question.
- **Currency:** answers reflect the documents as ingested. Regulatory dates and guidance change,
  and superseded documents must be removed or updated.
- **Language:** tuned for English. The embedding model is English-focused.
- **Redaction:** PII redaction is pattern-based. It will miss some personal data (for example
  names) and may occasionally redact harmless codes.
- **Formatting:** answers are optimised for phone screens, so long analyses are summarised.

## Risks and controls

| Risk | Control in Ben | Related library control |
| --- | --- | --- |
| Inaccurate or invented guidance (hallucination) | Answers must be grounded in the library with citations; control IDs are checked against the library and unknown IDs are flagged; eval suite measures citation accuracy and groundedness | AIRC-007 |
| Over-reliance / treated as legal advice | System prompt adds a "Legal/Compliance should confirm" note on regulatory interpretations; README and `/start` say the same | AIRC-006 |
| Prompt injection via documents or pasted text | Retrieved content is delimited as data with closing tags escaped; system prompt forbids following embedded instructions; tools are read-only; tool rounds capped; injection case in evals | AIRC-008 |
| Unauthorised access | Allowlists (deny by default), webhook secret / HMAC verification, per-user rate limit | AIRC-001 |
| Personal data exposure | PII redaction before logging and memory (optional before the model); retention limits; users told not to share personal data | AIRC-004 |
| Lack of traceability | Audit log of every exchange (sources, model ID, tokens, latency) with CSV export | AIRC-010 |
| Quality drift after model or document changes | `ben eval` run before release and after changes to the model, prompt or library; model ID pinned via config | AIRC-007, AIRC-010 |
| Users unaware they're talking to AI | Ben introduces itself as an AI assistant (`/start`) | AIRC-009 |

## Evaluation

- **Method:** `ben eval` on `evals/questions.yaml`. It measures citation accuracy,
  groundedness (no invented IDs, every citation maps to a retrieved source), key-point coverage,
  correct declines for out-of-scope and injection prompts, and retrieval recall. `--judge` adds
  a Claude-graded groundedness check.
- **Results:** *record the date, model ID, library version and summary metrics for each release
  here.*
- **Release criteria (suggested):** grounded rate ≥ 95%, no answers with unverified control IDs,
  decline accuracy 100% on injection cases, citation accuracy ≥ 80%.

## Monitoring and operations

- Review the audit log regularly: `denied` and `rate_limited` events, error rates, answers with
  `unverified_ids`, and latency and token trends.
- Re-run the evals when `BEN_MODEL`, `prompts/system.md` or the library changes.
- Incidents (for example harmful or wrong guidance that someone acted on) go through the AI
  incident process.

## Change log

| Date | Version | Change |
| --- | --- | --- |
| *(fill in)* | 0.1.0 | Initial release: Telegram + WhatsApp, hybrid retrieval, audit, redaction, evals |

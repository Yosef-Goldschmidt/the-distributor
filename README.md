# The Distributor

An AI agent that builds **film festival submission strategies** for independent film
distribution companies.

Give it a film — synopsis, genre, themes, country, director profile, premiere status —
and it returns a ranked festival roadmap: what to submit first, what to prioritise next,
where to leverage the company's existing relationships, and what to avoid because of
premiere or deadline risk. Every recommendation carries a 0–100 match score, the evidence
behind it, and its risks.

Built by Yosef Goldschmidt and Reuven Spitz.

---

## Architecture — Plan-and-Execute

```
User prompt → Planner → Executor (tools) → Replanner → Festival Strategy Roadmap
                            ↑_________________|  (revise if needed)
```

| Module | Type | Role |
| --- | --- | --- |
| `Planner` | LLM | Turns the request into an ordered task plan |
| `Executor` | orchestrator | Runs the planned tasks through the tool modules |
| `FilmAnalyzer` | LLM | Extracts the festival-relevant film profile |
| `FestivalSearch` | tool | Pinecone semantic retrieval + Supabase festival facts |
| `CompanyMemory` | tool | Supabase lookup of the company's prior festival history |
| `MatchScorer` | LLM + code | LLM rates six dimensions 0–5; code applies the weights |
| `RiskChecker` | LLM | Premiere, eligibility and deadline risk per festival |
| `RoadmapBuilder` | LLM | Writes the bucketed strategic roadmap |
| `Replanner` | LLM | Accepts the strategy or triggers one revision round |

These names are identical in the architecture diagram (`/api/model_architecture`), in the
`steps` trace returned by `/api/execute`, and in `/api/agent_info`.

**Cost:** six LLM calls per run (seven to eight if the Replanner asks for a revision).
MatchScorer and RiskChecker run concurrently, which keeps a run well inside the
300-second serverless limit.
Embeddings use a separate embedding model, and retrieval, company memory and the score
arithmetic cost nothing.

### Scoring

The LLM never invents the number. It rates five dimensions 0–5 with a short evidence
phrase, deadline urgency is derived from the calendar in code, and
`app/agent/scoring.py` owns the weights and the arithmetic:

| Dimension | Weight |
| --- | ---: |
| Thematic fit | 25 |
| Genre fit | 15 |
| Past lineup / winner similarity | 20 |
| Company relationship history | 15 |
| Strategic value | 15 |
| Deadline urgency (computed in code) | 10 |

Premiere risk is applied as a penalty (`high` −15, `medium` −7), not as a score component,
and a world-premiere requirement counts as an opportunity rather than a risk while the film
still has its premiere available. Festivals whose programming identity was inferred rather
than established (`identity_confidence: "low"`) have their lineup-similarity rating capped,
so inferred detail is never scored as if it were a verified track record.
Buckets are then assigned deterministically: **Submit First**, **Prioritize Next**,
**Leverage**, **Hold / Avoid**.

---

## API

| Endpoint | Description |
| --- | --- |
| `GET /` | GUI (no authentication) |
| `GET /api/team_info` | Student details |
| `GET /api/agent_info` | Description, purpose, prompt template, prompt examples with full responses and steps |
| `GET /api/model_architecture` | Architecture diagram (`image/png`) |
| `POST /api/execute` | `{"prompt": "..."}` → exactly `{"status", "error", "response", "steps"}` |
| `GET /api/health` | Which integrations are configured (diagnostics) |

`steps` is the ordered list of module invocations, each `{module, prompt, response}`. It
covers every LLM call plus the retrieval and scoring steps, so the whole execution is
auditable. A run that fails part-way still returns the trace collected up to that point.

The response carries exactly those four fields. Passing `"include_meta": true` adds a
fifth `meta` object with token usage, elapsed time, the premiere target and the full
ranking; the bundled GUI uses it, and nothing else depends on it.

---

## Local development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt uvicorn pillow
cp .env.example .env          # then fill in the keys
uvicorn api.index:app --reload --port 8000
```

Open <http://localhost:8000>.

Without credentials the agent still starts: retrieval falls back to a local TF-IDF search
over `data/festivals.json` and company memory falls back to `data/company.json`. Only the
LLM calls genuinely require a key.

`/api/health` reports exactly which integrations are live.

### Offline test (spends nothing)

```bash
python tests/test_pipeline.py
```

Stubs the LLM and asserts the trace order, the weighted scores, bucket assignment,
markdown rendering and every endpoint contract.

---

## Data

The corpus comes from the distribution company's own working spreadsheet, not from
scraped or invented sources.

| File | Rows | Origin |
| --- | ---: | --- |
| `data/festivals.json` | 355 festivals | `Adam Chart` sheet — tier (A/B+/B/C), category, focus, city/country, festival dates, deadline month + recorded deadline dates, premiere requirements, fees, status, website |
| `data/company.json` | 171 festivals with history | `BAKARA` sheet — 11,119 screening records (2008–2027, 892 titles, awards) aggregated per festival |
| `data/enrichment/` | 355 entries | Curated **descriptive** text only: programming identity, award patterns, theme tags, practical notes, each labelled `high` / `medium` / `low` confidence |

The enrichment never overwrites a fact from the workbook — it only fills `focus`,
`award_patterns`, `notes`, `themes` and `notable_past_selections`, which is what the
vector index needs in order to match a film to a festival's taste. Contact names,
e-mail addresses, invoice numbers and fees from the workbook are **not** imported.

### Rebuilding the data

```bash
python scripts/import_excel.py "/path/to/workbook.xlsx"   # sheets -> festivals.json + company.json
python scripts/merge_enrichment.py                        # fold in data/enrichment/part_*.json
python scripts/seed_pinecone.py                           # embed + upsert the corpus
python scripts/seed_supabase.py                           # push festivals + company memory
python scripts/make_architecture.py                       # regenerate assets/architecture.png (needs pillow)
python scripts/generate_example.py                        # record live /api/agent_info examples
python scripts/dry_run.py "an environmental documentary"  # retrieval-only check, no LLM cost
```

Run `scripts/schema.sql` in the Supabase SQL editor before `seed_supabase.py`.

### Anonymisation

`import_excel.py` anonymises the distribution company by default: the company name becomes
fictional and every catalogue title is replaced by a stable pseudonym, while the festival
facts and the relationship structure (which festival, how many screenings, which years,
which awards) are preserved exactly. The title mapping is written to
`data/anonymisation_map.json`, which is git-ignored. Pass `--real-company` for an internal
build that keeps the real names.

---

## Deployment (Vercel)

1. Push this repository to GitHub.
2. Import it in Vercel — `vercel.json` routes everything to the FastAPI app in `api/index.py`.
3. Add the environment variables from `.env.example` in **Settings → Environment Variables**
   (use the Supabase anon key in production; the service key is only needed locally for seeding).
4. Deploy, then verify:

```bash
curl https://<your-app>.vercel.app/api/health
curl -X POST https://<your-app>.vercel.app/api/execute \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Title: Salt and Ash\nFormat: feature documentary\n..."}'
```

A run takes well under the 300-second Vercel limit.

---

## Before submitting

- [ ] Fill in `data/team_info.json` (`group_batch_order_number`, real emails).
- [ ] Set the LLMod.ai project key (not the RAG assignment key) in Vercel.
- [ ] Seed Pinecone and Supabase, then confirm `/api/health` reports both as configured.
- [ ] Run `scripts/generate_example.py` so `/api/agent_info` returns real examples.

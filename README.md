# The Distributor

An AI agent that builds **film festival submission strategies** for independent film
distribution companies.

Give it a film — synopsis, genre, themes, country, director profile, premiere status —
and it returns a ranked festival roadmap: what to submit first, what to prioritise next,
where to leverage the company's existing relationships, and what to avoid because of
premiere or deadline risk. Every recommendation carries a 0–100 match score, the evidence
behind it, and its risks.

Built by Yosef Goldschmidt and Reuven Shpitz.

The public **Quick Strategy** experience remains the course-compatible one-shot agent.
An additive **Campaign Workspace** at `/campaign` turns the same evidence pipeline into a
durable, event-driven campaign: it tracks premiere state and human decisions, constructs a
directed compatibility graph, selects a globally coherent launch route, versions every
strategy, reuses valid evidence on deterministic replans, and compares no-write scenarios.

---

## Architecture — Plan-and-Execute

```
User prompt → Planner → Executor (evidence chain) → Replanner → Validated roadmap
                                      RoadmapBuilder ↑  (one targeted rewrite if invalid)
```

| Module | Type | Role |
| --- | --- | --- |
| `Planner` | deterministic control | Declares the complete domain evidence chain; required tasks cannot be omitted |
| `Executor` | orchestrator | Runs the chain in dependency order under a serverless time budget |
| `FilmAnalyzer` | LLM | Extracts supported film facts, premiere history, unknowns and the retrieval query |
| `CompanyMemory` | retrieval tool | Loads full company history before candidate generation |
| `FestivalSearch` | hybrid retrieval tool | Traced embedding + Pinecone semantics + local lexical relevance + company-memory and prestige reserves + Supabase facts + explicit fallback provenance + entity deduplication |
| `RiskChecker` | deterministic domain rules | Validates exact/projected deadlines, format eligibility and premiere scope with confidence labels |
| `MatchScorer` | LLM + code | LLM rates four creative dimensions; code validates/repairs structure and adds company/deadline evidence, guardrails, weights and penalties |
| `RoadmapBuilder` | LLM | Selects supplied evidence to foreground and unresolved facts; code owns narrative, actions and sequencing |
| `Replanner` | deterministic validator | Checks completeness, uniqueness, evidence references, assigned buckets and the single premiere target |

These names are identical in the architecture diagram (`/api/model_architecture`), in the
`steps` trace returned by `/api/execute`, and in `/api/agent_info`.

**Cost:** three chat calls per normal run (`FilmAnalyzer`, `MatchScorer`,
`RoadmapBuilder`) plus one embedding request. A failed roadmap validation can add one
targeted `RoadmapBuilder` rewrite; malformed `MatchScorer` structure can add one targeted
repair call. Analysis and retrieval are never repeated. Chat and embedding model attempts,
including parameter fallbacks, errors and retries, are traced separately. The run has a
260-second application deadline, leaving headroom below Vercel's 300-second function limit.
Query embeddings have a separate 20-second timeout and fall back to local TF-IDF rather than
holding the entire run open.

### Campaign Workspace — deterministic incremental planning

Campaign planning is additive to the module chain above; it does not replace or rename any
Quick Strategy module. `LegacyEvidenceAdapter` is the only raw-dictionary boundary. It
converts the existing analyzer, CompanyMemory, retrieval, risk and guarded scoring outputs
into frozen typed evidence. `CampaignPlanner` accepts only `PlanningInput` and performs no
provider or database call.

The campaign layer adds:

- a capability-scoped aggregate, deterministic reducer, premiere ledger and append-only
  events;
- a complete directed tri-state festival compatibility graph and a deterministic planner
  with one primary launch, at most two alternatives, explicit preservation diagnostics,
  verification gates, clarification and exact hard/soft budget semantics;
- immutable strategy versions, structured `StrategyDiff` values and explicit
  `ReuseManifest` hashes;
- A/B/C invalidation: A refreshes identity-dependent legacy evidence, B reruns
  ledger/risk/graph/planning, and C reruns planning/clarification only;
- an in-memory scenario engine that applies one to three of the same typed commands, uses
  the same B/C path, returns a diff and discards the clone without a database write.

For a valid B/C cache, operational replans are deterministic and make zero chat and zero
embedding attempts. A cache mismatch never silently falls back to a provider: the strategy
remains stale and exposes the miss.

### Scoring

The LLM never invents the number. It rates four creative dimensions 0–5 with a short
evidence phrase. Company relationship strength is computed from recorded screenings,
recency and awards; deadline urgency is computed from structured dates. Then
`app/agent/scoring.py` owns the weights and the arithmetic:

| Dimension | Weight |
| --- | ---: |
| Thematic fit | 25 |
| Genre fit | 15 |
| Past lineup / winner similarity | 20 |
| Company relationship history | 15 |
| Strategic value | 15 |
| Deadline urgency (computed in code) | 10 |

Premiere risk is applied as a penalty (`high` −15, `medium` −7), not as a score component.
Exact `final_deadline` values override recurring month shorthand; stale cycles are projected
explicitly and marked with confidence. Raw rules such as `World - Spain` are interpreted as
territorial and uncertain, not silently collapsed into strict world-premiere requirements.
Runtime ranges found only in descriptive enrichment are surfaced as verification warnings;
they do not override structured accepted-format data or become hard eligibility rules.
The roadmap chooses one launch target as the intended first public festival screening.
Mutually exclusive premiere paths are labelled as alternatives; all compatible screenings
must follow the target even when their submission deadlines come earlier. Festivals whose
programming identity was inferred rather than established
(`identity_confidence: "low"`) have their lineup-similarity rating capped, and strategic
value is capped by tier. These guardrails reduce unsupported score inflation, but they do
not claim to prove the semantic truth of every LLM-written phrase. `RoadmapBuilder` may
choose which supplied score evidence to emphasize and which facts to ask the distributor
to confirm, but deterministic code writes the summary, each action and the chronological
calendar and rejects structurally ungrounded evidence references.
Buckets are then assigned deterministically: **Submit First**, **Prioritize Next**,
**Leverage**, **Hold / Avoid**. These buckets prioritize submission work; the separate
premiere-sequence label governs whether an accepted screening can follow the selected target
or remains a mutually exclusive alternative.

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
| `GET /campaign` | Compact Campaign Workspace page; Quick Strategy at `/` remains public |
| `POST /api/workspace/bootstrap` | Resolve or create a private capability-scoped demo workspace |
| `GET/POST /api/workspace/campaigns` | List campaigns or create one from structured/free-text film evidence |
| `GET /api/workspace/campaigns/{id}` | Authoritative aggregate, active plan, latest diff, evidence and trace summary |
| `POST /api/workspace/campaigns/{id}/commands` | Apply one typed human command and synchronously replan |
| `POST /api/workspace/campaigns/{id}/replan` | Retry the current stale version without a state event |
| `POST /api/workspace/campaigns/{id}/simulate` | Compare one to three commands on a discarded in-memory clone |
| `GET /api/workspace/campaigns/{id}/strategies/{no}` | Inspect an immutable historical strategy |

`steps` is the ordered list of module invocations, each `{module, prompt, response}`. It
covers every actual chat or embedding model attempt (including retries, errors and
rejected-parameter fallbacks)
plus planning, retrieval, deterministic validation and scoring, so the whole execution is
auditable. A run that fails part-way still returns the trace collected up to that point.

The response always carries exactly those four fields, including malformed-input and
partially traced runtime errors. The bundled GUI derives its candidate table and attempt
counts from `steps`; it does not require a private extension to the contract. The public
endpoint rejects prompts over 12,000 characters before any paid service is called.

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
chat calls genuinely require a key. Every fallback is labelled in the trace; vector and
festival-fact fallbacks are also repeated in the final grounding rather than being presented
as Pinecone or Supabase evidence.

`/api/health` reports exactly which integrations are live.

Campaign Workspace persistence deliberately fails closed unless both `SUPABASE_URL` and
the server-only `SUPABASE_SERVICE_ROLE_KEY` are configured. Before enabling it, review and
apply `scripts/migrations/20260825_campaign_workspace_phase_1a.sql` to the intended
Supabase project. The migration is additive, creates no browser policies, and has not been
applied by the implementation workflow. Set `CAMPAIGN_ALLOWED_ORIGINS` to an exact
comma-separated list such as `http://localhost:8000,https://your-app.vercel.app`; wildcard,
missing and `null` origins are rejected for JSON mutations. The raw 256-bit workspace
capability is sent only in a `Secure`, `HttpOnly`, `SameSite=Lax` cookie; only its SHA-256
digest is stored.

### Offline test (spends nothing)

```bash
.venv/bin/python -m pytest -q
.venv/bin/python evals/run_campaign.py
```

The suite spends no LLM budget. It covers the end-to-end pipeline and API contract,
corpus-wide date/premiere invariants, company-memory integrity, hybrid retrieval and entity
deduplication, explainable scoring guardrails, roadmap uniqueness, per-attempt LLM tracing,
planner archetypes A–E, budget/premiere semantics, two-capability isolation, zero-provider
B/C replanning, correction behavior and scenario no-write behavior. The campaign evaluation
runner is offline and reports Sitges separately as a known corpus-coverage issue.

---

## Data

The structured festival and company-history facts come from the distribution company's own
working spreadsheet. Descriptive enrichment is kept separate, confidence-labelled and is
not treated as an official rules source.

| File | Rows | Origin |
| --- | ---: | --- |
| `data/festivals.json` | 355 festivals | `Adam Chart` sheet — tier (A/B+/B/C), category, focus, city/country, festival dates, deadline month + recorded deadline dates, premiere requirements, fees, status, website |
| `data/company.json` | 171 festivals with history | `BAKARA` sheet — 11,119 screening records (2008–2027, 853 anonymised catalogue titles, awards) aggregated per festival |
| `data/enrichment/` | 355 entries | Curated **descriptive** text only: programming identity, award patterns, theme tags, practical notes, each labelled `high` / `medium` / `low` confidence |

The enrichment never overwrites a fact from the workbook — it only fills `focus`,
`award_patterns`, `notes`, `themes` and `notable_past_selections`, which is what the
vector index needs in order to match a film to a festival's taste. Contact names,
e-mail addresses, invoice numbers and fees from the workbook are **not** imported.

The runtime treats the workbook as time-versioned evidence, not as a live rules service.
Recorded dates and raw premiere shorthand retain provenance; annual projections and ambiguous
rules are surfaced as estimates that must be checked on the festival's official site. One
known duplicate festival entity in the source is collapsed during retrieval without mutating
or re-seeding either backing store.

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
3. Add the environment variables from `.env.example` in **Settings → Environment Variables**.
   Quick Strategy can use the existing read configuration. Campaign Workspace additionally
   requires the server-only `SUPABASE_SERVICE_ROLE_KEY` and exact
   `CAMPAIGN_ALLOWED_ORIGINS`; neither value is exposed to the browser.
4. With explicit production-mutation authorization, apply the additive campaign migration
   once to the target Supabase project. Do not reseed festivals or rebuild Pinecone.
5. Deploy, then verify Quick Strategy and the capability-scoped campaign smoke path:

```bash
curl https://<your-app>.vercel.app/api/health
curl -X POST https://<your-app>.vercel.app/api/execute \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Title: Salt and Ash\nFormat: feature documentary\n..."}'
```

The FastAPI function is configured for 300 seconds and the agent enforces a 260-second
LLM deadline. Embedding, Pinecone and Supabase calls have separate 20/20/15-second bounds.
No runtime step writes to the bundled filesystem; Supabase run logging is best-effort,
bounded and cannot change a successful agent result into an application error.

---

## Before submitting

- [x] Fill in `data/team_info.json` (`group_batch_order_number`, real emails).
- [ ] Set the LLMod.ai project key (not the RAG assignment key) in Vercel.
- [x] Confirm the current Pinecone and Supabase integrations without re-seeding them.
- [x] Run `scripts/generate_example.py` so `/api/agent_info` returns real examples.
- [ ] Deploy the current revision and rerun the production smoke checks.

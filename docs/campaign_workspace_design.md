# Campaign Workspace: Design and Implementation Plan

Status: planning only  
Baseline inspected: `campaign-workspace` at `a9ebe1e` (`Add behavioral guardrails and adversarial evaluation`)  
Date: 2026-08-25

This document proposes an additive evolution of The Distributor from a one-shot festival roadmap into a persistent, human-in-the-loop campaign system. It does not authorize implementation, database migration, seeding, deployment, or changes to the existing course API behavior.

The recommended design preserves the current architecture as a compatibility path and adds a separate campaign workspace around it. The core new intelligence is not another prompt wrapper. It is explicit campaign state, deterministic state transitions, decision-aware clarification, a compatibility graph that represents premiere option loss, incremental replanning, and isolated counterfactual simulation.

## Executive recommendation

Build a single-company, no-login-gate campaign workspace with:

- durable film profiles, human constraints, campaign events, submissions, screenings, verification tasks, and immutable strategy versions;
- a deterministic premiere ledger derived from confirmed public screenings;
- a bounded heuristic campaign planner over a tri-state festival compatibility graph;
- primary, rejection, acceptance, and verification-dependent branches represented as validated data;
- incremental replanning that reuses retrieval and creative-fit evidence whenever the changed fact does not invalidate it;
- copy-on-write scenarios that never mutate the campaign until an explicit apply action;
- a professional workspace UI while retaining a visible textarea, **Run Agent** action, final response, and full trace at `/`;
- the current three-chat-call plus one-embedding initial run budget, with most typed event replans and simulations using zero model calls.

Do not build full accounts, multi-tenant administration, autonomous submissions, acceptance-probability forecasts, live rule scraping, a general workflow engine, or a multi-agent swarm in this iteration. A large corpus expansion should also wait; build the coverage audit and provenance gate first, then add only a small reviewed gap set in a separately approved data change.

## 1. Current-state architecture

### 1.1 Request boundary and course contracts

`api/index.py` owns the FastAPI application and the public course surface:

- `GET /` reads and returns `public/index.html` with no authentication guard.
- `GET /api/team_info` loads `data/team_info.json`.
- `GET /api/agent_info` describes the actual module catalog, scoring weights, prompt template, recorded examples, and grounding policy.
- `GET /api/model_architecture` returns `assets/architecture.png`.
- `POST /api/execute` validates a string `prompt`, rejects empty or over-12,000-character inputs before paid calls, invokes `app.agent.graph.run`, and returns exactly `status`, `error`, `response`, and `steps` on success and failure.
- `GET /api/health` reports integration configuration and corpus size.

The exact four-field `/api/execute` response is protected by `tests/test_pipeline.py`, `tests/test_pipeline_discovery.py`, and the behavioral API-contract scenario. Internal `graph.run` metadata is deliberately not exposed as a fifth field. The browser reconstructs candidate and attempt summaries from `steps` in `public/index.html::traceMeta`.

The six-page course specification in `project.pdf` reinforces four constraints that shape this design: the four named endpoints and exact `/api/execute` top-level fields must remain; the root GUI must remain immediately usable without login; model/submodule names must agree across diagram, descriptions, and trace; and Vercel requests must complete below 300 seconds. It also rewards avoiding unnecessary LLM calls and prompt context.

### 1.2 Runtime data flow

The current path is:

```text
POST /api/execute
  -> api.index.execute
  -> app.agent.graph.run
  -> Trace + deadline-bounded LLMClient
  -> Planner (fixed evidence-chain plan)
  -> Executor trace container
  -> FilmAnalyzer
  -> CompanyMemory
  -> FestivalSearch
  -> RiskChecker
  -> MatchScorer + deterministic assemble
  -> deterministic premiere target/sequence annotation
  -> RoadmapBuilder
  -> Replanner validator, with at most one targeted roadmap rewrite
  -> deterministic roadmap normalization and Markdown rendering
  -> best-effort agent_runs log
  -> exact four-field API response
```

The actual execution order comes from `app/agent/prompts.py::TASK_CATALOG` and is enforced by `app/agent/modules.py::planner`; the current Planner does not use an LLM. `app/agent/graph.py::_run` initializes one `LLMClient`, records an `Executor` step before its children, and executes the fixed chain sequentially. A failed stage becomes `AgentRunError`, preserving the partial trace.

### 1.3 Existing modules and responsibility boundaries

| Module | Actual implementation | Current responsibility |
|---|---|---|
| Planner | `app/agent/modules.py::planner` | Deterministically declares the complete evidence chain. It prevents an LLM planner from omitting required domain work. |
| Executor | `app/agent/graph.py::_run` | Executes the catalog, carries intermediate values, observes the 260-second run budget, and records outcomes. |
| FilmAnalyzer | `modules.film_analyzer` plus `prompts.FILM_ANALYZER` | One chat call extracts a structured profile and retrieval query. `domain.analyse_critical_input` then overrides unsupported certainty, preserves unknown premiere state, surfaces contradictions, and blocks youth-audience over-inference. |
| CompanyMemory | `modules.company_memory` | Loads the full company profile/history from Supabase or `data/company.json`, then computes deterministic relationship summaries. It runs before retrieval. |
| FestivalSearch | `modules.festival_search` | Uses one traced query embedding plus Pinecone when configured, always computes local lexical relevance, reserves bounded relevant relationship and prestige slots, fetches structured facts from Supabase with local row fill, and deduplicates entities by website then normalized name. |
| RiskChecker | `modules.risk_checker`; `app/agent/domain.py` | Deterministically assesses exact/projected deadlines, format eligibility, descriptive runtime warnings, and world/international/continental/territorial premiere constraints. |
| MatchScorer | `modules.match_scorer`, `modules.assemble`, `app/agent/scoring.py` | One chat call rates four qualitative dimensions. Code validates every row, permits one targeted structural repair, injects company/deadline dimensions, applies confidence/audience/tier guardrails, calculates the weighted score, and assigns buckets. |
| RoadmapBuilder | `modules.roadmap_builder`, `modules.normalise_roadmap` | One chat call selects one or two supplied evidence dimensions and open questions. Deterministic code owns actions, summary, sequence language, calendar, and final completeness. |
| Replanner | `modules.replanner` | Currently a deterministic roadmap validator, not a lifecycle replanner. It checks known/unique/complete IDs, assigned buckets, evidence references, and a single premiere target. |

Normal execution therefore uses three chat calls (`FilmAnalyzer`, `MatchScorer`, `RoadmapBuilder`) and one embedding. A malformed scorer may add one chat repair; an invalid roadmap may add one RoadmapBuilder rewrite. `app/llm.py` traces every real provider attempt, including rejected-parameter fallbacks, token-budget retries, malformed successes, and transport errors. `app/embeddings.py` similarly traces embedding attempts without exposing vectors.

### 1.4 Current domain semantics that must remain invariants

`app/agent/domain.py` is the strongest reusable foundation in the repository:

- `analyse_critical_input` conservatively detects runtime, format, completion/release, premiere, and youth-audience evidence and contradictions from the original prompt.
- `assess_deadline` gives exact `final_deadline` precedence, aligns recurring cycles, makes stale-cycle projection explicit, and never invents a day from month-only data.
- `premiere_constraint` refuses to collapse `World - Spain`-style shorthand into a strict world-premiere rule.
- `assess_premiere` separates structured format ineligibility from descriptive runtime warnings and treats missing premiere facts as uncertainty.
- `post_target_compatibility` and `pre_target_compatibility` already provide the first form of a directed compatibility relation between screenings.

`app/agent/scoring.py` keeps the LLM away from arithmetic. It owns six weights totaling 100: thematic fit 25, genre fit 15, lineup similarity 20, company relationship 15, strategic value 15, and deadline urgency 10. It caps low-confidence lineup similarity, caps strategic value by tier, bounds company history to 15 points, and caps youth-specialist fit without explicit youth-audience evidence. Premiere risk is a separate penalty (`high=-15`, `medium=-7`).

The campaign architecture should call these policies through typed adapters, not duplicate their logic in prompts or UI JavaScript.

### 1.5 Retrieval, persistence, and data

`app/stores/corpus.py` loads the bundled 355-festival corpus and implements local TF-IDF. `app/stores/pinecone_store.py` wraps semantic retrieval and labels all fallbacks. `app/stores/supabase_store.py` reads structured festival facts and company history, fills partial Supabase results from local data, and best-effort logs runs.

The current `scripts/schema.sql` contains four tables:

- `festivals` for structured and curated descriptive festival data;
- `companies` for one company profile and denormalized circuit/film summaries;
- `company_festival_history` for imported aggregate relationships;
- `agent_runs` for minimal prompt/run metadata.

The current runtime is not campaign-persistent. Every `/api/execute` call recreates film state from text, and the only runtime write is a non-critical `agent_runs` insert.

The corpus snapshot contains 355 festivals across 63 countries: 39 tier A, 34 B+, 163 B, and 119 C. Identity confidence is high for 165, medium for 137, and low for 53. There are 334 rows with an exact recorded final date, seven month-only rows, and 315 with raw premiere shorthand. These numbers describe coverage volume, not current factual validity.

`scripts/import_excel.py` preserves distributor workbook facts while removing contacts and anonymizing company/title identities. `scripts/merge_enrichment.py` merges only descriptive fields and labels identity confidence. `scripts/seed_supabase.py` and `scripts/seed_pinecone.py` are explicit offline mutation steps; no future runtime flow should invoke either.

### 1.6 Frontend and observability

`public/index.html` is a dependency-free single page with a film textarea, Run Agent button, response renderer, candidate table, and collapsed trace details. It calls only `/api/execute`. It already meets the course's minimum UI contract but has no durable project navigation, structured editing, event entry, scenario comparison, or distinction between facts, human choices, agent recommendations, and uncertain evidence.

The trace is unusually strong and should remain a first-class feature. Provider attempts, deterministic validation, retrieval sources, scoring, and replanner defects are all inspectable. Future workspace strategy runs should persist the same ordered step schema and expose it behind a collapsed Evidence & Trace panel.

### 1.7 Baseline evidence and architectural seam

At inspection time all 66 unit tests pass. The latest behavioral report records 17 offline/mocked scenarios, with one true failure: Sitges is absent from the committed corpus. It correctly classifies this as a corpus/data failure, not retrieval, ranking, or roadmap failure. The recent git history shows deliberate convergence on exact API compatibility, smaller prompts/candidate pools, deterministic guardrails, full failed-attempt traces, and adversarial evaluation.

The safe seam is therefore additive:

1. Leave `graph.run(prompt)` and `/api/execute` behavior intact as the legacy/quick-strategy path.
2. Add typed campaign services in a new `app/campaign/` package that reuse `app.agent.domain`, retrieval, scoring, and LLM-client infrastructure.
3. Add new `/api/workspace/...` endpoints rather than overloading the course endpoint.
4. Update the architecture diagram and module descriptions only when the new traced modules actually exist.

## 2. Product model

### 2.1 Central product concept

A **Film Project** is the durable identity and fact record of one film. A **Campaign** is a time-bounded distribution strategy for that film under one company context. This distinction permits a future second campaign for a new territory or release phase without rewriting the film's factual history.

The campaign is the main workspace object. It holds current human policy, operational events, submissions, screenings, opportunities, verification work, and the active strategy version. A generated ranking is evidence used by the campaign planner; it is not itself the campaign.

### 2.2 Intended workflow

1. **Create or resume.** The user opens the root GUI without a login wall, starts a film project, or resumes a browser-owned campaign.
2. **Establish the film.** Free text can initialize a profile through the existing FilmAnalyzer, but the result is shown as structured facts with source/status. Unknown and contradictory critical fields remain visible.
3. **Set human policy.** The user adds hard constraints and softer preferences, such as preserve-world-premiere, budget, geography, relationship leverage, locked targets, and exclusions.
4. **Clarify only material uncertainty.** The system asks the smallest set of questions whose answer can alter eligibility, the launch target, option loss, budget feasibility, or branch validity. When safe, it publishes a clearly preliminary plan before all useful fields are answered.
5. **Build a campaign strategy.** Retrieval and semantic fit produce opportunities. A deterministic campaign planner chooses a primary launch path, alternatives, post-premiere route, and verification gates using global compatibility rather than independent rank alone.
6. **Operate the campaign.** The user records submissions, invitations, rejections, accepted offers, screenings, rule verifications, deadline changes, and preference changes.
7. **Replan incrementally.** The system records what changed, invalidates only dependent evidence, creates a new immutable strategy version, and explains changed and unchanged decisions.
8. **Simulate before committing.** A what-if scenario forks the current campaign version, applies hypothetical events to the fork, replans, and displays the delta. Apply is a separate human action guarded by version checks.
9. **Accumulate company memory.** Confirmed campaign screenings, selections, and awards become new company-history events without modifying the imported historical aggregate.

### 2.3 Product principles

- **Facts, policy, recommendations, and uncertainty are different data classes.** Their visual presentation and mutation rules must remain distinct.
- **A submission is not a screening.** Submitting or being rejected does not consume premiere status. Even an invitation does not consume it until a public screening occurs, though accepting an invitation may create a strategic commitment requiring confirmation.
- **Irreversible actions are explicit.** The system may recommend, simulate, or create a task; it may not silently record an external event or override a locked human decision.
- **State is versioned.** A strategy always names the exact campaign version and evidence snapshot from which it was built.
- **Uncertainty is actionable.** Missing rules create verification gates and option-value ranges, not fabricated dates or false eligibility.

## 3. Domain model

The implementation should define Pydantic models/enums in `app/campaign/models.py` and keep database serialization in `app/campaign/repository.py`. The domain model should not be expressed as unvalidated dictionaries throughout the new package.

### 3.1 Core entities

| Entity | Responsibility | Important relationships and invariants |
|---|---|---|
| Workspace | Browser-owned container that makes persistence possible without a visible login system. | Owns one company context and many film projects. Root quick strategy does not require a workspace. |
| Company | Existing distributor profile and imported relationship context. | Reuses `companies`; imported `company_festival_history` stays immutable. New confirmed outcomes are appended as company festival events. |
| FilmProject | Stable film identity independent of one generated plan. | Belongs to a workspace/company; points to one current immutable `FilmProfileVersion`. |
| FilmProfileVersion | Immutable structured snapshot of film facts, unknowns, contradictions, sources, and confirmation states. | Every strategy records its profile version. Corrections create a new version; they never edit old strategy inputs. |
| Campaign | Operational aggregate for one film-festival campaign. | Has an optimistic `version`, lifecycle state, readiness state, active strategy version, and event sequence. |
| CampaignConstraint | Structured human policy or locked decision. | Has kind, operator/value, hard/preference strength, origin=`human`, active/withdrawn status, and lock state. It must not be confused with a festival rule. |
| FestivalOpportunity | Campaign-specific view of one festival candidate and its current decision status. | References the global `festival_id`, score/evidence snapshot, eligibility, uncertainty, relationship evidence, and candidate/locked/excluded status. It does not own global festival facts. |
| Submission | Operational lifecycle of a submission/offer. | Unique per campaign/festival/cycle unless explicitly versioned; state transitions are validated. Submission state alone never consumes premiere status. |
| Screening | A scheduled or occurred showing with public/private/unknown classification and location. | Only a confirmed occurred public screening changes the premiere ledger. Historical screenings may exist without a submission. |
| CampaignEvent | Append-only record of a validated real-world or human-policy change. | Carries event type, typed payload, actor, occurred/recorded timestamps, idempotency key, and campaign version before/after. |
| PremiereLedger | Derived tri-state availability for world, international, continental, and territorial premiere scopes. | Recomputed deterministically from confirmed profile facts and non-superseded screening events. Never directly edited. |
| VerificationTask | Decision-relevant fact that must be checked against an identified source. | Has priority, affected decision IDs, source URL/type, status, result, as-of date, and evidence reference. Resolving it emits an event. |
| StrategyVersion | Immutable contingent plan created from one campaign version. | Stores input hashes, planner policy version, plan DAG, opportunity snapshots, explanation/diff, trace reference, and status (`draft`, `active`, `superseded`, `failed`). |
| StrategyRun | One execution attempt, including ordered trace steps, provider usage, cache/reuse decisions, and failure status. | A failed run never replaces the active strategy. |
| Scenario | Copy-on-write fork from an exact campaign/strategy version with hypothetical commands. | Cannot change real state until explicitly applied against the unchanged base version. |
| CompanyFestivalEvent | New, normalized company-memory evidence produced by real campaigns. | Selection, screening, or award facts supplement but never rewrite the imported aggregate history. |
| FestivalEvidenceVersion | Provenanced festival fact or descriptive identity revision. | Required for controlled corpus/rule updates; includes source, observed date, confidence, reviewer status, and supersession. |

### 3.2 Fact and provenance model

Each field in `FilmProfileVersion.facts` should use a small common envelope:

```json
{
  "value": 89,
  "status": "confirmed",
  "confidence": "high",
  "source_refs": ["campaign_event:evt_123"],
  "last_updated_at": "2026-08-25T12:00:00Z"
}
```

Allowed fact states should be `confirmed`, `asserted`, `inferred`, `unknown`, and `contradicted`. `inferred` values may help retrieval but may not satisfy hard eligibility. `contradicted` stores competing assertions and forces the normalized value used by deterministic rules to `null`/`unknown` until resolved. This generalizes the safeguards already enforced by `film_analyzer` without discarding the original evidence.

Every surfaced reason should refer to one of four origins:

- `festival_requirement`: a source-backed external rule;
- `human_constraint`: an explicit user policy or locked decision;
- `agent_recommendation`: a versioned plan choice;
- `uncertain_evidence`: missing, ambiguous, projected, stale, or inferred information.

The UI should use these origin values, not keyword heuristics, to label content.

### 3.3 Structured campaign constraints

Initial constraint kinds should be deliberately finite:

- `preserve_premiere` with scope and strength (`hard` for “at all costs”);
- `include_region` / `prioritize_region`;
- `exclude_country` / `exclude_region`;
- `submission_budget_max` with currency;
- `festival_type_preference`;
- `relationship_leverage_preference`;
- `lock_festival`;
- `exclude_festival`.

Each constraint has `strength=hard|preference`, `locked`, `origin=human`, and active dates. Free text may be interpreted into a proposed constraint, but only the structured value enters planning. Unknown fee is never treated as zero when evaluating a budget constraint.

## 4. State model

### 4.1 Orthogonal campaign states

A single giant campaign enum would mix lifecycle, planning readiness, and operational outcomes. Use three orthogonal state machines instead.

**Campaign lifecycle**

```text
draft -> active <-> paused -> completed -> archived
  \-------------------------------------> archived
```

- `draft`: profile/constraints are being established.
- `active`: the campaign is accepting events and has or is building a strategy.
- `paused`: state remains durable but no action is currently planned.
- `completed`: distribution work is intentionally closed; corrections remain possible.
- `archived`: hidden from the default workspace view, never physically deleted by a routine command.

**Planning readiness**

- `blocked`: a critical contradiction or absent hard input makes any safe actionable plan impossible.
- `preliminary`: a plan can be shown, but one or more actions are verification-dependent.
- `ready`: no unresolved blocking/high-impact fact affects the recommended branch.
- `stale`: a committed event changed plan inputs and a replacement strategy has not completed.

**Opportunity decision state**

- `candidate` -> `locked` or `excluded`; either human state can be withdrawn.
- Submission/outcome status is not compressed into this decision state.

### 4.2 Submission and screening transitions

```text
not_started -> preparing -> submitted
submitted -> rejected
submitted -> invited
submitted -> withdrawn
invited -> offer_accepted | offer_declined
offer_accepted -> scheduled
scheduled -> screened | cancelled
```

“Selected” should normalize to `invited`, not `screened`. “Accepted” is linguistically ambiguous: it can mean the festival accepted the film or the distributor accepted the invitation. A natural-language interpreter must return an ambiguity for confirmation unless context establishes which transition is intended.

Manual historical screenings are valid even when no submission row exists. This is required to initialize an already-running campaign accurately.

### 4.3 Premiere ledger

Do not model premiere state as only one enum. Store a derived ledger with tri-state values:

```json
{
  "world": "false",
  "international": "unknown",
  "continental": {"Europe": "unknown"},
  "territorial": {"Israel": "false", "Spain": "unknown"},
  "basis_event_ids": ["evt_docaviv_screened"],
  "history_completeness": "partial"
}
```

Rules:

1. No screening evidence means `unknown`, not available.
2. A confirmed statement that the complete public screening history is empty may establish world availability.
3. `submitted`, `rejected`, `invited`, and `offer_accepted` do not by themselves consume a premiere.
4. A scheduled screening does not consume a premiere until it occurs, but accepting/scheduling an option-destroying screening requires a human warning.
5. Only `screened` with `public_status=public` consumes applicable premiere scope.
6. A private industry, market, press, or test screening does not consume public premiere status unless a verified festival rule says otherwise.
7. A public domestic screening sets world availability false and consumes the relevant domestic territory. It does not prove that international premiere remains available unless the history is confirmed complete.
8. A public screening outside the home country sets international availability false and consumes matching continental/territorial scopes.
9. `public_status=unknown` creates a blocking/high-impact clarification; it does not consume status automatically.
10. Corrections append a compensating event that supersedes the mistaken screening assertion; old strategy versions retain their original input snapshot.

The reducer should call/refactor the existing normalization and territory helpers in `app/agent/domain.py`. It must never weaken the current “unknown stays unknown” behavior.

### 4.4 Event transition table

| Event | Preconditions | Deterministic effects | Replan impact |
|---|---|---|---|
| `film_profile_corrected` | Valid typed patch; expected campaign version | New profile version; preserve old assertions/provenance | Retrieval only if creative/search fields changed; risk/planning if critical fields changed |
| `constraint_added/changed/withdrawn` | Human actor; valid constraint schema | Update constraint projection and campaign version | Filter/plan; retrieval only for search-expanding preferences |
| `decision_locked/unlocked` | Known opportunity; human confirmation | Change lock state | Planner only |
| `submission_submitted` | Opportunity not excluded; no duplicate live submission | Transition submission; record fee if known | Usually presentation only |
| `festival_rejected` | Submitted or explicitly imported historical outcome | Transition to rejected; preserve screening/premiere state | Remove from active branches; promote compatible alternative |
| `festival_invited` | Submitted or imported outcome | Transition to invited | Add human acceptance gate; do not consume premiere |
| `offer_accepted/declined` | Invited; explicit human action | Transition outcome; accepted offer becomes a commitment | Planner and conflict warning; no premiere consumption yet |
| `screening_scheduled` | Accepted offer or imported historical item | Create/update screening schedule | Plan timing if dates are known; no premiere consumption |
| `screening_occurred` | Known location/date/public classification, or clarification task | Mark screened; update premiere ledger; append company event | Risk and compatibility graph for all affected opportunities |
| `deadline_verified` | Source URL, observed date, verifier | Add campaign evidence override/version; resolve task | Risk/planner for affected festival |
| `premiere_rule_verified` | Source and normalized rule | Add evidence version; resolve task | Compatibility edges and planner |
| `event_corrected` | References prior event; valid replacement | Append compensating correction and rebuild affected projection | Same dependency set as corrected event |

All command application must be atomic, idempotent, and guarded by `expected_campaign_version`.

## 5. Persistence design

### 5.1 Recommended Supabase additions

Use additive migrations under `scripts/migrations/`; do not rewrite or drop the current four tables in the first release.

| Table | Key columns | Notes and constraints |
|---|---|---|
| `workspaces` | `id uuid`, `owner_id uuid`, `company_id text`, `created_at`, `archived_at` | `owner_id` is a silent Supabase anonymous-auth user. Demo/reference company data remains read-only. |
| `film_projects` | `id uuid`, `workspace_id`, `company_id`, `title`, `current_profile_version_id`, timestamps | Stable identity only; detailed facts live in versions. |
| `film_profile_versions` | `id uuid`, `film_project_id`, `version int`, `facts jsonb`, `unknowns jsonb`, `contradictions jsonb`, `source_event_id`, `created_at` | Unique `(film_project_id, version)`; immutable after insert. |
| `campaigns` | `id uuid`, `film_project_id`, `workspace_id`, `status`, `readiness`, `version bigint`, `active_strategy_version_id`, `strategy_stale bool`, timestamps | `version` is the optimistic concurrency token. |
| `campaign_constraints` | `id uuid`, `campaign_id`, `kind`, `value jsonb`, `strength`, `locked`, `status`, `source_event_id`, timestamps | Check constraints on kind/strength/status; human origin is explicit. |
| `campaign_events` | `id uuid`, `campaign_id`, `sequence bigint`, `event_type`, `payload jsonb`, `actor_type`, `occurred_at`, `recorded_at`, `idempotency_key`, `campaign_version_before/after`, `supersedes_event_id` | Unique sequence and idempotency per campaign; append-only except administrative redaction metadata. |
| `campaign_opportunities` | `id uuid`, `campaign_id`, `festival_id`, `decision_status`, `locked`, `latest_evidence_snapshot jsonb`, `latest_strategy_version_id`, timestamps | Unique `(campaign_id, festival_id)`. Global festival facts stay in `festivals`/evidence versions. |
| `submissions` | `id uuid`, `campaign_id`, `festival_id`, `cycle_key`, `state`, `submitted_at`, `fee_amount`, `fee_currency`, `source_event_id`, timestamps | Unique live submission per campaign/festival/cycle; checked transition service. |
| `screenings` | `id uuid`, `campaign_id`, `festival_id`, `status`, `public_status`, `country`, `city`, `scheduled_at`, `occurred_at`, `source_event_id`, `superseded_by` | Premiere reducer reads only non-superseded occurred public rows. |
| `verification_tasks` | `id uuid`, `campaign_id`, `festival_id`, `fact_key`, `priority`, `status`, `source_url`, `affected_decision_ids jsonb`, `result jsonb`, `observed_at`, timestamps | No task is “resolved” without a result and provenance. |
| `strategy_versions` | `id uuid`, `campaign_id`, `version int`, `input_campaign_version`, `profile_version_id`, `planner_policy_version`, `input_hash`, `plan jsonb`, `comparison jsonb`, `status`, `run_id`, timestamps | Immutable. Unique `(campaign_id, version)` and optionally `(campaign_id, input_hash, planner_policy_version)`. |
| `strategy_runs` | `id uuid`, `campaign_id`, `trigger_event_id`, `status`, `trace_steps jsonb`, `usage jsonb`, `reuse_manifest jsonb`, `error`, timings | Failed attempts are durable but cannot become active strategy. |
| `scenarios` | `id uuid`, `campaign_id`, `base_campaign_version`, `base_strategy_version_id`, `name`, `commands jsonb`, `result jsonb`, `comparison jsonb`, `status`, timestamps | `draft`, `computed`, `applied`, `discarded`, or `stale`. |
| `company_festival_events` | `id uuid`, `company_id`, `film_project_id`, `campaign_id`, `festival_id`, `event_type`, `occurred_at`, `payload jsonb`, `source_event_id` | Supplements frozen imported aggregates with new real outcomes. |
| `festival_evidence_versions` | `id uuid`, `festival_id`, `field_key`, `value jsonb`, `source_url`, `observed_at`, `confidence`, `review_status`, `supersedes_id`, `created_at` | Enables campaign-specific verification and later controlled corpus updates without silent overwrite. |

Indexes should cover campaign/version, campaign/event sequence, campaign/festival, workspace/campaign recency, open verification tasks, and company/festival event lookup. Foreign keys should use restrictive deletion for audit records; normal user “delete” is archive.

### 5.2 Transaction boundary

Implement one database transaction/RPC-equivalent command path:

1. load campaign with `version = expected_version`;
2. reject invalid transition or stale version;
3. append the event using the idempotency key;
4. update affected projections (`submissions`, `screenings`, constraints, profile version, premiere snapshot/readiness);
5. increment campaign version and set `strategy_stale=true` when required;
6. commit;
7. run replanning after the state commit.

If replanning fails, the real-world event must remain saved and the old strategy remains visible with a prominent stale banner. Durable event mutation is not best-effort; only the replacement strategy is retryable.

This is a pragmatic event journal plus transactional projections, not a general event-sourcing platform. Tests should be able to replay events to verify projections, but production reads should use efficient current projections.

### 5.3 Durable versus ephemeral state

**Durable**

- confirmed/asserted film facts and their versions;
- active human constraints and lock history;
- validated campaign events;
- submissions, outcomes, screenings, and company-memory events;
- verification tasks/results and evidence provenance;
- strategy versions, diffs, traces, and provider usage;
- explicitly saved scenarios and their base versions.

**Ephemeral or cached**

- an unconfirmed natural-language event interpretation;
- unsaved scenario computation;
- intermediate beam-search states;
- prompt payloads before trace redaction/validation;
- retrieval/score caches that can be regenerated from immutable input hashes;
- UI selection, expanded panels, and unsaved form drafts.

### 5.4 No-login-gate access model

Preferred first implementation: enable Supabase anonymous auth only when the user chooses **Save as campaign**. The root quick-strategy UI and `/api/execute` remain usable even if anonymous-session initialization fails. The anonymous JWT owns workspace rows through RLS (`owner_id = auth.uid()`), is sent to same-origin workspace endpoints, and creates no signup/login/password-reset UI. A later account upgrade could link the anonymous identity, but that is out of scope.

Do not make one publicly writable shared demo campaign. If anonymous auth is unavailable in the course deployment, the fallback is a high-entropy, server-issued capability token stored in an HttpOnly cookie, with only its hash persisted. That fallback needs separate security review before implementation.

The browser must never receive a Supabase service-role key. Existing public festival/demo-company reads can remain under the current anon policy initially; new campaign tables must have owner-scoped RLS. The imported company is anonymized, but public company-history access should still be reviewed rather than copied automatically to new user data.

### 5.5 Migration and backward compatibility

- Ship a new idempotent migration, e.g. `scripts/migrations/001_campaign_workspace.sql`; keep `scripts/schema.sql` valid for the baseline until the new feature is accepted.
- Deploy database additions before enabling workspace routes. Existing code will ignore new tables.
- Gate new routes/UI with `CAMPAIGN_WORKSPACE_ENABLED`; the root quick path remains the fallback.
- Do not infer historical campaigns from `agent_runs` or prompt examples. Those records lack authoritative lifecycle events.
- Do not normalize or overwrite imported `company_festival_history`; merge its deterministic aggregate with new `company_festival_events` at read time.
- Do not mutate global festival facts from a campaign verification. Record a versioned overlay first; promote it to the curated corpus only through the controlled data workflow.
- Preserve old strategy snapshots across model, prompt, scoring, domain-rule, and schema changes by recording component versions.
- Before any production migration, validate in a separate Supabase project, export/backup affected tables, exercise rollback by feature-flag disable, and run both legacy and new suites.

## 6. Future agent architecture

### 6.1 Architectural shape

The campaign system should be an event-driven plan-and-execute system, not a set of conversational sub-agents:

```text
Command or prompt
  -> EventInterpreter (only for natural language)
  -> StateTransitionEngine
  -> Replanner impact analysis
  -> selective evidence chain
       FilmAnalyzer? -> CompanyMemory? -> FestivalSearch? -> RiskChecker
       -> MatchScorer? -> CampaignPlanner -> ClarificationEngine
       -> RoadmapBuilder? -> StrategyValidator
  -> immutable StrategyVersion + diff
```

Question marks mean dependency-driven reuse, not optional correctness. The Planner should declare which cached evidence is valid and why. The trace should record reuse decisions as deterministic steps so “zero LLM calls” is auditable.

### 6.2 Module contract matrix

| Module | Purpose | Inputs | Outputs | Type | Dependencies | Failure behavior | Status |
|---|---|---|---|---|---|---|---|
| Planner | Declare the required initial or incremental workflow from a typed change set and cache manifest. | Campaign snapshot, trigger, component/input hashes | Ordered tasks plus reuse/invalidation reasons | Deterministic | Replanner impact rules, component versions | Fail closed before provider calls if dependency graph is inconsistent | Adapt existing deterministic Planner |
| Executor | Run declared tasks within time/call budgets and preserve ordered trace. | Plan, repositories, clients, deadline | StrategyRun result, intermediate artifacts, trace | Deterministic orchestration | Existing `graph`/`Trace`/`LLMClient` patterns | Persist failed run; never replace active strategy | Adapt existing Executor into `app/campaign/orchestrator.py` |
| EventInterpreter | Convert a natural-language update into one or more proposed typed commands and ambiguities. | Text, current campaign summary, known festival IDs | Proposed commands, confidence, unresolved ambiguity | LLM, conditional | Small prompt; no retrieval | Never mutates state; invalid/ambiguous output becomes a confirmation form | New; optional/high value |
| StateTransitionEngine | Validate and atomically apply a confirmed command, then derive projections and premiere state. | Typed command, current aggregate, expected version | Event, updated aggregate, change set | Deterministic | Models, state machine, repository transaction | Reject with conflict/validation error; no partial mutation | New, core |
| FilmAnalyzer | Extract initial or changed film facts and a retrieval identity. | Original text or explicit profile patch | Proposed profile facts/version | LLM plus existing deterministic critical-input validation | Existing prompt/domain rules | Existing bounded structured validation; user confirms critical inferred changes | Reuse/adapt existing |
| CompanyMemory | Load imported aggregate and new campaign outcomes; compute bounded relationship evidence. | Company ID, optionally candidate IDs | Relationship summaries with provenance | Retrieval plus deterministic scoring | Supabase/local baseline | Explicit stale/local source; no relationship assumed on failure | Adapt existing |
| FestivalSearch | Retrieve or expand relevant festival candidates. | Versioned search identity, format, company memory | Deduplicated candidates and retrieval provenance | One embedding plus deterministic hybrid retrieval | Pinecone, local TF-IDF, Supabase | Existing labeled lexical/local fallback; cache by input hash | Reuse/adapt existing |
| RiskChecker | Evaluate date, format, runtime, premiere, and verified campaign-specific rule overlays. | Profile version, premiere ledger, opportunity facts, as-of date | Tri-state eligibility/risk/uncertainty | Deterministic | `app.agent.domain`, evidence overlays | Unknown/verify rather than invented answer | Extend existing |
| MatchScorer | Rate only creative/qualitative fit for new or invalidated candidate evidence; merge computed dimensions. | Profile identity, candidate descriptions, company evidence | Validated qualitative ratings and deterministic score snapshots | One LLM call for a batch plus deterministic code | Existing prompts/scoring | One structural repair; unchanged valid rows remain cached | Reuse/adapt existing |
| CampaignPlanner | Plan globally over candidate compatibility, deadlines, budget, constraints, uncertainty, and premiere option preservation. | Scored opportunity snapshots, constraints, premiere ledger | Contingent plan DAG, option-value diagnostics, alternatives | Deterministic bounded heuristic | RiskChecker, compatibility graph | Publish no plan when hard state is contradictory; otherwise preliminary plan with gates | New, core |
| ClarificationEngine | Select only questions that can materially change decisions. | Unknown/contradicted facts, current plan, counterfactual deltas | Prioritized questions and affected decisions | Deterministic; templated language | CampaignPlanner/RiskChecker sensitivity hooks | Fall back to known critical catalog; never dump every missing field | New, core |
| RoadmapBuilder | Optionally select evidence emphasis and concise professional narrative for a new full plan. | Validated structured plan and evidence only | Evidence references/open-question wording | LLM plus deterministic renderer | Existing RoadmapBuilder pattern | Prior active narrative remains; structured UI still works | Adapt existing; skip on routine deterministic replans |
| Replanner | Determine what an event invalidates, preserve reusable artifacts, and explain changed/unchanged decisions. | Before/after snapshot, event/change set, current strategy | Task invalidation map and structured strategy diff | Deterministic | Dependency registry, CampaignPlanner | Mark strategy stale and retain prior version if downstream run fails | Replace validator-only meaning with lifecycle role |
| ScenarioSimulator | Apply hypothetical commands to an in-memory/copy-on-write snapshot and invoke the same reducer/planner. | Base version, hypothetical commands | Scenario plan and comparison | Deterministic orchestration; conditional existing modules | StateTransitionEngine in simulation mode, Replanner | No durable writes unless save requested; error cannot affect campaign | New, core |
| StrategyValidator | Enforce graph, state, provenance, constraint, and trace invariants. | Proposed strategy version | Accept/reject defects | Deterministic | Current Replanner invariant logic plus new rules | One targeted presentation repair only; planner defects are code errors, not LLM retries | New name for extracted validator responsibility |

Repository adapters, API routes, and UI components are not “agents” and should not appear as sub-agents in the architecture diagram.

### 6.3 Call policy

- Initial campaign from free text: keep three normal chat calls plus one embedding at most.
- Initial campaign from a fully structured confirmed form: FilmAnalyzer may be skipped; target two chat calls plus one embedding.
- Typed rejection, invitation, accepted offer, screening, lock, exclusion, or verified-rule event: normally zero chat and zero embedding calls.
- Natural-language event: one EventInterpreter chat call to propose commands; confirmation/apply adds no model call.
- Creative profile or search-expanding preference change: rerun FilmAnalyzer only if input is unstructured, one embedding, and one batched MatchScorer call for new/invalidated candidates.
- Scenario over existing candidates: zero model calls unless it changes creative identity enough to require candidate expansion.
- RoadmapBuilder is optional for routine event diffs because structured plan and deterministic explanations are sufficient. Do not spend a call just to rephrase unchanged strategy.

## 7. Clarification architecture

### 7.1 Decision-aware process

The ClarificationEngine should operate after a preliminary deterministic risk/plan pass:

1. Collect unknown and contradicted facts from `FilmProfileVersion`, festival evidence, fees, timing, and constraints.
2. Map every fact key to its dependent computations: retrieval, eligibility, compatibility edge, budget, launch choice, or presentation only.
3. For facts with a finite safe answer domain, evaluate answer **classes** against cached evidence. This is sensitivity analysis, not guessing the answer. Example premiere classes are world available, only international available, already publicly screened, and still unknown.
4. Compare outputs: hard eligibility, chosen launch target, preserved-option interval, branch set, budget feasibility, and required human confirmation.
5. Assign priority from observed decision impact.
6. Generate one templated question per decision intent and deduplicate by fact/dependency, not word overlap alone.
7. Stop when the highest-priority unanswered questions are sufficient to make the next irreversible decision safe.

No additional LLM call is needed. FilmAnalyzer may identify a missing field, but deterministic impact analysis decides whether and when to ask.

### 7.2 Priority definitions

| Priority | Definition | Examples | Behavior |
|---|---|---|---|
| Blocking | No safe actionable plan or state transition can be produced; critical assertions conflict. | Contradictory public screening history; conflicting formats when format eligibility is required; an “accepted” event whose meaning is ambiguous before mutation | Prevent affected mutation/plan, but preserve existing campaign and unrelated actions |
| High impact | Different answer classes change hard eligibility, launch target, locked-option compatibility, or whether an irreversible action is safe. | Completeness of screening history; preserve-world-premiere policy; public/private status of a screening; official premiere rule for the intended target | Show at top; preliminary plan may continue with verification-dependent branches |
| Useful | Changes ranking, budget, timing, or a secondary route but not the safe primary decision. | Verified fee under a non-tight budget; preferred region; exact deadline for a next-wave candidate | Ask in context near affected opportunity, not as onboarding wall |
| Low priority | Does not materially change retrieval, eligibility, planning, or user constraints in the current campaign. | Composer name for a campaign with no music-specific strategy | Keep in profile completeness view; do not interrupt planning |

### 7.3 Preliminary-strategy rule

A preliminary plan is allowed when every recommended external action is either:

- safe under all plausible answer classes; or
- explicitly gated by a verification task before submission/acceptance/screening.

It is not allowed to label a premiere target as confirmed when premiere availability is unknown. The existing baseline behavior—no target when premiere status is unknown—remains the default. The workspace may still show creative-fit opportunities and “if available” routes without manufacturing a target.

## 8. Campaign planning algorithm

### 8.1 Choice: bounded compatibility-graph heuristic

Use a small directed compatibility graph plus bounded beam search/Pareto filtering. Do not introduce a general constraint solver or stochastic optimizer in the first iteration.

Why this choice:

- The candidate pool is intentionally small (normally 12), so exhaustive-ish bounded search is cheap.
- Premiere compatibility is naturally a directed edge: a public screening at A may preserve, consume, or leave unknown eligibility at B.
- Most inputs are tri-state or missing; a mathematically “optimal” solution would only hide arbitrary assumptions.
- The graph and search trace are easy to explain and ablate academically.
- Existing `post_target_compatibility` and `pre_target_compatibility` are direct foundations.
- OR-Tools/MILP would add dependency and modeling cost without reliable notification dates, screening dates, acceptance probabilities, or complete rules.

### 8.2 Separate submission order from screening order

Submitting to a festival generally does not consume a premiere. Therefore the planner must represent:

- a **submission action set**, which may include multiple mutually exclusive premiere targets before their deadlines; and
- an **acceptance/screening sequence**, where choosing or completing one screening can invalidate later options.

This preserves the current useful distinction between submission buckets and premiere sequence, while making it explicit in the data model.

### 8.3 Compatibility graph

For each viable opportunity pair `(A, B)`, compute the effect of screening publicly at A before B:

- `compatible`: B's relevant premiere requirement is known to remain satisfiable;
- `incompatible`: A deterministically consumes B's required premiere;
- `verify`: the source or film history is insufficient;
- `not_applicable`: B has no relevant premiere restriction.

Each edge stores its reason and evidence refs. An unknown edge is never coerced to a numeric probability.

### 8.4 Option-value proxy

The planner must not invent acceptance likelihoods. “Option value” is therefore a transparent strategic-utility proxy, not expected monetary value.

For opportunity `i`, define `strategic_weight_i` as its deterministic weighted score excluding deadline urgency and current premiere penalty. This reuses creative fit, lineup similarity, bounded company relationship, and strategic value while preventing a near deadline from making an option intrinsically more valuable.

For a candidate plan state:

```text
known_preserved = sum(strategic_weight_i for every unvisited option known compatible)
possible_additional = sum(strategic_weight_i for every unvisited option with verify status)
known_preserved_ratio = known_preserved / sum(strategic_weight_i for initially viable options)
```

Display option value as an interval/diagnostic:

```text
known preserved: 61%
additional uncertain: up to 18%, pending two rule verifications
```

`possible_additional` is never added to the default objective; it remains a visible uncertainty band. This avoids treating “unknown” as a 50% chance.

### 8.5 Search and selection

1. Apply hard user constraints and deterministic ineligibility.
2. Include every human-locked target, even if it creates a visible conflict; never silently drop it.
3. Generate viable launch roots from the top opportunities plus locked candidates and a `no_confirmed_launch` root when premiere state is unknown.
4. Explore public-screening sequences to depth three with beam width five. This is enough to demonstrate launch, post-premiere, and one later route without pretending to schedule an entire year from incomplete dates.
5. At each state, enforce known compatibility and budget constraints, retain verify edges as explicit gates, and compute the known/possible option diagnostics.
6. Remove Pareto-dominated paths across immediate launch utility, known preserved ratio, known cost, and verification burden.
7. Apply human policy:
   - a hard preserve-premiere constraint eliminates paths that consume the protected scope;
   - locked festival choices remain and force conflicts to human review;
   - preferences adjust a versioned planning-policy weight, never hard eligibility.
8. Under the default policy, choose the highest:

```text
plan_value = immediate_launch_utility
           + lambda_option * 100 * known_preserved_ratio
```

Use a small, versioned initial `lambda_option` (recommend 0.25), then break ties by fewer blocking verification gates, lower known cost, earlier actionable deadline, and stable festival ID. This lambda is a product policy parameter, not a fact; the evaluation suite must ablate it and distributor review must approve it before production.

9. Build rejection branches by moving to the next non-dominated launch route. Rejection preserves premiere state. Build selection/offer branches with an explicit human acceptance gate. Build verification branches only for named unresolved edges/tasks.

### 8.6 Pseudocode

```python
opportunities = apply_hard_constraints(risk_checked_candidates, constraints)
graph = build_tri_state_compatibility_graph(opportunities, premiere_ledger)
roots = launch_roots(opportunities, locked_targets=True, allow_unknown_root=True)

frontier = []
for root in roots:
    paths = bounded_beam_search(
        root=root,
        graph=graph,
        max_screenings=3,
        beam_width=5,
        state=(premiere_ledger, known_cost=0, visited=set()),
    )
    frontier.extend(paths)

frontier = remove_hard_violations_except_visible_locked_conflicts(frontier)
frontier = pareto_filter(frontier)
primary = select_by_human_policy_then_versioned_default(frontier)
alternatives = select_grounded_rejection_and_verification_branches(frontier, primary)
return validate_strategy_dag(primary, alternatives)
```

### 8.7 Tradeoffs and deliberate limits

- Depth three cannot optimize an entire multi-year circuit, but deeper plans would be dominated by stale/unknown schedule facts and create false precision.
- A weighted policy still contains judgment. Versioning, visible diagnostics, ablation, and human constraints make that judgment inspectable.
- The algorithm plans festival decisions, not acceptance. It should say “if invited” or “if rejected,” never predict either outcome.
- If screening or notification dates are unavailable, the graph records relative order and `timing_status=unknown`; it does not invent calendar nodes.
- Budget planning treats unknown fees as unresolved, not free. A hard budget can make a path preliminary until fee verification.

## 9. Contingent strategy representation

Represent the strategy as a small acyclic graph in `StrategyVersion.plan`, not Markdown and not a deeply nested bespoke tree. A DAG allows shared post-premiere routes without duplicating nodes.

```json
{
  "schema_version": 1,
  "root_node_id": "submit_wave_1",
  "nodes": [
    {
      "id": "submit_wave_1",
      "type": "action_set",
      "action": "submit",
      "festival_ids": ["hot-docs", "docaviv"],
      "timing": {"deadline": null, "confidence": "low"},
      "evidence_refs": ["opp:hot-docs:v3", "opp:docaviv:v3"]
    },
    {
      "id": "hot_docs_outcome",
      "type": "outcome_gate",
      "festival_id": "hot-docs",
      "allowed_conditions": ["invited", "rejected"]
    },
    {
      "id": "accept_hot_docs",
      "type": "human_decision",
      "decision": "accept_screening_offer",
      "irreversible_effect_preview": ["world_premiere_consumed"]
    },
    {
      "id": "verify_docaviv_rule",
      "type": "verification_gate",
      "verification_task_id": "verify_17"
    },
    {
      "id": "post_premiere_route",
      "type": "route",
      "festival_ids": ["festival-x", "festival-y"],
      "timing": {"relative": "after_launch", "dates_known": false}
    }
  ],
  "edges": [
    {"from": "submit_wave_1", "to": "hot_docs_outcome", "condition": "submitted"},
    {"from": "hot_docs_outcome", "to": "accept_hot_docs", "condition": "invited"},
    {"from": "hot_docs_outcome", "to": "verify_docaviv_rule", "condition": "rejected_and_premiere_preserved"},
    {"from": "accept_hot_docs", "to": "post_premiere_route", "condition": "human_confirmed_and_screened"}
  ],
  "option_value": {
    "known_preserved_ratio": 0.61,
    "possible_additional_ratio": 0.18,
    "policy_version": "campaign-planner-v1"
  }
}
```

Validation invariants:

- graph is acyclic and every edge/node reference exists;
- every festival ID comes from the opportunity snapshot;
- each branch condition corresponds to a possible typed event or verification result;
- no branch asserts a date, outcome, rule, or eligibility absent from evidence;
- every irreversible choice has a human-decision node;
- locked/excluded constraints are honored or surfaced as an explicit conflict;
- a rejection branch preserves premiere unless a separate screening event exists;
- an unknown compatibility edge must pass through a verification gate;
- only one active primary launch path exists, while alternatives are labeled mutually exclusive where appropriate.

## 10. Replanning

### 10.1 Replanner responsibility

The future Replanner should answer four concrete questions:

1. What canonical state changed?
2. Which derived artifacts are invalid because of that change?
3. Which prior evidence remains reusable?
4. What recommendations changed, what remained stable, and why?

The current structural validator should move to `StrategyValidator`; keeping validation and lifecycle change analysis in one function would preserve the current naming problem rather than solve it.

### 10.2 Dependency registry

Maintain an explicit registry in `app/campaign/replanning.py`:

| Changed input | Recompute | Reuse |
|---|---|---|
| Rejection/withdrawal | Candidate availability, contingent branches, CampaignPlanner, diff | Film profile, retrieval, all creative scores, unaffected risks |
| Invitation/offer acceptance | Human gate, commitment conflicts, CampaignPlanner, diff | Retrieval and creative scores; premiere ledger until screening |
| Confirmed public screening | Premiere ledger, RiskChecker for premiere-sensitive opportunities, compatibility graph, CampaignPlanner, clarification, diff | Semantic retrieval, creative-fit ratings, company relationship facts |
| Private screening | Event history and possibly presentation | Premiere ledger and strategy unless a verified rule makes it relevant |
| Festival deadline/rule verified | Affected evidence snapshot, affected risk/edges, CampaignPlanner | Other candidate facts, retrieval, creative scores |
| Festival locked/excluded | Opportunity filter, CampaignPlanner, diff | Retrieval, risk, scores |
| Budget changed | Cost feasibility, CampaignPlanner, clarification | Retrieval, fit, premiere risk |
| Geography/type preference changed | Filter/reweight; retrieve only if the preference expands the search space beyond cached candidates | Existing candidate evidence/scores |
| Runtime/format/country changed | Critical validation, risk; retrieval/score if format or identity changes | Unaffected evidence only |
| Synopsis/themes/audience changed | Retrieval identity, retrieval, creative scores for invalidated/new candidates, planner | Operational event history and confirmed external facts |
| Company relationship event | CompanyMemory aggregate and relationship dimension for affected festival, planner | Creative ratings and external rules |

Every stored artifact should carry an input hash and component policy version. Reuse is allowed only when both match the dependency registry. A deterministic `ReuseDecision` step should list reused and invalidated artifacts in the trace.

### 10.3 Incremental execution sequence

For “Hot Docs rejected us” after confirmation:

1. `StateTransitionEngine` appends `festival_rejected` and increments campaign version.
2. Premiere ledger is unchanged because no screening occurred.
3. Replanner marks Hot Docs unavailable and invalidates only branches containing it.
4. CampaignPlanner evaluates existing scored alternatives and promotes the best non-dominated compatible route.
5. ClarificationEngine keeps unrelated tasks unchanged.
6. A new strategy version stores a structured comparison:
   - changed: Hot Docs removed; alternative X promoted;
   - reason: recorded rejection, premiere still preserved;
   - unchanged: creative-fit scores, company relationships, verified rules, post-premiere candidates not dependent on Hot Docs.
7. No embedding or LLM call is required.

For “The film screened publicly at Docaviv”:

1. Confirm location/date/public status if absent.
2. Append screening event and derive the premiere ledger.
3. Re-evaluate premiere eligibility/compatibility for all affected opportunities.
4. Remove deterministically incompatible world/domestic-premiere routes, create verification tasks for ambiguous territorial rules, and retain unrelated creative scores.
5. Produce a new post-premiere strategy and a diff that names each newly unavailable opportunity and the exact screening/rule basis.

### 10.4 Failure and staleness semantics

- State commit succeeds independently of replanning.
- `campaign.strategy_stale=true` is set in the event transaction.
- Only a fully validated strategy version built from the current campaign version may clear staleness and become active.
- If Supabase is unavailable, a real campaign mutation fails clearly; it must not fall back to an in-memory/local write and pretend persistence.
- If LLM/retrieval fails during a replan that actually needs it, retain the previous strategy with a stale banner and expose retry.
- If another tab changes the campaign first, return HTTP 409 with the latest version and a human-readable conflict; never last-write-wins.

## 11. Scenario simulation

### 11.1 Isolation model

A scenario is based on immutable identifiers:

- `campaign_id`;
- `base_campaign_version`;
- `base_strategy_version_id`;
- zero or more typed hypothetical commands.

The simulator loads that snapshot, applies commands through the same `StateTransitionEngine` in `simulation=True` mode, and invokes the same Replanner and CampaignPlanner. Repository mutation methods must be unavailable to the simulation context. This is stronger than copying a mutable Python dictionary and hoping no store method is called.

### 11.2 Comparison output

The comparison should be deterministic and structured:

- changed primary target;
- branch nodes/edges added and removed;
- opportunities newly available, newly unavailable, or moved to verification;
- option-value interval before/after;
- hard-constraint conflicts before/after;
- budget/timing change where supported;
- unchanged decisions and evidence reuse;
- hypothetical facts clearly labeled.

The UI may render prose from this structure without an LLM.

### 11.3 Apply and discard

- **Preview:** no durable writes except optional scenario record.
- **Save:** store the hypothetical commands, result, and comparison; still no campaign mutation.
- **Discard:** mark saved scenario discarded or remove only an unsaved client draft.
- **Apply:** show the exact real commands/events that will be created and require confirmation. Apply only if the campaign version still equals the base version. Then append the commands transactionally, replan from real state, and mark the scenario applied.
- **Stale scenario:** if real state advanced, do not silently rebase. Offer “recompute on latest” as a new scenario version.

Scenarios that say “what if Hot Docs rejects us?” may simulate rejection only when Hot Docs is a current submitted opportunity or the scenario explicitly includes the prerequisite submission. Invalid hypothetical state transitions should be rejected rather than normalized magically.

## 12. Human-in-the-loop design

### 12.1 Agent authority

The agent may autonomously:

- extract proposed structured facts from supplied text;
- retrieve and score candidate festivals;
- compute deterministic risk, compatibility, option diagnostics, and plan alternatives;
- create verification tasks;
- mark a strategy stale after a confirmed event;
- create a draft strategy version and explain its differences;
- simulate hypothetical events in isolation;
- reuse or invalidate cached evidence according to declared dependencies.

The agent may not autonomously:

- assert that an unknown screening/premiere fact is known;
- record that a submission, invitation, rejection, acceptance, withdrawal, award, or screening happened externally;
- accept a festival screening offer;
- unlock or override a human-locked target/constraint;
- exceed a hard budget or excluded territory;
- promote an unreviewed campaign verification into the global festival corpus;
- send forms, emails, messages, calendar invitations, or payments;
- apply a scenario to real state.

### 12.2 Confirmation policy

Typed UI actions such as **Record rejection** can be considered explicit confirmation when their payload is complete. Natural-language updates should first produce a command preview. Always require an additional confirmation when an action:

- changes screening/public-premiere history;
- accepts or schedules an option-destroying screening;
- withdraws a submission;
- changes/unlocks a hard or locked constraint;
- applies a scenario;
- corrects a prior event with downstream consequences.

The confirmation panel should show the before/after premiere ledger, invalidated routes, and any locked-decision conflict. “Agent recommends” must never look like “festival requires” or “user decided.”

## 13. API design

### 13.1 Course endpoints: immutable compatibility surface

Preserve these exactly:

- `GET /`
- `GET /api/team_info`
- `GET /api/agent_info`
- `GET /api/model_architecture`
- `POST /api/execute` with input `{"prompt": string}` and exactly `status`, `error`, `response`, `steps`

`GET /api/health` may remain. `/api/execute` must not implicitly create a campaign, return a campaign ID, accept follow-up state, or change its existing logging behavior. That would change the course contract even if the four response fields remained.

When the future architecture is live, update `/api/agent_info`, `assets/architecture.png`, and traced module names together in one integration change. Retain accurate normal call counts for the legacy path.

### 13.2 Workspace endpoints

Use a separate prefix and FastAPI router, e.g. `api/campaign_routes.py`:

| Method/path | Purpose | Key request/response behavior |
|---|---|---|
| `GET /api/workspace/bootstrap` | Initialize/read the anonymous workspace and company summary | Returns workspace ID, company, recent campaigns, feature availability; root quick strategy works without calling it |
| `POST /api/workspace/campaigns` | Create film project/campaign from text or structured profile | Returns campaign aggregate, proposed/confirmed facts, clarifications, and optional initial run ID |
| `GET /api/workspace/campaigns/{id}` | Read one aggregate view | One payload includes version, profile, constraints, premiere ledger, opportunities, events, active strategy, tasks, and trace references |
| `POST /api/workspace/campaigns/{id}/commands` | Apply one or a transaction-safe batch of typed commands | Requires `expected_version` and `idempotency_key`; returns new campaign version, event IDs, stale/replan status; 409 on race |
| `POST /api/workspace/campaigns/{id}/interpret` | Interpret natural-language update without mutation | Returns proposed commands, ambiguity/questions, and an effect preview; trace included if a model was called |
| `POST /api/workspace/campaigns/{id}/plan` | Create/retry a strategy from current state | Accepts `expected_version`; returns run status, strategy version, structured diff, and ordered trace |
| `GET /api/workspace/campaigns/{id}/strategies/{version}` | Inspect historical plan/evidence/trace | Immutable response; no recomputation |
| `POST /api/workspace/campaigns/{id}/scenarios` | Compute/save isolated what-if | Requires base version and typed hypothetical commands; returns result/comparison and `mutated_campaign=false` |
| `POST /api/workspace/campaigns/{id}/scenarios/{scenario_id}/apply` | Apply a saved scenario | Explicit confirmation token/flag plus unchanged base version; returns real event and new strategy versions |
| `GET /api/workspace/company` | Company profile and bounded relationship summaries | Includes imported-versus-campaign provenance and recency |

Avoid a separate mutation endpoint for every event type. One typed command boundary centralizes state-machine validation and idempotency. Read endpoints may be shaped for the UI, but domain rules must stay server-side.

### 13.3 Common workspace contracts

Commands:

```json
{
  "expected_version": 12,
  "idempotency_key": "9f2...",
  "commands": [
    {
      "type": "screening_occurred",
      "payload": {
        "festival_id": "docaviv",
        "occurred_at": "2026-05-22",
        "country": "Israel",
        "public_status": "public"
      }
    }
  ]
}
```

Errors should use normal HTTP status plus a stable body containing `code`, human-readable `message`, current campaign version where relevant, and field/transition defects. This richer error shape applies only to new workspace endpoints.

Every strategy-running response should include or reference ordered trace steps with the existing `{module, prompt, response}` shape. Provider secrets and raw vectors remain absent.

## 14. UI information architecture

### 14.1 Root behavior

The root must remain immediately useful and visibly satisfy the course UI:

- a **Quick Strategy** panel with textarea and **Run Agent** button calling `/api/execute`;
- final response and full collapsible steps trace;
- no login/signup/password-reset guard;
- an adjacent **Save as campaign** action that initializes anonymous persistence only when chosen.

Do not hide the required interface behind campaign onboarding.

### 14.2 Workspace navigation

Keep the frontend build-free initially, but split the single file into maintainable static assets (`public/index.html`, `public/styles.css`, `public/app.js`, and small `public/components/*.js` modules if needed).

Recommended layout:

```text
Company / Campaign switcher
  Campaign header: film, lifecycle, readiness, version, stale/active strategy
  ---------------------------------------------------------------
  Main strategy                         Right rail
  - Primary launch route                - Verification required
  - Conditional branches                - Human constraints/locks
  - Post-premiere route                 - Next actions
  - Timeline (known dates only)
  ---------------------------------------------------------------
  Tabs: Opportunities | Activity | Scenarios | Evidence & Trace | Company
```

### 14.3 Components and data dependencies

| Component | Displays/edits | Data source |
|---|---|---|
| Campaign switcher | Recent film projects, campaign state, stale badge | `/bootstrap` |
| Film Profile | Facts grouped by confirmed/asserted/inferred/unknown/contradicted; version history | Campaign aggregate/profile versions |
| Campaign Status | Lifecycle/readiness, active strategy version, last event | Campaign aggregate |
| Primary Launch Strategy | Primary path, why selected, option value, human gate | Strategy DAG + evidence refs |
| Alternative Routes | Rejection and mutually exclusive premiere alternatives | Strategy nodes/edges |
| Post-Premiere Route | Compatible downstream opportunities, relative ordering | Strategy DAG/compatibility edges |
| Timeline | Submission deadlines, known notifications/screenings, projected labels | Opportunity snapshots; never client-generated dates |
| Top Opportunities | Score, fit, rule, relationship, status, lock/exclude controls | Campaign opportunities |
| Verification Required | Priority, affected decision, source, resolve form | Verification tasks |
| Human Constraints | Structured hard/preference chips, origin, lock status | Campaign constraints |
| Activity | Append-only campaign events and resulting strategy versions | Event feed |
| Scenario Explorer | Typed event/constraint controls, comparison, apply/discard | Scenario endpoints |
| Company Relationships | Imported history plus new outcomes, recency/strength | Company endpoint |
| Evidence & Score Breakdown | Provenance, score arithmetic, uncertainty, policy versions | Opportunity/strategy snapshot |
| Execution Trace | Ordered deterministic/provider steps, collapsed by default | Strategy run trace |

Facts, constraints, recommendations, and uncertainty should have consistent labels/icons and accessible text, not color alone. A dependency graph or animation is unnecessary; the contingent route should read as a compact decision flow.

### 14.4 UI behavior constraints

- The frontend never recomputes premiere state, score, eligibility, or strategy.
- It sends `expected_version` on mutations and handles 409 by refreshing with an explicit conflict message.
- It does not optimistically display irreversible events as committed.
- Projected/unknown dates remain labeled and sort separately from exact dates.
- A stale strategy remains inspectable but cannot look current.
- Applying a scenario uses a before/after confirmation dialog.
- Trace is collapsed by default but complete and accessible.

## 15. Evaluation strategy

### 15.1 Extend, do not replace, the current framework

Keep the 66 unit tests and the current `evals/run_behavioral.py` isolation policy. The runner's separation of corpus presence, retrieval recall, scoring/ranking, and roadmap presentation is especially important and should be extended to campaign planning rather than flattened into one pass/fail.

Add deterministic fixtures and scenarios under new files such as:

- `tests/test_campaign_state.py`
- `tests/test_campaign_persistence.py`
- `tests/test_campaign_planner.py`
- `tests/test_campaign_replanning.py`
- `tests/test_scenarios.py`
- `tests/test_workspace_api.py`
- `evals/campaign_worker.py` or a clearly separated section in `behavioral_worker.py`

No judge LLM is required for core behavioral invariants.

### 15.2 Measurable invariants

**State and persistence**

- Save/reload produces the same typed campaign aggregate and active strategy IDs.
- Duplicate idempotency keys create one event and one version increment.
- Stale `expected_version` returns 409 and creates no event.
- Invalid transitions create no partial projection.
- Failed replanning preserves the committed event and prior active strategy with `stale=true`.

**Premiere semantics**

- Absent history remains unknown.
- Rejection, invitation, offer acceptance, scheduling, and private screenings do not consume premiere.
- Confirmed public screening consumes only justified scopes.
- A domestic screening does not manufacture known international availability.
- A correction event re-derives state without erasing history.
- Contradictory public/private assertions block or ask; they are not normalized silently.

**Clarification**

- Premiere-history contradiction outranks composer or general profile completeness.
- Questions whose answer does not change a decision are not shown in the primary clarification queue.
- A safe preliminary strategy is emitted when uncertainty affects only gated actions.
- A question names the decision it affects and does not assert an answer.

**Planning and option preservation**

- In a synthetic graph, festival A with immediate utility 90 that destroys two stronger downstream options loses to festival D with utility 82 when option preservation is enabled and no hard preference says otherwise.
- The same fixture selects A when `lambda_option=0`, proving the component has measurable effect.
- A hard preserve-world constraint removes option-destroying paths.
- A locked conflicting target is surfaced, not silently dropped.
- Unknown compatibility creates a verification gate and contributes only to the possible, not known, option band.
- Submission deadlines may order preparation before the launch screening without implying that screening occurs first.
- No output includes invented acceptance probabilities, notification dates, screening dates, or rules.

**Replanning**

- Rejection promotes a grounded alternative while preserving premiere state and creative scores.
- Screening removes incompatible routes and explains the exact edge/rule change.
- Deadline verification invalidates only the affected risk/plan artifacts.
- Each diff correctly classifies changed and unchanged recommendations.
- Typed routine events record zero model/embedding attempts when dependencies permit reuse.

**Scenarios**

- Simulation leaves every real table/version unchanged.
- Scenario comparison matches the deterministic aggregate diff.
- Applying a stale scenario fails; recomputing creates a new scenario base.
- Apply creates exactly the previewed commands and then a real new strategy version.

**Company memory**

- With/without memory changes only the bounded relationship dimension and possibly the plan through that bounded delta.
- Company history never rescues hard ineligibility or a severe creative mismatch.
- New confirmed screening/award events appear in company memory without modifying imported aggregates.

**Compatibility**

- `/api/execute` remains exactly four fields on every success/error path.
- The required root textarea, Run Agent action, response, and complete trace remain available without login.
- Architecture/module names stay synchronized after the new architecture is enabled.

### 15.3 Required ablations

Run paired evaluations for:

- CompanyMemory on/off;
- premiere unknown/confirmed available/already screened;
- before/after confirmed public screening;
- invited/rejected;
- user hard constraint absent/present;
- naive independent ranking versus CampaignPlanner;
- `lambda_option=0` versus default;
- premiere preservation hard/off;
- adult subject with teenage protagonist versus explicit youth audience;
- corpus entity absent versus present but retrieval miss;
- full re-execution versus dependency-aware incremental replan.

Report behavioral outcome, changed trace calls, and root-cause layer separately. Useful summary metrics include hard-invariant violations (target zero), incorrect certainty count (target zero), strategy stability for unaffected decisions, percentage of routine events with zero model calls, and candidate/branch recall on curated fixtures.

### 15.4 Cost controls for evaluation

Keep the current `0|1` live-run mechanism and global cap. Campaign lifecycle and ablation suites should use mocked LLM outputs and deterministic fixtures. A single live run can verify provider/tracing integration; it should not be multiplied across every scenario. Reports must continue redacting secrets and distinguishing skipped live evidence from passes.

## 16. Data and corpus strategy

### 16.1 Current limitation assessment

The current data is broad for an imported working list, but taxonomy counts show uneven specialist depth. Simple text checks find extensive documentary/animation/experimental language but very few explicit horror/fantasy entries. These substring counts are only a screening signal; they are not a quality judgment. The strongest concrete evidence is the behavioral failure: Sitges is absent, so no retrieval algorithm can recover it.

The current source model also has two temporal limitations:

- workbook dates/rules are snapshots and often projected at runtime;
- descriptive enrichment has confidence but not per-field source URL/as-of metadata.

Corpus expansion belongs in the broader iteration only as a controlled, separately approved data workstream. The campaign planner's credibility depends on coverage, but bulk additions without provenance would make the new option graph less trustworthy.

### 16.2 Controlled coverage audit

Add a read-only audit tool, for example `scripts/audit_corpus.py`, and a reviewed manifest such as `data/corpus_coverage_manifest.json`. The matrix should define expected representative festivals and minimum review coverage by:

- festival type: general IFF, documentary, short, animation, experimental, genre;
- geography: major regions and the company's operational territories;
- prestige: launch-tier and specialist anchors, not arbitrary row quotas;
- specialization: documentary, youth/family, LGBTQ, religion/faith, animation, experimental, horror/fantastic, human rights, environmental, art/music, ethnographic;
- format eligibility;
- provenance completeness: official URL, observed date, identity/rule confidence;
- entity identity/duplicate key.

For every manifest target, report independently:

1. entity present in corpus;
2. required structured fields present;
3. descriptive identity provenance sufficient;
4. retrievable in broad top-K for representative queries;
5. survives candidate cutoff;
6. receives appropriate qualitative scoring;
7. appears in the plan when strategically viable.

This extends the evaluation suite's existing root-cause classification.

### 16.3 Safe addition workflow

1. Identify a gap from the reviewed coverage manifest or failed evaluation.
2. Verify festival identity and current facts from official sources; record URL and observed date.
3. Separate hard structured facts from descriptive identity, exactly as current import/enrichment does.
4. Assign per-field confidence and reviewer status; do not infer unsupported rules/dates.
5. Add/update local corpus in a dedicated data-only branch.
6. Run data-quality, entity-deduplication, retrieval, scoring, and campaign-planning fixtures.
7. Review the diff manually.
8. Only after approval, seed Supabase and Pinecone in a controlled deployment step and verify ID/vector parity.

Sitges should be the first explicit gap candidate, but this document does not prescribe its facts and does not authorize adding or seeding it now.

### 16.4 What not to do

- Do not let campaign users directly edit global festival facts.
- Do not auto-scrape official sites during a Vercel request.
- Do not use an LLM-generated description as a high-confidence official rule.
- Do not report a missing entity as a retrieval failure.
- Do not bulk-add hundreds of famous festivals solely to improve apparent coverage.
- Do not re-embed the full corpus for rule/date-only changes; embeddings should change only when identity text changes.

## 17. Reliability and failure modes

| Risk | Protection |
|---|---|
| State corruption | Typed commands; DB checks; one transactional event+projection boundary; append-only audit; replay invariant tests |
| Contradictory events | Transition preconditions; `contradicted` fact state; ambiguity preview; compensating correction events rather than destructive edits |
| Duplicate user action/retry | Per-campaign idempotency key and unique constraint; return original result on safe retry |
| Two tabs/race condition | Optimistic campaign version; HTTP 409 with current version; no last-write-wins |
| Event committed, replan failed | Persist event, set strategy stale, retain last active strategy, expose retry and failed trace |
| Strategy written from stale state | Strategy records input campaign version; activation transaction checks campaign is still at that version |
| Provider or timeout failure | Existing traced bounded fallback for retrieval; previous active strategy survives; no silent loss of durable state |
| Supabase outage | Read-only quick strategy may still use bundled corpus; durable workspace mutations fail explicitly and never claim persistence |
| Stale festival facts | Evidence versions, as-of/confidence labels, verification tasks, campaign overlays, projected-date policy |
| LLM hallucination/malformed output | Typed schema, known-ID validation, one targeted repair, deterministic hard rules and arithmetic, no LLM state transitions |
| Cache misuse after change | Component/input hashes plus explicit dependency registry and traced reuse decision |
| UI/backend disagreement | Server aggregate is authoritative; UI does no domain computation; response includes version and strategy staleness |
| Migration failure | Additive migrations, separate environment rehearsal, backups, feature flag, no old-table drops, legacy suite before/after |
| Scenario leaks into real state | Simulation repository exposes no mutation method; base-version apply check; `mutated_campaign=false` assertion |
| Unknown date sorted as known | Typed timing with confidence/status; null remains null; UI groups unscheduled/verify items |
| Locked decision overridden | Planner validator requires lock satisfaction or explicit conflict node; only human command unlocks |
| Trace/data leakage | Existing secret-free prompts; redact auth headers/tokens; avoid storing unnecessary raw user text; RLS owner isolation |

The planner should be deterministic given the same versioned inputs and as-of date. When current date affects a deadline projection, record that date in the strategy input hash and trace.

## 18. Cost and performance

### 18.1 Expected call profile

| Operation | Chat calls | Embeddings | Typical DB operations |
|---|---:|---:|---:|
| Legacy `/api/execute` | 3 normal; bounded repairs as today | 1 when configured | Existing reads + best-effort run log |
| Initial campaign from free text | At most 3 | 1 | Aggregate create transaction, evidence reads, strategy/run insert |
| Initial campaign from confirmed form | At most 2 | 1 | Same |
| Typed rejection/invitation/lock/exclude | 0 | 0 | One event transaction, one strategy/run transaction/read |
| Confirmed screening | 0 | 0 | One event transaction, affected evidence read, one strategy insert |
| Natural-language event preview | 1 | 0 | One aggregate read; no mutation |
| Scenario over current candidates | 0 | 0 | One aggregate read; optional saved scenario insert |
| Creative identity change | 1 analyzer if unstructured + 1 scorer; RoadmapBuilder only if justified | 1 | Event/profile transaction plus evidence/strategy writes |

### 18.2 Database efficiency

- Add one aggregate read function/view so campaign detail does not issue a dozen serial browser requests.
- Keep event application atomic in one RPC/transaction.
- Load only current strategy in the campaign aggregate; fetch historical strategy traces on demand.
- Precompute/merge company relationship summaries server-side; current imported history is only 171 aggregate rows, so a new vector store for company memory is unnecessary.
- Cache retrieval and creative scores by profile identity hash, festival identity version, model/prompt version, and company relationship version.
- Store trace JSON once per run; do not duplicate it in every strategy node.

### 18.3 Vercel budget

Retain the current 260-second hard application budget and 300-second function ceiling for the initial provider-heavy path. The historical reduction from 16 to 12 candidates was evidence-driven (a 16-candidate production run took 188 seconds), so the new planner must not expand the LLM candidate batch by default.

Campaign event replans should target sub-second to low-single-digit deterministic computation plus Supabase latency, and comfortably under 15 seconds end to end. Graph search over 12 nodes, depth three, and beam width five is negligible relative to provider calls.

Do not introduce a background job platform in the first iteration. If an initial plan times out, persist a failed run/stale state and expose retry. Vercel's serverless process cannot be treated as a durable worker.

## 19. Security and privacy

Relevant protections only:

- Keep `/` and `/api/execute` public and rate/size bounded as today; do not add a login guard.
- Scope campaign rows with Supabase anonymous-user RLS. Never expose the service-role key to the browser.
- Validate workspace/campaign ownership on every new route; never trust a campaign ID alone.
- Use UUIDs but do not treat UUID obscurity as authorization.
- Accept only known command/event types and Pydantic payloads; reject extra dangerous fields.
- Use idempotency and version checks to prevent replay/race corruption.
- Escape all user/festival text in the UI as the current frontend does; do not render untrusted HTML/Markdown directly.
- Keep the 12,000-character public input ceiling and add small limits to event text, scenario command count, and stored trace payload size.
- Redact authorization headers, API keys, cookies, and provider response metadata from traces/errors.
- Continue excluding contacts, email addresses, invoices, and nonessential fees from imported company data.
- Treat user film descriptions, strategy, outcomes, and screening history as private workspace data even when the demo company profile is public/anonymized.
- Add retention/archive controls only if needed; do not build enterprise roles, SSO, audit exports, or compliance theater.

The current public select policies on `companies` and `company_festival_history` are acceptable only for the deliberately anonymized demo baseline. They should not be copied to new campaign tables or to future real-company data.

## 20. Implementation plan

Implementation must proceed behind a feature flag and preserve a green legacy suite after every phase. File lists below are likely ownership, not permission to edit them during this planning pass.

### Phase 0 — Freeze contracts and fixtures

**Goal**

Define the campaign schemas, enums, command contracts, trace names, planner-policy version, and golden deterministic fixtures before persistence or UI code.

**Production files likely to change**

- new `app/campaign/__init__.py`
- new `app/campaign/models.py`
- new `app/campaign/contracts.py`
- `app/config.py` for a disabled-by-default feature flag only
- design/architecture source later, but do not change `assets/architecture.png` yet

**Tests/evals to add**

- model validation and JSON round-trip tests;
- command/event transition fixture definitions;
- a frozen compatibility-graph fixture with known/unknown/incompatible edges;
- explicit legacy endpoint contract test retained unchanged.

**Dependencies**

None beyond the current Pydantic dependency.

**Expected risks**

Premature schema complexity; ambiguous naming around `selected` versus `accepted`; accidental divergence from current domain enums.

**Acceptance criteria**

- Every requested lifecycle action maps to one typed command/event.
- Premiere ledger uses tri-state semantics.
- Existing 66 tests pass.
- No runtime path changes when the feature flag is false.

**Parallel safety**

This phase is the shared contract gate and should not be implemented in parallel with downstream production work. Review/merge it first.

### Phase 1 — Persistence and deterministic state engine

**Goal**

Persist workspaces, film/profile versions, campaigns, constraints, events, submissions, screenings, tasks, runs, and strategies; implement atomic versioned transitions and premiere reduction.

**Production files likely to change**

- new `scripts/migrations/001_campaign_workspace.sql`
- new `app/campaign/repository.py`
- new `app/campaign/state.py`
- new `app/campaign/premiere.py`
- new `app/stores/campaign_store.py`
- small reusable helper extraction from `app/agent/domain.py` only if necessary
- `.env.example` for feature/auth configuration only after chosen access model is validated

**Tests/evals to add**

- `tests/test_campaign_state.py`
- `tests/test_campaign_persistence.py`
- replay/projection equality;
- idempotency, optimistic concurrency, illegal transitions, compensation;
- public/private/unknown screening premiere invariants;
- mocked Supabase failure and transaction tests.

**Dependencies**

Phase 0 contracts; a non-production Supabase project for integration tests.

**Expected risks**

RLS/anonymous-auth configuration; transaction semantics through PostgREST; two-source inconsistency between events and projections.

**Acceptance criteria**

- Atomic command tests pass under duplicate/racing requests.
- Save/reload/replay yields the same aggregate.
- A confirmed public screening reproduces existing domain semantics and never manufactures availability.
- No production seeding or baseline-table destructive migration.

**Parallel safety**

After Phase 0, persistence/state can run in parallel with the pure planner work in Phase 2 if both consume the frozen models and neither edits the other's files.

### Phase 2 — Campaign planner and clarification engine

**Goal**

Implement the compatibility graph, option-value diagnostics, bounded search, strategy DAG validation, and decision-impact clarification using pure in-memory models.

**Production files likely to change**

- new `app/campaign/compatibility.py`
- new `app/campaign/planning.py`
- new `app/campaign/clarification.py`
- new `app/campaign/validation.py`
- possibly additive functions in `app/agent/domain.py` and `app/agent/scoring.py`, owned by one designated integration agent

**Tests/evals to add**

- `tests/test_campaign_planner.py`
- deterministic Pareto/beam fixtures;
- option preservation on/off ablation;
- locked/excluded/budget constraints;
- unknown compatibility gates;
- no fabricated schedule/outcome facts;
- decision-impact question prioritization.

**Dependencies**

Phase 0 models and the existing domain/scoring semantics. It does not need Supabase.

**Expected risks**

An arbitrary option-policy weight; double-counting current score dimensions; confusing submission timing with screening order; a graph that looks sophisticated but adds no measurable behavior.

**Acceptance criteria**

- Synthetic counterfactuals prove that option preservation changes a justified route.
- `lambda_option=0` reproduces naive/immediate behavior in the paired fixture.
- Unknown edges remain verification branches.
- Planner is deterministic and fast for 12 candidates.
- Distributor/domain review accepts the strategy-weight explanation.

**Parallel safety**

Safe in parallel with Phase 1 after Phase 0. It must not edit persistence/API/UI files.

### Phase 3 — Incremental orchestration and evidence reuse

**Goal**

Connect campaign snapshots to the existing analyzer/retrieval/risk/scoring chain, add dependency-based invalidation, make Replanner lifecycle-aware, and persist strategy runs/diffs.

**Production files likely to change**

- new `app/campaign/replanning.py`
- new `app/campaign/orchestrator.py`
- new `app/campaign/rendering.py`
- narrow adapters in `app/agent/modules.py`, `app/agent/graph.py`, and/or `app/agent/prompts.py`
- `app/stores/supabase_store.py` or new store adapters for merged company events
- `app/config.py` for campaign call/time policies

**Tests/evals to add**

- `tests/test_campaign_replanning.py`
- reuse/invalidation matrix tests;
- zero-call routine event assertions from trace;
- failed replan/stale strategy behavior;
- unchanged recommendation/diff invariants;
- company-memory bounded counterfactual with new events.

**Dependencies**

Phases 1 and 2.

**Expected risks**

Accidentally rerunning the whole pipeline; cache reuse after a hidden dependency changed; changing legacy trace behavior; RoadmapBuilder calls becoming routine again.

**Acceptance criteria**

- Rejection and screening examples generate correct new strategies/diffs.
- Typed routine events use zero model/embedding calls where declared.
- Creative profile changes invalidate exactly the expected artifacts.
- Legacy `/api/execute` output and tests remain unchanged.

**Parallel safety**

This is an integration phase and should be owned by one branch/agent after Phases 1 and 2 merge. UI can develop against frozen fixtures in parallel, but not against moving route contracts.

### Phase 4 — Workspace API and anonymous persistence boundary

**Goal**

Expose owner-scoped read/command/plan endpoints without altering course endpoints.

**Production files likely to change**

- new `api/campaign_routes.py`
- minimal `api/index.py` change to include the router
- new `app/campaign/auth.py` or request-context helper if needed
- `app/campaign/repository.py`
- `.env.example`
- `vercel.json` only if include/runtime configuration actually requires it

**Tests/evals to add**

- `tests/test_workspace_api.py`
- RLS/ownership tests in an isolated Supabase environment;
- 401/403 for workspace resources without breaking public root/execute;
- 409 concurrency, idempotent retry, structured validation errors;
- exact course API regression.

**Dependencies**

Phases 1 and 3; reviewed anonymous-auth or capability-token decision.

**Expected risks**

Confusing “no login guard” with “no authorization”; leaking one anonymous workspace to another; service-role misuse; synchronous timeout while persisting a plan.

**Acceptance criteria**

- Root and all required course endpoints work with no auth session.
- Workspace rows are isolated between two anonymous users.
- Browser never receives a service-role key.
- All mutations use expected version and idempotency.

**Parallel safety**

The API route owner alone edits `api/index.py`. The UI may proceed in parallel once response fixtures are frozen.

### Phase 5 — Scenario explorer

**Goal**

Implement copy-on-write simulation, deterministic comparison, save/discard, and guarded apply.

**Production files likely to change**

- new `app/campaign/scenarios.py`
- `app/campaign/orchestrator.py`
- `api/campaign_routes.py`
- `app/stores/campaign_store.py`

**Tests/evals to add**

- `tests/test_scenarios.py`
- no-mutation snapshots across every scenario type;
- stale-base apply rejection;
- preview/apply command equality;
- rejection, premiere-at-Docaviv, preserve-policy-off, exclusion, and deadline-change scenarios.

**Dependencies**

Stable StateTransitionEngine, Replanner, planner, and workspace endpoints.

**Expected risks**

Simulation accidentally calling mutating repositories; hidden divergence between simulated and real reducers; stale scenario application.

**Acceptance criteria**

- Database is byte/logically unchanged after unsaved preview.
- Scenario and real apply produce the same deterministic state/plan from the same base.
- Comparison names changed, newly available/unavailable, and unchanged decisions.

**Parallel safety**

Can run in parallel with most UI implementation after Phase 4 contracts freeze, but scenario API files need one owner.

### Phase 6 — Professional root/workspace UI

**Goal**

Add durable campaign navigation and decision-focused views while retaining the course quick-run interface and trace.

**Production files likely to change**

- `public/index.html`
- new `public/styles.css`
- new `public/app.js`
- optional new `public/components/*.js`

**Tests/evals to add**

- static/API integration tests for required root controls;
- browser tests for create/resume, conflict refresh, event confirmation, stale strategy, scenario apply/discard, and trace visibility;
- accessibility checks for keyboard/focus/status labels;
- mobile/narrow layout smoke checks.

**Dependencies**

Frozen Phase 4 API fixtures; Phase 5 for final Scenario panel.

**Expected risks**

Breaking the required minimal GUI while chasing a dashboard; implementing domain logic client-side; losing trace completeness; a single static file becoming unmaintainable.

**Acceptance criteria**

- A grader can still paste a prompt and run `/api/execute` immediately.
- A user can create/resume a campaign, understand facts versus constraints versus recommendations, record an event, see the diff, and inspect trace.
- No known/projection/unknown state is visually conflated.

**Parallel safety**

Safe in a dedicated UI worktree once API fixtures are frozen. The UI workstream owns only `public/**` and browser tests.

### Phase 7 — Evaluation, corpus audit, and release gate

**Goal**

Prove that campaign components materially improve behavior, identify coverage gaps, and release without regressing the submission-ready baseline.

**Production files likely to change**

- `evals/run_behavioral.py`
- `evals/behavioral_worker.py` or new `evals/campaign_worker.py`
- `evals/README.md`
- new `scripts/audit_corpus.py`
- new `data/corpus_coverage_manifest.json`
- `tests/test_data_quality.py` and new campaign tests
- `scripts/make_architecture.py`, `assets/architecture.png`, `README.md`, and `/api/agent_info` descriptions only after behavior is final

**Tests/evals to add**

All invariants/ablations in Section 15; coverage-layer reports; one bounded live integration run only when approved.

**Dependencies**

All earlier phases. Any actual corpus additions are a separate reviewed change followed by explicitly approved seeding/deployment.

**Expected risks**

Optimizing for fixture theatrics; changing data and code together so root cause becomes ambiguous; architecture diagram names drifting from trace; accidental provider spend.

**Acceptance criteria**

- Legacy 66 tests and new suites pass.
- Behavioral report has no hard-invariant failure.
- Ablations show measurable value for stateful replanning, option preservation, explicit constraints, and bounded company memory.
- Course endpoint, trace, budget, and root UI checks pass locally and in an isolated deployment.
- Corpus audit clearly separates missing data from retrieval/planning failures.

**Parallel safety**

Evaluation fixtures/tooling can run in parallel after contracts freeze, but final recorded examples, architecture diagram, README, and release report must be regenerated serially from the integrated commit.

### Phase 8 — Optional natural-language event input

**Goal**

Only after typed lifecycle controls are reliable, add concise natural-language updates such as “Hot Docs rejected us” as command previews.

**Production files likely to change**

- `app/campaign/event_interpreter.py`
- additive prompt in `app/agent/prompts.py` or a new campaign prompt module
- `api/campaign_routes.py`
- UI command composer

**Tests/evals to add**

- ambiguous accepted/selected wording;
- unknown festival/entity resolution;
- multi-event text;
- invented date/festival rejection;
- trace/call-count and no-mutation-before-confirmation.

**Dependencies**

Stable typed commands, confirmation UX, and full lifecycle tests.

**Expected risks**

Natural-language magic bypassing state invariants; spending a model call on every simple button action; silently applying the wrong event.

**Acceptance criteria**

- Interpreter only proposes known typed commands.
- Ambiguity is surfaced.
- No state changes before confirmation.
- Typed actions remain the default zero-call route.

**Parallel safety**

Optional and late. It should not block the core release.

## 21. Parallel-agent execution plan

This is the plan for a later implementation session using separate git worktrees and branches. No worktrees or branches are created by this planning task.

### 21.1 Integration topology

Create an integration branch such as `codex/campaign-workspace-v2` from the approved baseline. Create sibling worktrees outside the main checkout, each on a `codex/` branch. Do not let multiple agents edit the same shared files.

Suggested workstreams after Phase 0 contracts merge:

| Workstream/branch | Owned files | May run concurrently with | Must wait for |
|---|---|---|---|
| `codex/campaign-state` | `app/campaign/models.py`, `state.py`, `premiere.py`, repository/store, migration, state/persistence tests | Pure planner | Phase 0 contracts |
| `codex/campaign-planner` | `compatibility.py`, `planning.py`, `clarification.py`, `validation.py`, planner tests | State/persistence | Phase 0 contracts |
| `codex/campaign-eval-fixtures` | New campaign eval fixtures/worker without modifying production | State and planner | Phase 0 contracts/fixtures |
| `codex/campaign-integration` | `orchestrator.py`, `replanning.py`, adapters in existing `app/agent/**` | UI fixtures only | State and planner merged |
| `codex/campaign-api` | `api/campaign_routes.py`, sole ownership of `api/index.py`, workspace API tests | UI after contracts freeze | Integration + auth decision |
| `codex/campaign-ui` | `public/**`, browser/static tests | Scenarios and corpus audit | Frozen API fixtures |
| `codex/campaign-scenarios` | `scenarios.py`, scenario tests; API changes coordinated through API owner | UI | State, planner, API contracts |
| `codex/corpus-audit` | `scripts/audit_corpus.py`, manifest, data-quality tests only | Most code work | Coverage contract; no corpus mutation |
| `codex/campaign-release-docs` | README, architecture generator/image, agent-info text, examples/report | Nothing touching same files | Fully integrated behavior |

### 21.2 Shared-file rules

- Only the integration workstream may edit existing `app/agent/domain.py`, `modules.py`, `graph.py`, `scoring.py`, or `prompts.py` after reviewing pure-module needs from other branches.
- Only the API workstream edits `api/index.py` and `api/campaign_routes.py` during its phase.
- Only the UI workstream edits `public/**`.
- Only the release workstream edits `README.md`, `scripts/make_architecture.py`, `assets/architecture.png`, recorded prompt examples, and the final evaluation report.
- Migrations are append-only and numbered; never have two branches invent the same migration number.
- Tests should live with their owning implementation except cross-cutting behavioral fixtures, which have a designated eval owner.

### 21.3 Merge order and gates

1. Merge Phase 0 contracts into the integration branch after review.
2. Run state/persistence, planner, and eval-fixture branches concurrently.
3. Merge state first, run all tests; merge planner second, run all tests; merge eval fixtures third.
4. Implement/merge incremental orchestration and resolve all adapter changes centrally.
5. Freeze workspace API fixtures; then run API, UI, and scenario work with explicit file ownership.
6. Merge API, then scenarios, then UI; run legacy, new unit, browser, and behavioral suites after each merge.
7. Merge corpus audit tooling without corpus additions.
8. Serially regenerate architecture, `/api/agent_info` examples, README, and evaluation report from the exact integrated commit.
9. Only after human approval: apply migrations in an isolated environment, run smoke tests, then separately approve production migration/seeding/deployment.

### 21.4 Agent handoff requirements

Each workstream should hand off:

- commit hash and exact baseline;
- files changed and explicit shared files not touched;
- tests run and outputs;
- assumptions or contract deviations;
- migration/provider actions explicitly **not** performed;
- unresolved risks.

Agents must not reformat unrelated files, commit `.env`/artifacts, seed providers, deploy, or regenerate recorded examples from a partially integrated branch. Prefer cherry-picking reviewed commits over merging overlapping long-lived branches.

## 22. Scope control

### MUST

- Preserve the exact course endpoints, public quick-run root flow, and complete trace.
- Add persistent film projects/campaigns with versioned facts, constraints, events, submissions, screenings, verification tasks, and strategy versions.
- Implement deterministic, tri-state premiere reduction from confirmed public screening history.
- Make human constraints structured, sourced, lockable, and authoritative over agent preferences.
- Add decision-impact clarification without a new routine LLM call.
- Add compatibility-graph campaign planning with explicit, non-probabilistic option preservation.
- Represent primary/alternative/post-premiere/verification routes as validated data.
- Replan incrementally on typed lifecycle events and explain changed/unchanged decisions.
- Simulate on isolated snapshots and require explicit apply.
- Extend unit/behavioral evaluation with counterfactuals, ablations, call-count assertions, and state invariants.
- Keep initial cost at or below the existing normal call profile and most routine replans at zero calls.

### HIGH VALUE

- Visible company workspace and new outcome accumulation with bounded influence.
- Browser-owned anonymous persistence with no visible login wall.
- Natural-language event interpretation as a confirmation preview after typed events are stable.
- Coverage manifest/audit and a small reviewed gap set, starting with Sitges after approval.
- Structured strategy/version comparison and historical trace browsing.
- Budget tracking when fees are known, with verification for unknown fees.

### OPTIONAL

- Named/saved scenario library rather than ephemeral comparison only.
- Upgrade from anonymous session to a real account later.
- CSV/JSON campaign export.
- More than one campaign per film for territory/re-release strategies.
- More than depth-three planning after evidence demonstrates value.
- Limited reminder links or calendar export after the core state model is stable.

### DO NOT BUILD in this iteration

- Generic signup/login/password-reset/account administration.
- Multi-company roles, invitations, permissions dashboards, SSO, or billing.
- Acceptance probabilities, expected revenue, or fabricated notification/screening dates.
- Autonomous submission, email, outreach, payment, or offer acceptance.
- Runtime web scraping or automatic unsupervised global festival-rule updates.
- A general-purpose workflow/BPM engine or arbitrary event schema.
- A heavy MILP/OR-Tools optimizer before the heuristic is proven insufficient.
- Fake sub-agents that merely wrap prompts or a multi-agent swarm for appearance.
- Vector storage for campaign state or company memory when relational retrieval is sufficient.
- A broad corpus expansion without official-source provenance and review.
- A visual graph gimmick that obscures the primary route, gates, and required human decisions.

## 23. Grading-value analysis

| Capability | Academic AI-agent value | Why it is more than web-app surface | Required evidence |
|---|---|---|---|
| Persistent campaigns | High as an enabler | Gives the agent state across time so later observations can change reasoning rather than restart from a prompt | Reload/replay and multi-event lifecycle eval |
| Versioned facts/uncertainty | High | Prevents hallucinated certainty and supports grounded reasoning across corrections | Unknown/contradiction/provenance invariants |
| Structured human constraints | High | Establishes an explicit human-agent authority boundary and measurable constraint satisfaction | With/without constraint counterfactuals |
| Decision-aware clarification | High | Selects information by expected decision impact rather than generic completeness | Question-priority sensitivity tests |
| Campaign planner/option value | Very high | Demonstrates global reasoning over irreversible choices, not independent ranking | Naive-vs-option ablation on known compatibility graph |
| Contingent strategy DAG | High | Encodes future actions under grounded outcomes and verification, enabling lifecycle reasoning | Graph invariants and branch-grounding tests |
| Incremental replanning | Very high | Observes environment changes, preserves valid work, changes only affected decisions, and explains causality | Event sequences, reuse traces, zero-call routine replans |
| Scenario explorer | High | Uses the same transition/planning model counterfactually with safe isolation | No-mutation and apply-equivalence tests |
| Company memory | Medium-high | Makes long-term memory affect decisions in a bounded, auditable way | Memory ablation and <= relationship-weight influence |
| Verification tasks/evidence versions | High | Turns uncertainty into executable human work and later grounded state | Unknown-to-verified transition tests |
| Professional UI | Medium | Makes state, branches, uncertainty, and human gates inspectable; value depends on accurately exposing the agent model | Usability/trace/state-origin checks |
| Corpus audit/targeted gaps | Medium | Demonstrates diagnostic separation of data, retrieval, scoring, and planning | Coverage-layer report and Sitges root-cause closure |
| Authentication/accounts | Low for this assignment | Operational plumbing does not improve agent reasoning; invisible anonymous ownership is enough for safe persistence | Security isolation only |
| Natural-language event parser | Medium | Useful semantic-to-command boundary, but only if it cannot bypass deterministic state rules | Ambiguity/no-mutation evals |

The strongest submission story is: a persistent agent observes events, manages epistemic state, asks only decision-changing questions, plans over irreversible premiere choices, simulates counterfactuals, and proves through ablation that these components change behavior while preserving deterministic correctness and low cost.

## 24. Risk-adjusted final recommendation

### 24.1 Most ambitious version that is still safe

Implement one browser-owned workspace for the anonymized Meridian Films context, with multiple film campaigns but no generic accounts. Support:

- profile/version/constraint persistence;
- typed events for submitted, rejected, invited, offer accepted/declined, scheduled, screened, withdrawn, rule/deadline verified, preference changed, and lock/unlock;
- deterministic premiere ledger;
- a 12-candidate compatibility graph with one primary launch route, up to two grounded launch alternatives, and a post-premiere route to depth three;
- option-value interval and one versioned default preservation weight;
- blocking/high/useful clarification;
- incremental zero-call replans for operational events;
- isolated scenarios for rejection, screening-first, preserve-policy change, territory exclusion, and deadline verification;
- campaign detail, activity, opportunity, verification, scenario, company relationship, evidence, and trace views;
- comprehensive deterministic evals and corpus coverage audit tooling.

Defer natural-language event input until the typed workflow is stable. Defer broad corpus changes; after the audit, add Sitges and only a handful of reviewed specialist anchors in a separate approved data release. Defer full accounts, collaboration, notification systems, live data ingestion, and solver complexity.

This scope is ambitious because it changes the agent's temporal and decision model, yet safe because it is additive, small-candidate, deterministic at critical boundaries, compatible with the existing provider budget, and protected by the one-shot baseline.

### 24.2 Stop/go gates

Do not proceed past contract design until the five assumptions below are reviewed. Do not enable the feature in production until persistence isolation, option-policy behavior, state-machine invariants, incremental call counts, and exact course compatibility all pass.

### 24.3 Five highest-risk assumptions requiring review

1. **Anonymous persistence is deployable and acceptable.** The recommended silent Supabase anonymous-auth/RLS model must work within the course Vercel/Supabase setup while the root quick flow remains fully public. If not, a capability-token design needs separate review; a shared writable demo is not acceptable.
2. **Current premiere evidence is sufficient for useful graph edges.** Many rules are ambiguous shorthand or stale. The planner may initially produce many `verify` edges; stakeholders must accept a verification-heavy but honest plan rather than pressure the system into false certainty.
3. **Relative contingent planning is valuable without full notification/screening dates.** The corpus often cannot support a calendar-optimized branch tree. The safe design provides relative order and gates, not invented timing; a distributor should confirm this remains professionally useful.
4. **The option-value proxy and default weight reflect real strategy.** `strategic_weight` and `lambda_option=0.25` are transparent policy choices, not facts. They need domain review and ablation before they can select the primary route.
5. **Initial-run latency remains inside a safe Vercel margin after persistence.** The provider-heavy path already motivated reducing the candidate pool from 16 to 12. Aggregate reads/writes, trace persistence, and any added prompt context must be benchmarked so the campaign path does not approach the 300-second ceiling or exceed the project budget.

Approval should first resolve these assumptions and confirm the MUST/HIGH VALUE boundary. Only then should Phase 0 contracts begin.

# The Distributor — Campaign Workspace Architecture v2

**Status:** final reconciled design; implementation is not authorized by this document  
**Baseline:** current `campaign-workspace` branch and the existing public Quick Strategy agent  
**Inputs reconciled:** `campaign_workspace_design.md`, `campaign_workspace_architecture_review.md`, repository constraints, and owner review decisions

---

## 1. Executive product thesis

The next iteration should turn The Distributor from a strong one-shot recommendation agent into a small, persistent decision agent for one film-festival campaign. Its academic claim is not that it has more screens, tables, prompts, or agents. Its claim is that it can maintain an epistemically honest campaign state, reason globally about an irreversible premiere decision, react to real events without redoing valid work, and prove those behaviors through deterministic tests and ablations.

The product should therefore do five things exceptionally well:

1. persist a film campaign and its human decisions across time;
2. derive premiere availability from evidence rather than inventing or flattening it;
3. choose a launch route using all pairwise premiere consequences, not independent festival ranking;
4. produce a new, explainable strategy after an event while reusing unaffected evidence; and
5. ask only questions that can change the current decision.

Everything else is supporting infrastructure. The implementation should remain additive to the submission-ready baseline, use the existing candidate ceiling of 12, keep critical reasoning deterministic, and expose the advanced behavior on one compact Campaign page. The existing Quick Strategy page and exact course endpoints remain available without a login wall.

## 2. What changed from v1 and why

### 2.1 Reconciliation method

The original design correctly identified the intellectual center: durable state, premiere-aware compatibility, option preservation, human constraints, lifecycle replanning, clarification, counterfactuals, and ablations. The adversarial review correctly identified that v1 surrounded that center with too much product and persistence machinery. V2 keeps the reasoning model and removes infrastructure that does not materially improve the academic demonstration.

The classifications below address the reviewer's major recommendations explicitly.

| Reviewer recommendation | Decision | Reconciled rationale for this project |
|---|---|---|
| Keep the new system additive and protect `/api/execute` | **ACCEPT** | This is the lowest-regression seam and preserves the already-tested course submission. Campaign orchestration is a new path that reuses existing modules rather than replacing the one-shot path. |
| Use a `workspace_id` in `localStorage` with no real authorization | **REJECT** | A UUID is an identifier, not authority. V2 uses a high-entropy, server-issued opaque capability in an HttpOnly cookie. It adds no accounts or visible auth flow but prevents casual cross-workspace access. |
| Do not build Supabase anonymous auth/RLS | **ACCEPT WITH MODIFICATION** | V2 avoids anonymous identities and per-user RLS. New campaign tables are inaccessible to browser roles; a server-only repository resolves the capability and scopes every query. RLS remains deny-by-default defense, not the identity system. |
| Reduce the 15-table persistence design to roughly eight tables | **ACCEPT** | Eight new tables are sufficient. Current projections plus append-only events and immutable strategy snapshots preserve the demonstration without a platform-sized schema. |
| Remove `FilmProfileVersion` | **ACCEPT WITH MODIFICATION** | The current profile lives on `film_projects`; profile-changing events preserve history, and every strategy version embeds the complete input snapshot and hash. This preserves reproducibility without a dedicated version table. |
| Merge `StrategyRun` into `StrategyVersion` | **ACCEPT** | Trace, usage, reuse, failure, input snapshot, diff, and plan belong to one immutable planning-attempt record. The campaign points only to the latest successful active version. |
| Store verification tasks as JSON | **ACCEPT** | Verification items are bounded annotations on current opportunities and strategy gates. Resolution is still a typed event. A separately queryable workflow table is unnecessary. |
| Merge submissions and screenings into opportunities | **ACCEPT WITH MODIFICATION / REJECT IN PART** | Current submission and invitation state is folded into `campaign_opportunities`. Screenings remain a separate table because one film may have multiple scheduled, cancelled, private, and public screenings at the same festival or in different places. Premiere derivation must reason over each occurrence. |
| Persist scenarios and support save/apply | **REJECT** | V2 scenarios are ephemeral: clone, reduce, replan, compare, discard. Applying a real change is a separate normal command. This eliminates scenario lifecycle and stale-apply complexity without weakening counterfactual reasoning. |
| Replace the general strategy DAG with a simple plan | **ACCEPT WITH MODIFICATION** | The directed compatibility graph remains internal and academically important. The external `CampaignPlan` is constrained to one primary route, at most two alternatives, rejection and screening branches, verification gates, and post-premiere opportunities. No general workflow engine is built. |
| Use a destruction penalty with another lambda | **REJECT** | Replacing one arbitrary coefficient with another does not solve the defense problem. V2 uses hard constraints, Pareto dominance, and an explicit user-selected lexicographic/rank policy. There is no acceptance probability or financial-value claim. |
| Exclude LLM-derived creative judgments from future-option importance | **REJECT** | Preserving a creatively irrelevant festival is not strategically valuable. Guarded semantic ratings are frozen, validated evidence; deterministic arithmetic can legitimately consume them. Provenance is explicit, and a count-only/deterministic-only ablation tests the choice. |
| Use only a static clarification catalog | **ACCEPT WITH MODIFICATION** | The catalog supplies safe dependencies and priorities, but current-plan dependency checks and a few bounded deterministic counterfactuals decide whether a missing fact can actually change the route. No extra LLM call is added. |
| Collapse replanning to identity versus operational change | **ACCEPT WITH MODIFICATION** | V2 uses three invalidation levels. A public screening or verified premiere rule must rerun RiskChecker and compatibility, while a rejection or lock needs planning only. The model remains small but technically correct. |
| Hardcode two or three scenario functions | **ACCEPT WITH MODIFICATION** | The UI exposes only two or three compelling controls, but the engine accepts generic typed commands and calls the same reducer used for real changes. This avoids a demo-specific architecture. |
| Build a single compact campaign dashboard | **ACCEPT** | One page is enough to expose state, route, alternatives, constraints, clarification, events, scenarios, diff, evidence, and trace. It is more legible to a grader than a multi-tab SaaS shell. |
| Keep existing CompanyMemory; defer new feedback loops | **ACCEPT** | The existing 171 relationship aggregates already support a meaningful bounded-memory ablation. Updating global company memory from campaign outcomes is not required for the central claim. |
| Audit corpus coverage; add Sitges and a few gaps later | **ACCEPT WITH MODIFICATION** | The audit is valuable and Sitges is a confirmed missing entity. Any corpus change remains a separate reviewed data change after the agent core is stable; v2 does not authorize seeding or re-embedding. |
| Simplify premiere logic to only the most common cases | **REJECT** | Territorial and international semantics already exist in `app/agent/domain.py` and are part of the baseline's correctness. Removing them would be a regression. V2 reuses and tests those rules rather than inventing a second, weaker ledger. |
| Split implementation into many workstreams | **REJECT** | At most three coding agents should run concurrently. Shared contracts are serial, integration is single-owner, and only isolated file groups proceed in parallel. |
| Add a second campaign serverless entry point | **REJECT** | Workspace routes must be a FastAPI `APIRouter` imported by the existing `api/index.py` app. `vercel.json` keeps its single catch-all function. |

### 2.2 Net architectural changes

V2 replaces 15 proposed tables with eight, removes accounts, saved scenarios, general DAG rendering, evidence-versioning infrastructure, company feedback ingestion, and routine natural-language event parsing. It replaces weighted option arithmetic with an exact Pareto/rank policy, replaces a detailed dependency registry with three invalidation levels plus hashes, and replaces the multi-tab workspace with one Campaign page.

V2 deliberately retains separate screenings, territorial premiere semantics, an internal directed graph, creative-fit evidence in option preservation, immutable strategy snapshots, structured constraints, decision-aware clarification, zero-call routine replans, and behavioral ablations. Those are reasoning capabilities, not decorative surface.

## 3. Final scope

### MUST

- Preserve the exact existing course endpoints and public Quick Strategy interaction.
- Persist multiple film projects per private demo workspace and one active campaign per film project.
- Persist typed campaign lifecycle events, current opportunity state, constraints/locks, screenings, and immutable strategy versions.
- Derive a premiere ledger deterministically from sourced assertions and confirmed screening evidence.
- Build a directed `compatible | incompatible | verify` graph over at most 12 current opportunities.
- Select a primary launch route globally with transparent option preservation diagnostics and no arbitrary lambda.
- Expose at most two alternative launches, a rejection promotion, a screened/post-premiere branch, verification gates, and post-premiere opportunities.
- Make structured human hard constraints and locks authoritative; surface conflicts rather than silently violating them.
- Replan incrementally with an explicit A/B/C invalidation classification and hash-proven evidence reuse.
- Demonstrate a typed rejection producing a new strategy version with zero chat and zero embedding attempts.
- Persist structured before/after diffs, trace, usage, policy versions, and the complete strategy input snapshot.
- Prioritize clarification by current decision impact without a routine model call.
- Run typed what-if commands on an isolated in-memory snapshot through the real reducer and planner, then discard.
- Show CompanyMemory's existing influence and provenance.
- Add deterministic state, planner, API, scenario, compatibility, and reuse tests plus behavioral ablations.
- Mount workspace routes into the existing FastAPI application and retain the current Vercel catch-all.

### HIGH VALUE

- Campaign creation from either a compact structured form or free text.
- One visually polished Campaign page that a grader can understand in roughly one minute.
- Strategy history with concise version selection and structured change explanations.
- A read-only corpus coverage audit that distinguishes missing entities from retrieval, scoring, and planning failures.
- A separately reviewed addition of Sitges and, only if justified by the audit, a few specialist anchors.
- Known-fee budget constraints, with unknown fees represented as verification rather than zero.
- A single bounded live integration run after deterministic evaluation passes.

### OPTIONAL

- Export one campaign and its strategy history as JSON.
- A second preservation-policy control beyond the three defined modes only if distributor feedback requires it.
- More than one active campaign per film for re-release or territory-specific planning.
- A natural-language event **preview** after typed commands are proven, never direct mutation.
- Calendar export for verified dates.

### DEFER / DO NOT BUILD

- Generic signup, login, password reset, roles, invitations, SSO, billing, or team administration.
- Supabase anonymous-user auth as the workspace identity layer.
- A shared writable demo workspace identified only by a UUID.
- A `FilmProfileVersion`, `StrategyRun`, `VerificationTask`, `Scenario`, `Submission`, festival-evidence-version, or company-feedback table.
- Saved scenario libraries or scenario apply semantics.
- A general workflow DAG, BPM engine, graph editor, or graph visualization.
- Acceptance probabilities, financial expected value, invented dates, or expected revenue.
- Runtime scraping, unsupervised rule updates, or broad corpus expansion.
- Autonomous submission, outreach, email, payment, or offer acceptance.
- A solver platform, background job system, or vector store for campaign state.
- New campaign-outcome feedback into global CompanyMemory until the core is complete.
- A routine LLM call to parse every event, choose clarifications, update state, plan, or write strategy prose.

## 4. Simplified final domain model

The aggregate boundary is one `Campaign`, backed by one `FilmProject` and owned by one capability-scoped `Workspace`. V2 supports many films per workspace but only one active campaign per film. Multi-territory or re-release campaigns for the same film are optional later.

```text
Workspace
  └── FilmProject (current sourced identity profile)
        └── Campaign (version, lifecycle, ledger, active strategy)
              ├── CampaignConstraint[*]
              ├── CampaignEvent[*]
              ├── CampaignOpportunity[*] ──> existing Festival
              │      └── verification_items[]
              ├── Screening[*] ────────────> optional Opportunity/Festival
              └── StrategyVersion[*]
```

Core typed values:

- `Fact<T>`: `{value, status, source_refs, observed_at}`, where status is `confirmed | asserted | inferred | unknown | contradicted`.
- campaign lifecycle: `draft | active | post_premiere | closed`.
- readiness, derived rather than manually edited: `needs_identity | needs_premiere_clarification | ready | stale`.
- opportunity submission state: `not_submitted | submitted | rejected | invited | withdrawn`.
- offer state: `none | pending | accepted | declined`.
- policy state: `normal | locked | excluded`.
- screening state: `scheduled | occurred | cancelled`.
- screening access: `public | private | unknown`.
- premiere ledger value by scope: `available | consumed | unknown`; contradictions are a separate blocking defect attached to the affected scope.
- compatibility edge: `compatible | incompatible | verify`.
- preservation mode: `balanced | strict | opportunistic`, with `balanced` as the default.

The distinction among fact, constraint, event, recommendation, and evidence is strict:

- a fact describes the film or external world and carries epistemic status;
- a constraint is a human policy and may be hard/locked;
- an event records an observed lifecycle transition;
- a recommendation belongs only to a strategy version;
- evidence supports a fact, score, rule, or transition and never becomes a command by itself.

### 4.1 Frozen legacy-to-campaign contract boundary

The only object accepted by `CampaignPlanner` is a validated, immutable `PlanningInput`. Existing modules may continue returning dictionaries internally on the legacy path, but campaign code may not consume those dictionaries directly. A single `LegacyEvidenceAdapter`, implemented by the Phase 2 integration owner against Phase 0 schemas, converts legacy outputs into the following boundary models:

| Boundary model | Producer -> consumer | Essential fields and rules |
|---|---|---|
| `CampaignProfile` | FilmAnalyzer/profile-command adapter -> retrieval, risk, snapshot | Typed `Fact<T>` identity/eligibility fields, premiere assertions, `profile_hash`. A strategy freezes it. It may contain LLM-derived facts only with status/source; critical validation and contradiction rules apply. |
| `RetrievalInput` | LegacyEvidenceAdapter -> FestivalSearch adapter | `profile_hash`, validated semantic query, format/country/themes, retrieval policy/model, as-of date, `retrieval_key`. The query may originate from FilmAnalyzer; length, required fields, and critical facts are validated. |
| `RetrievedFestivalEvidence` | FestivalSearch adapter -> candidate-evidence assembler | Canonical `festival_id`, identity/facts snapshot and hash, rank/scores, retrieval sources/backend, source confidence, rule/date/fee facts. Immutable within a strategy; no chat judgment. Unknown or unparseable fees remain typed unknown facts. |
| `CandidateCreativeEvidence` | MatchScorer adapter plus CompanyMemory -> candidate-evidence assembler | Canonical `festival_id`, raw and guarded semantic ratings, per-dimension evidence, guardrail adjustments, deterministic company-relationship facts/rating, prompt/model/guardrail versions, `creative_key`. It may contain LLM ratings, but only after schema, evidence, range, known-ID, and guardrail validation. |
| `CandidateRiskEvidence` | RiskChecker adapter -> candidate-evidence assembler/graph | Canonical `festival_id`, eligibility, premiere constraint/risk, deadline/runtime assessment, uncertainties, as-of date, policy/input hash. No LLM values; immutable within one strategy and replaceable as one unit on B invalidation. |
| `FrozenCandidateEvidence` | Candidate-evidence assembler -> opportunity projection and PlanningInput | Exactly one retrieved, creative, and risk record for one canonical `festival_id`; deterministic score/breakdown, existing-policy `decision_grade`, `future_quality`, fee fact, component/policy hashes. A strategy freezes the complete object. Cross-component ID/hash mismatch is invalid. |
| `CampaignSnapshot` | Repository/state reducer -> orchestrator/scenario engine | Workspace-scoped campaign/version, `CampaignProfile`, ledger, screenings, constraints/locks, opportunity operational states, frozen candidate evidence, active strategy reference, aggregate hash. LLM-derived values may appear only inside typed profile/creative evidence. |
| `CompatibilityEdge` | CompatibilityBuilder -> PlanningInput | Canonical `from_festival_id`, `to_festival_id`, `compatible | incompatible | verify`, scope, reason code, evidence refs, graph-policy version, edge hash. Fully deterministic and immutable. |
| `PlanningInput` | CampaignOrchestrator -> CampaignPlanner, ClarificationEngine, StrategyValidator | Campaign/version, profile/ledger hashes, tuple of `FrozenCandidateEvidence`, complete directed edge set, opportunity states, constraints/locks, preservation mode, typed budget constraint and required-fee facts, as-of date, all policy versions, `planning_input_hash`. This is the exact and only planner input; it is deeply immutable. |
| `CampaignPlan` | CampaignPlanner -> validator, renderer, persistence/API | Fixed external plan schema, selected/alternative IDs, branches, gates, budget assessment, option preservation diagnostics, policy mode, reason/evidence refs, plan hash. No newly generated LLM values; validator must pass before persistence. |
| `ReuseManifest` | CampaignOrchestrator -> strategy version/trace | Invalidation class, prior/current artifact keys, reused/rerun artifacts, reasons, provider-attempt counts. Deterministic; all referenced hashes must exist in the strategy snapshot. |
| `StrategyDiff` | Deterministic diff engine -> strategy version/API/UI | Base/new campaign and strategy versions, changed/unchanged route IDs, gates, budget/preservation changes, causal event/evidence refs, reuse summary, diff hash. No prose comparison is authoritative. |

All models are versioned Pydantic contracts with unknown fields forbidden. Every boundary object embedded in a `StrategyVersion` is immutable in full; a later fact or score creates a new strategy input rather than mutating history. Decision hashes use canonical serialization (sorted keys, canonical festival IDs, UTC timestamps, and no volatile display prose). Phase 0 freezes their JSON fixtures and producer/consumer protocols; Phase 1 branches build only against those fixtures and may not import or accept raw `app.agent.modules` output dictionaries.

`festivals.id` is the sole canonical festival identifier throughout the chain. FestivalSearch must deduplicate to and emit that exact ID; MatchScorer and RiskChecker adapters must return the same ID; `CampaignOpportunity.id` is a separate persistence ID and never substitutes for it; every graph edge and plan reference uses `festival_id`. Display names and aliases are evidence only. The adapter rejects an unknown ID, duplicate canonical ID, name-derived replacement ID, or any ID mismatch across retrieval, creative, risk, opportunity, graph, and plan records.

## 5. Final persistence schema

V2 adds exactly eight tables. Existing `festivals`, `companies`, `company_festival_history`, and `agent_runs` remain intact; campaign code reads the first three and does not add outcome feedback in this iteration.

| New table | Purpose and essential fields | Important constraints |
|---|---|---|
| `workspaces` | `id`, `company_id`, `capability_digest`, `display_name`, `created_at`, `last_seen_at` | Unique capability digest; no email/account columns; browser roles cannot select rows |
| `film_projects` | `id`, `workspace_id`, title, `profile_json`, `profile_hash`, timestamps | Workspace FK; profile JSON uses typed fact envelopes; current projection only |
| `campaigns` | `id`, `film_project_id`, lifecycle, integer `version`, derived `readiness`, `premiere_ledger_json`, `ledger_hash`, `active_strategy_version_id`, `strategy_stale`, timestamps | One active campaign per film in v2; version increments once per accepted command |
| `campaign_constraints` | `id`, `campaign_id`, type, strength (`hard | preference`), payload JSON, `locked`, `active`, source, timestamps | Known types only; only a human command can unlock or deactivate a locked constraint |
| `campaign_events` | `id`, `campaign_id`, `sequence_no`, type, payload JSON, actor, `idempotency_key`, before/after aggregate hashes, created_at | Append-only; unique `(campaign_id, sequence_no)` and `(campaign_id, idempotency_key)` |
| `campaign_opportunities` | `id`, `campaign_id`, `festival_id`, submission/offer/policy states, `evidence_json`, `creative_scores_json`, `risk_json`, `verification_items_json`, evidence hashes, updated_at | Unique `(campaign_id, festival_id)`; one current operational projection per candidate |
| `screenings` | `id`, `campaign_id`, nullable opportunity/festival ID, venue/name, exhibition kind, country/region, scheduled/occurred time, screening state, access, source refs, current evidence status | Multiple rows per festival allowed; scheduled/cancelled/private rows do not consume premiere; corrections are event-driven |
| `strategy_versions` | `id`, `campaign_id`, monotonic `strategy_no`, `based_on_campaign_version`, `outcome`, `input_snapshot_json`, input hash, `plan_json`, `diff_json`, `trace_json`, `reuse_manifest_json`, usage JSON, policy/model versions, error JSON, created_at | Immutable; `outcome=ready | failed`; campaign pointer selects the active ready version; unique strategy number |

### 5.1 Why no profile-version table

The initial `campaign_created` event contains the starting profile; every profile change is a typed event; the current materialized profile lives on `film_projects`; and each immutable strategy row embeds the complete profile, constraint, ledger, opportunity-evidence, as-of-date, and policy snapshot used for that decision. This supports audit, deterministic reproduction, and version comparison without another join-heavy temporal table.

### 5.2 Why screenings stay separate

A festival can invite a film, schedule multiple showings, move or cancel one, hold a private industry screening, and later hold a public screening. A film can also have a public exhibition without a candidate festival opportunity. Collapsing these occurrences into one opportunity row would lose chronology and could incorrectly consume premiere rights. Submission state is singular for the v2 active cycle and can safely remain on the opportunity; screening occurrences cannot.

### 5.3 Transaction boundary

Supabase PostgREST is not a multi-statement transaction coordinator. A single SQL RPC, conceptually `apply_campaign_command`, must:

1. resolve and lock the campaign row;
2. verify workspace ownership, `expected_version`, command schema, transition preconditions, and idempotency;
3. append one event;
4. update the affected projections, screenings, constraints, and derived ledger;
5. increment the campaign version and set `strategy_stale=true`; and
6. return the authoritative aggregate snapshot.

Planning runs after that transaction. A second compare-and-set RPC inserts/activates a ready strategy only if the campaign is still at its input version. If planning fails, an immutable failed strategy attempt is stored, the prior active strategy remains inspectable, and the campaign stays visibly stale. Application-level rollback is not an acceptable substitute.

### 5.4 Minimal authorization

`POST /api/workspace/bootstrap` generates at least 256 bits with a cryptographically secure RNG and sends the base64url-encoded opaque capability only in a `Secure`, `HttpOnly`, `SameSite=Lax` cookie. The repository has no existing stable application secret whose natural purpose includes capability hashing, so v2 stores `SHA-256(raw_capability)` as the unique digest. The capability's entropy, not a secret hashing key, prevents guessing. This is the Phase 0 contract: do not introduce another server secret solely for HMAC.

The raw capability never appears in JSON, database plaintext, logs, traces, errors, or later response bodies; after bootstrap it exists only in the cookie. The cookie, not a workspace or campaign UUID, is authority. Every state-changing workspace route accepts JSON only and rejects a missing, `null`, or nonmatching `Origin` against a small configured allowlist of the deployed origin and explicit local-development origins. It resolves the capability digest first and scopes all repository operations to that workspace; `campaign_id` alone is never trusted. This requires no CSRF-token framework, refresh flow, recovery system, roles, or auth platform.

The campaign repository uses a server-only Supabase service credential and must fail closed if that credential is absent; it must not silently use the public anonymous key for writes. New tables have no public policies. Two-capability isolation and cross-origin mutation rejection are required deterministic API tests. This is intentionally a private demo session, not an account system; losing the cookie creates a new workspace, and recovery/identity management is deferred.

## 6. Final state-transition model

All durable mutations enter as one typed `CampaignCommand`, pass deterministic validation, and produce one typed event. UI controls and future natural-language previews may propose commands, but they never mutate state directly.

Representative commands and transitions:

| Command | Preconditions | Durable effect | Invalidation |
|---|---|---|---|
| `update_profile_fact` | Known field and valid fact envelope | Update film projection; append fact-change event | A |
| `set_constraint` / `remove_constraint` | Known constraint type; locked constraints require explicit unlock | Upsert/deactivate constraint | C, unless it changes identity/candidate expansion |
| `lock_opportunity` / `unlock_opportunity` | Known opportunity; only human actor may unlock | Update policy state | C |
| `exclude_opportunity` / `include_opportunity` | Known opportunity | Update policy state | C |
| `mark_submitted` | Opportunity is active and not terminal | `not_submitted -> submitted` | C |
| `record_rejection` | `submitted` or explicitly imported external decision | `-> rejected` | C |
| `record_invitation` | Active opportunity; direct invitation is allowed with source | `-> invited`, offer pending | C |
| `accept_offer` / `decline_offer` | Invitation pending | Set offer state | C |
| `withdraw` | Submitted/invited/accepted and not screened | `-> withdrawn` | C |
| `schedule_screening` | Valid place/time or explicitly unknown values | Insert scheduled screening | C |
| `confirm_screening` | Screening exists; access and occurrence evidence supplied | `scheduled -> occurred`; rederive ledger | B |
| `cancel_screening` | Screening not already confirmed occurred, unless correction | `-> cancelled` | C |
| `verify_opportunity_fact` | Known verification item and supported result | Update item and evidence/rule/deadline projection | B |
| `correct_record` | References prior event/fact and supplies replacement evidence | Append correction; update projection; rederive affected state | A, B, or C by corrected fact |
| `close_campaign` | Explicit human action | lifecycle `-> closed` | C |

Invitation, acceptance, and scheduling never imply that a public screening occurred. Rejection never changes premiere state. Invalid transitions create no event or partial projection. Duplicate idempotency keys return the original result. A stale expected version returns HTTP 409 with the current version.

Constraints that merely filter or order the existing 12 candidates are C. A request such as “search a new region” expands the candidate universe and is classified A; it must not masquerade as a zero-call policy replan.

## 7. Premiere ledger rules

The ledger is a deterministic projection, not an LLM conclusion. It reuses the territorial semantics already present in `app/agent/domain.py` and makes the evidence trail durable.

### 7.1 Derivation rules

1. An empty screening table does **not** prove availability. Without a sourced “no prior public exhibition” assertion, world status remains `unknown`.
2. A confirmed, occurred, public exhibition consumes the world premiere. The earliest supported occurrence is recorded as the world-premiere event.
3. International premiere is consumed only by a confirmed public exhibition outside the film's confirmed origin country. Unknown film or screening country yields `unknown`, not consumed or available.
4. Continental and territorial availability are derived from confirmed location and the existing country/region normalization. A public exhibition consumes only scopes it can justify.
5. Scheduled, invited, offer-accepted, cancelled, private, press-only, market, and industry screenings do not consume premiere.
6. An occurred screening whose access is `unknown` creates an unresolved ledger dependency and `verify` compatibility; it is never silently treated as public or private.
7. A confirmed theatrical, broadcast, online, or other public exhibition may be represented as a screening/exhibition row with no festival ID. Its territorial effect follows supported evidence; ambiguous online scope remains `unknown`.
8. A sourced assertion that the film has never screened publicly can establish availability, but it is contradicted by any confirmed public occurrence.
9. Corrections append a new event and update the current screening/fact projection; they do not delete history. The ledger is then fully rederived.
10. Conflicting public/private, location, date, or “unscreened” evidence attaches a contradiction defect to affected scopes, changes their effective value to `unknown`, and blocks any plan that requires certainty until resolved.
11. CompanyMemory screenings concern other films and never consume the current film's premiere.
12. The ledger records source references, the derivation policy version, input hash, and exact reasons for every scope.

The graph consumes this ledger. It never upgrades `unknown` to `available`, and a human preference cannot override a confirmed consumed premiere or hard ineligibility.

## 8. Final agent architecture

V2 has two additive execution paths.

### 8.1 Existing Quick Strategy path — unchanged

```text
POST /api/execute
  Planner -> Executor
    -> FilmAnalyzer (LLM)
    -> CompanyMemory (retrieval)
    -> FestivalSearch (embedding + hybrid retrieval)
    -> RiskChecker (deterministic)
    -> MatchScorer (LLM judgment + deterministic guardrails/arithmetic)
    -> RoadmapBuilder (LLM)
  -> Replanner (existing deterministic validator)
```

Its normal three chat calls, one embedding attempt, trace ordering, response shape, fallbacks, and error behavior remain intact.

### 8.2 Campaign path

```text
CampaignOrchestrator
  initial / A only:
    FilmAnalyzer? -> CompanyMemory -> FestivalSearch -> MatchScorer
  always as required by invalidation:
    CampaignStateReducer
    -> PremiereLedger
    -> RiskChecker
    -> CompatibilityBuilder
    -> CampaignPlanner
    -> ClarificationEngine
    -> StrategyValidator
    -> deterministic CampaignRenderer + StrategyDiff
```

Responsibilities:

- `CampaignOrchestrator` selects required stages from the invalidation class and hashes; it is control code, not an “agent persona.”
- `CampaignStateReducer` is the only state-transition implementation and is used by both real and simulated commands.
- `PremiereLedger` derives scoped availability and defects from facts and screenings.
- the existing `RiskChecker` semantics are reused through a narrow adapter rather than forked.
- `CompatibilityBuilder` creates the tri-state directed graph with evidence references.
- `CampaignPlanner` applies constraints, Pareto elimination, preservation policy, and route construction deterministically.
- `ClarificationEngine` ranks decision-changing questions deterministically.
- `StrategyValidator` checks known IDs, locks, hard constraints, branches, edge states, evidence references, and no-fabrication invariants.
- `CampaignRenderer` produces concise structured prose without a RoadmapBuilder call.

For a free-text initial campaign, the normal target is two chat calls (FilmAnalyzer and MatchScorer) plus one embedding. A structured profile omits FilmAnalyzer. Campaign planning, rendering, diffs, clarification, B/C replans, and scenarios do not call an LLM. Existing CompanyMemory is loaded before retrieval/scoring, displayed in evidence, and bounded by the existing 15-point relationship dimension.

The campaign trace uses truthful stages and includes each provider attempt and each deterministic reuse decision. It must never label a cache miss as reuse or silently fall back to a full provider run during a command promised to be zero-call.

The `LegacyEvidenceAdapter` is the integration choke point: it is the only campaign component allowed to know the dictionary shapes returned by FilmAnalyzer, FestivalSearch, MatchScorer, or RiskChecker. Downstream modules receive the frozen contracts in Section 4.1. This boundary is fixed in Phase 0 so state and planner branches can be implemented independently without inventing translations during Phase 2.

## 9. Compatibility graph

The graph contains one node per current campaign opportunity, capped at the existing 12 candidates. An edge `A -> B` answers a precise question:

> If A becomes the film's first confirmed public screening under the current evidence, can B still be pursued as represented?

Every directed edge is one of:

- `compatible`: current confirmed evidence shows that screening at A does not destroy B's relevant premiere eligibility;
- `incompatible`: current confirmed evidence shows that it does; or
- `verify`: source ambiguity, territorial uncertainty, or unknown screening facts prevent a safe conclusion.

Each edge stores a reason code, affected scope, ledger/rule evidence references, source confidence, and graph-policy version. Direction matters: a domestic launch may preserve an international opportunity while the reverse may not. Unknown is not given partial credit as compatible.

The graph models **screening order**, not submission order. The product may recommend submitting to B before A's event date while still requiring B's public screening to follow A. Deadlines and preparation actions appear in the operational timeline, never as fabricated graph chronology.

Rejected, withdrawn, excluded, and hard-ineligible nodes are unavailable to the planner but remain visible in history. Verification edges become explicit gates. The full pairwise graph is small (`N <= 12`, at most 132 directed non-self edges) and is recomputed deterministically for B changes; no graph database or search service is needed.

## 10. Final campaign-planning algorithm

### 10.1 Inputs and hard filtering

The planner consumes exactly one deeply immutable `PlanningInput`, never repository rows or legacy dictionaries. It contains profile facts, ledger and candidate hashes, frozen guarded evidence, deterministic risk, the complete directed graph, company-memory evidence, human constraints/locks, operational states, budget/fee facts, as-of date, and policy versions.

It first removes routes that violate confirmed hard eligibility, rejection/withdrawal/exclusion state, exact hard budget constraints, or locked human constraints. A locked request that conflicts with confirmed evidence produces a visible `constraint_conflict`; it is never silently weakened. Deadline closure may move a festival to a later-cycle/post route rather than claiming permanent ineligibility.

### 10.2 Immediate utility and future-option quality

`immediate_utility(root)` is the existing guarded 0–100 candidate score after deterministic deadline and premiere-risk arithmetic. V2 does not add another tier bonus or double-count strategic value.

For preserving a future festival `j`, the planner calculates an enduring `future_quality(j)` from the existing score breakdown:

```text
future_quality(j) = 100 * (
    thematic_fit_points
  + genre_fit_points
  + lineup_similarity_points
  + company_relationship_points
  + strategic_value_points
) / 90
```

Deadline urgency (10 points) is excluded because it describes the current submission window, not the enduring value of keeping the festival possible. Premiere penalty is excluded because the graph separately represents the consequence being measured. The four semantic dimensions are LLM-derived but schema-validated, evidence-required, guardrailed, frozen in the strategy snapshot, and never recomputed during B/C replans. Company relationship is deterministic. Thus the planner is deterministic for a frozen snapshot without pretending creative fit is irrelevant.

For each feasible root, the preservation universe contains every other nonterminal opportunity that is not permanently ineligible for non-premiere reasons. The edge partitions its total future-quality weight into:

```text
known_preserved_pct = compatible quality / total quality * 100
possible_additional_pct = verify quality / total quality * 100
known_destroyed_pct = incompatible quality / total quality * 100
```

If the universe is empty, preservation is defined as 100%, possible additional as 0%, and destroyed as 0%. The UI shows the interval `[known_preserved, known_preserved + possible_additional]`, plus named preserved, destroyed, and verify lists. These are quality-weighted option preservation diagnostics, not probabilities or financial values.

### 10.3 Hard budget and unknown-fee semantics

A fee fact is `{amount, currency, status, source_ref, observed_at}`. Only an exact supported amount and currency is known; blanks, ranges, text such as “varies,” stale/ambiguous facts, and currencies that cannot be compared to the constraint remain unknown. V2 performs no exchange-rate inference.

For a root under evaluation, **required fees** are only fees for actions marked `required_now` before the next decision gate: normally the root submission and any explicitly locked or noncontingent concurrent submission the plan tells the user to make now. Fees for rejection-only alternatives, mutually exclusive screening branches, post-premiere opportunities, and hypothetical later actions are not summed until their branch becomes active. Every assessment lists its included action/fee IDs.

With a hard budget, the deterministic route state is:

- `KNOWN_INFEASIBLE`: known required cost already exceeds the limit, even before unknown fees; filter the route.
- `KNOWN_FEASIBLE`: every required fee is known, comparable, and the total is within the limit.
- `VERIFY`: known required cost is within the limit but at least one required fee is unknown or incomparable; keep only as a preliminary route behind a blocking fee-verification gate and never label it budget-feasible.

Unknown fees are never zero. A hard-budget `VERIFY` contributes a blocking gate to verification burden, so an otherwise identical `KNOWN_FEASIBLE` route ranks ahead. With a soft budget preference, no route is filtered: use the explicit secondary order `known within preference -> unknown -> known over preference` only after the preservation-mode tuple, and show known total plus unknown fee IDs. No unknown amount is estimated.

`PlanningInput`, `CampaignPlan`, verification gates, and the strategy diff carry the constraint, required-fee items, known total, unknown IDs, state, and source refs. `StrategyValidator` rejects an infeasible selected route, a `VERIFY` route described as confirmed feasible, an unknown fee represented as zero, a currency inference without evidence, or a fee total containing inactive mutually exclusive branches.

### 10.4 Exact selection policy

There is no lambda.

1. Apply hard constraints and locks.
2. Construct one root route for every feasible launch candidate using all outgoing graph edges.
3. Remove Pareto-dominated roots. Route X dominates Y when X is no worse on immediate utility and known preservation, no greater on verification burden, and strictly better on at least one of those dimensions. Verification burden is the lexicographic tuple `(blocking_gate_count, verify_edge_quality_pct, total_gate_count)`; hard-budget `VERIFY` contributes a blocking gate.
4. Select from the Pareto frontier according to the human-visible `preservation_mode`:

   - `strict`: lexicographically maximize known-preserved percentage, then minimize verification burden, then maximize immediate utility, then apply the soft-budget preference order and stable festival ID.
   - `opportunistic`: lexicographically maximize immediate utility, then known-preserved percentage, then minimize verification burden, then apply the soft-budget preference order and stable festival ID.
   - `balanced` (default): first retain frontier roots in the best available existing immediate-decision grade: `submit_first`, then `prioritize_next/leverage`, while `hold_avoid` is not a current launch root. Within that grade, assign dense descending ranks on immediate utility (`rU`) and known preservation (`rP`) and minimize `(max(rU, rP), rU + rP, rP, verification_burden, soft_budget_preference_rank, stable_festival_id)`. Thus 90 versus 82 can be decided by preservation when both are launch-ready, while a 55-point merely viable route cannot displace a 94-point launch-ready route in balanced mode. When the trade-off is otherwise symmetric, preservation wins because premiere destruction is irreversible.

5. Select at most two remaining frontier roots as alternatives using the same ordering. A rejection branch promotes the first still-valid alternative.
6. List compatible post-premiere opportunities by future quality and verified deadline actionability. Keep `verify` opportunities behind named gates. Do not assert an event order that dates do not support.

A hard `preserve_world_premiere` or territory-preservation constraint filters roots before Pareto analysis. Evidence determines what is possible. The planner exposes the strategic tradeoff frontier. Human policy determines how aggressively to preserve premiere options. There is no universally optimal launch strategy after hard constraints and Pareto elimination: `balanced`, `strict`, and `opportunistic` are explicit distributor policy modes, not claims about an objectively correct utility function. The stable ID tie-break guarantees reproducibility.

### 10.5 Why this is defensible

- It evaluates each launch against all downstream opportunities, so it is materially different from independent ranking.
- Pareto elimination prevents selection of an obviously worse route.
- The default compromise is ordinal within an existing decision grade, so it can recognize preservation without sacrificing a clearly superior class of immediate opportunity.
- Irreversibility appears only as a documented tie-break and explicit hard constraint, not a hidden penalty.
- Creative relevance and CompanyMemory influence preservation transparently through already-audited score components.
- Unknown compatibility is visible as verification burden and possible preservation, never converted to certainty.
- The result is deterministic, small enough to inspect, and trivial to reproduce over 12 nodes.

### 10.6 Required planner archetypes, properties, and ablations

The deterministic suite must include multiple cases capable of falsifying the policy:

| Archetype | Fixture and required result |
|---|---|
| **A — option destruction matters** | A roughly 90-point launch destroys substantially stronger downstream opportunities; a roughly 82-point launch-ready root preserves them. Naive/opportunistic choose 90; balanced and strict can choose 82 under the frozen edge set. |
| **B — immediate value clearly dominates** | A 94-point launch-ready root competes with a preserving alternative near 55 in a lower decision grade. Balanced and opportunistic choose 94; strict may choose 55 only because that human policy explicitly prioritizes preservation. |
| **C — verification burden matters** | Two otherwise equal Pareto roots differ in `verify` edges/gates. The route with lower documented verification burden wins. |
| **D — hard human preservation** | The highest score violates locked `preserve_world_premiere`; it is filtered in every mode, with the constraint cited. |
| **E — no strategic tradeoff** | Preservation sets and verification burden are equal. Option-aware ordering matches immediate ordering and does not manufacture a difference. |

Planner invariants are:

1. identical preservation and verification outcomes imply higher immediate utility ranks first;
2. absent destructive compatibility differences, option preservation cannot reverse immediate ranking without an explicit human constraint;
3. a Pareto-dominated root is never selected;
4. an unknown edge is never treated as compatible for selection;
5. changing preservation mode changes selection only through the documented ordinal policy, never hidden weights; and
6. the same frozen `PlanningInput` always produces the same `CampaignPlan` and plan hash.

At minimum compare:

1. naive `highest immediate_utility` versus balanced option preservation;
2. balanced versus strict versus opportunistic on the same graph;
3. compatibility graph enabled versus all downstream options naively assumed compatible;
4. quality-weighted preservation versus unweighted option count;
5. full guarded creative/company future quality versus a deterministic-only subset;
6. CompanyMemory on versus off;
7. unknown edges kept as `verify` versus an intentionally unsafe “unknown means compatible” baseline;
8. hard-budget known-feasible versus known-infeasible versus unknown-fee routes; and
9. required-now fees versus an intentionally incorrect sum across mutually exclusive future branches.

Every case report must show the complete typed inputs, hard-filter results, budget states, Pareto frontier, decision grades, rank tuples, selected route, and preserved/destroyed/verify IDs rather than only PASS/FAIL.

## 11. Final contingent-strategy representation

The internal graph is not serialized as a general user workflow. The validated external plan has a fixed schema:

```json
{
  "schema_version": 2,
  "primary_launch": {
    "festival_id": "...",
    "submission_action": "...",
    "screening_gate": "...",
    "reason_refs": ["score:...", "edge:...", "constraint:..."]
  },
  "alternative_launches": [
    {"festival_id": "...", "activates_on": "primary_rejected_or_withdrawn"}
  ],
  "rejection_branch": {
    "of_festival_id": "...",
    "promote_festival_id": "..."
  },
  "screened_branch": {
    "at_festival_id": "...",
    "premiere_effect": {"world": "consumed", "international": "..."},
    "post_premiere_opportunity_ids": ["..."]
  },
  "verification_gates": [
    {"id": "...", "fact_key": "...", "affected_decision": "..."}
  ],
  "budget": {
    "state": "KNOWN_FEASIBLE",
    "known_total": {"amount": 0, "currency": "USD"},
    "unknown_fee_ids": [],
    "required_action_ids": []
  },
  "post_premiere_opportunities": ["..."],
  "option_preservation": {
    "mode": "balanced",
    "known_preserved_pct": 0,
    "possible_additional_pct": 0,
    "known_destroyed_pct": 0,
    "preserved_ids": [],
    "verify_ids": [],
    "destroyed_ids": []
  },
  "clarifications": [],
  "next_actions": []
}
```

Alternative launches are mutually exclusive premiere routes, not steps that all occur. The rejection branch may only reference a grounded existing alternative. The screened branch is conditional on a confirmed occurred public screening. `StrategyValidator` rejects unknown festival IDs, more than two alternatives, unsupported edges, fabricated dates/outcomes, missing evidence references, unsatisfied locks, an invalid budget assertion, and a post-premiere item whose edge is incompatible.

## 12. Final clarification architecture

Clarification uses three bounded layers and no routine LLM call.

1. **Static domain catalog.** It maps facts to dependent decisions and safe question templates. Premiere status, screening access/location, format, country, runtime, preserve policy, unknown required fees under a budget, and a small set of hard constraints receive explicit priorities. Composer and other nondependent profile completeness do not enter the campaign queue.
2. **Current-plan dependency check.** A missing or contradicted fact is promoted only if the active primary route, an alternative, a verification gate, hard eligibility, or a locked constraint depends on it.
3. **Limited deterministic sensitivity.** On cached evidence, the engine may clone the snapshot and test finite states for premiere availability and preservation mode. Format/country may be tested against the current candidates for eligibility/territory only, with a visible “candidate-set lower bound” because a real identity change would rerun retrieval.

Priority is deterministic:

```text
blocking hard decision
  > changes primary launch
  > changes an alternative or post route
  > changes only verification/order
  > useful but non-decision-changing
```

Within a class, rank by number/quality of affected route elements, contradiction before absence, static domain priority, and stable fact key. The Campaign page shows only the highest-priority question and the decision it affects; a compact queue can be expanded. If uncertainty affects only a gated later action, the system emits a safe preliminary plan rather than blocking the whole campaign.

The engine records tested states, before/after route IDs, dependency reasons, and why a question was suppressed. It does not call a model to estimate “value of information,” and it never asserts an answer.

## 13. Final replanning and evidence-reuse architecture

### 13.1 Three invalidation levels

| Level | Examples | Rerun | Reuse |
|---|---|---|---|
| **A — identity/candidate universe** | synopsis, themes, format, country, structured request for a new region; unstructured identity correction | FilmAnalyzer only if unstructured; CompanyMemory read; retrieval/embedding; creative scoring; deterministic score assembly; risk; graph; planner; clarification | Stable company-memory rows and unchanged corpus facts where hashes match |
| **B — domain/evidence state** | confirmed public screening, screening access/location correction, verified premiere rule, deadline or material eligibility fact | ledger; affected deterministic score assembly/risk; full compatibility graph; planner; clarification | Candidate retrieval and frozen creative ratings; CompanyMemory |
| **C — operational/policy** | submitted, rejected, invited, accepted/declined, withdrawal, lock, exclusion, filter-only constraint, preservation mode | state reducer; planner; clarification; diff | Retrieval, creative ratings, deterministic risk, ledger, and graph where hashes match |

Scheduling or cancelling a not-yet-occurred screening is C. Confirming that it occurred publicly is B. A constraint that requests candidates outside the current retrieved set is A, not C.

### 13.2 Artifact keys and trace proof

The implementation keeps the invalidation model small but validates reuse with hashes:

- `identity_hash`: semantic/eligibility profile fields and their fact statuses;
- `retrieval_key`: identity hash, corpus identity version, retrieval policy, embedding model;
- `creative_key`: identity hash, candidate identity hashes, scoring prompt/model/guardrail version;
- `risk_key`: relevant profile facts, ledger hash, current festival rule/deadline evidence, as-of date, risk policy;
- `graph_key`: ledger/risk/rule hashes, candidate set, graph policy;
- `plan_key`: graph key, frozen scores, opportunity states, constraints, preservation policy, planner version.

Each strategy trace contains an ordered `ReuseDecision` manifest with old/new keys, invalidation level, rerun modules, reused artifacts, and reasons. For a typed Hot Docs rejection with unchanged evidence, the required trace is conceptually:

```text
CampaignStateReducer  event=rejected, campaign_version N -> N+1
ReuseDecision         class=C; retrieval reused; creative scores reused;
                      risk reused; premiere ledger reused; graph reused
CampaignPlanner       rerun because opportunity availability changed
ClarificationEngine   rerun from new plan dependencies
StrategyDiff          primary/alternative/unchanged decisions
Usage                 chat_attempts=0, embedding_attempts=0
```

No provider fallback is allowed inside this promised zero-call command path. If a required cached artifact is absent or its key does not match, the result remains stale with an explicit `cache_miss_requires_refresh`; it does not secretly make a call and still claim reuse.

### 13.3 Strategy diffs

Every ready strategy after the first stores a structured diff containing:

- triggering event and campaign versions;
- primary route changed/unchanged and causal evidence;
- alternatives promoted, added, removed, or unchanged;
- post-premiere opportunities newly available, newly unavailable, gated, or unchanged;
- option preservation metrics before/after;
- constraints and locks that affected the result;
- reused versus rerun artifacts and provider-attempt counts.

The deterministic diff compares IDs and typed fields, not generated prose.

## 14. Final scenario architecture

The scenario engine is general in mechanism and deliberately small in product surface:

```text
typed hypothetical CampaignCommand(s), maximum 3
  -> validate against current base version
  -> deep-clone immutable CampaignSnapshot in memory
  -> apply the SAME CampaignStateReducer
  -> run the normal B/C invalidation path against cached evidence
  -> validate CampaignPlan
  -> compute structured comparison
  -> return mutated_campaign=false
  -> discard all temporary objects
```

There is no scenario table, name, save, apply, or history. To make a hypothetical real, the user deliberately performs the equivalent normal command against the current campaign and receives normal confirmation/version checks.

The initial UI exposes:

- “What if this festival rejects us?” (C);
- “What if the film screens publicly here?” (B); and
- optionally “What if preservation mode/this constraint changes?” (C).

The engine is not festival-specific. An A-class hypothetical that would require new retrieval returns `requires_provider_refresh` and may still show a clearly labeled current-candidate lower bound; it never invents a complete result. Tests assert that repositories receive no write calls, the campaign version and event count are unchanged, and the simulated result equals a real reducer/planner result from the same snapshot.

## 15. Human-in-the-loop boundary

Humans own facts they assert, irreversible actions, hard constraints, locks, and ambiguity resolution. The agent may recommend and compare, but it may not submit, withdraw, accept an offer, declare a screening public, remove a lock, or convert unknown evidence to fact.

The boundary is enforced as follows:

- typed commands identify `actor=human`; provider output cannot create them;
- hard constraints filter before optimization and cannot be outweighed by scores;
- locks persist across replans and scenarios until an explicit human unlock;
- a conflicting lock yields a visible conflict instead of a fabricated feasible route;
- verification gates name the fact, source, affected decision, and required human action;
- state-changing controls require confirmation and current `expected_version`;
- scenarios never apply automatically;
- natural-language input, if ever added, only previews typed commands for confirmation;
- every recommendation is inspectable with evidence, policy, and trace, while raw secrets and unnecessary personal data are excluded.

## 16. Final workspace API design

All workspace routes live in a new `APIRouter` imported and included by the existing `api/index.py` application. There is one Python Vercel function. The current catch-all rewrite remains unchanged.

### 16.1 Endpoints

| Method and route | Purpose |
|---|---|
| `GET /campaign` | Serve the compact campaign page from `public/campaign.html` |
| `POST /api/workspace/bootstrap` | Resolve a valid capability cookie or create one private demo workspace and set the cookie |
| `GET /api/workspace/campaigns` | List this workspace's film campaigns and active/stale versions |
| `POST /api/workspace/campaigns` | Create a film project/campaign from a structured profile or free text and produce its initial strategy |
| `GET /api/workspace/campaigns/{campaign_id}` | Return one authoritative aggregate with active plan, latest diff, constraints, events, visible CompanyMemory summary, and collapsed trace metadata |
| `POST /api/workspace/campaigns/{campaign_id}/commands` | Apply one typed command atomically, replan synchronously by A/B/C policy, and return aggregate plus strategy/diff status |
| `POST /api/workspace/campaigns/{campaign_id}/replan` | Retry planning for the current stale version without inventing another state event |
| `POST /api/workspace/campaigns/{campaign_id}/simulate` | Run one to three typed commands on an isolated snapshot and return comparison with `mutated_campaign=false` |
| `GET /api/workspace/campaigns/{campaign_id}/strategies/{strategy_no}` | Fetch one historical plan, input summary, diff, evidence, reuse manifest, usage, and trace on demand |

Clarification answers, locks, submissions, rejections, screening updates, and constraint changes all use the command endpoint rather than proliferating routes.

### 16.2 Command and error contracts

Mutation bodies include `type`, typed `payload`, `expected_version`, and a client-generated `idempotency_key`. Pydantic models forbid unknown fields and enforce small payload/command-count limits. New route errors use stable `{code, message, current_version?, details?}` bodies and appropriate 400/401/404/409/422 statuses.

The existing global request-validation handler must keep the exact four-field course error shape for `/api/execute`; workspace validation may branch by path to its richer contract. Provider secrets, cookies, service keys, raw vectors, and authorization headers never enter traces.

## 17. Final compact UI design

### 17.1 Quick Strategy

The current root retains the textarea, **Run Agent** action, response, and complete collapsible trace. It remains public and usable without creating a workspace. V2 may add one unobtrusive **Campaign Workspace** link; it does not make Quick Strategy depend on campaign persistence or silently rerun/save a prior quick result.

### 17.2 One Campaign page

The page is a single decision-focused layout, not a tabbed SaaS product:

```text
Film / Campaign                         version · lifecycle · ready/stale
----------------------------------------------------------------------------
Primary launch route                    Highest-priority clarification
Why selected                            Active constraints / locks
Immediate score + preservation band     Quick event actions
Up to 2 alternatives                    [reject] [invite] [screen] [lock]
Rejection promotion
Screened/post-premiere route
Verification gates
----------------------------------------------------------------------------
What-if: reject | screen here | change preservation mode
Before/after strategy diff
Recent events
Evidence, CompanyMemory and execution trace (collapsed)
```

The option panel shows the naive highest-scoring root beside the selected root when they differ, known/possible preservation, named destroyed options, current policy mode, and source-linked reasons. Budget wording is exact: **Budget confirmed: X of Y** only for `KNOWN_FEASIBLE`; **Budget verification required: X known, N required fees unknown — not confirmed feasible** for `VERIFY`; known-infeasible roots appear only in diagnostics as excluded. Soft preferences show known cost and unknown fee count without claiming feasibility. The event area displays only the most relevant typed actions. Historical versions use a compact selector; recent events are limited and older history expands on demand.

The server is authoritative. JavaScript does not derive premiere state, eligibility, score, graph edges, or a strategy. It sends expected versions, escapes all text, does not optimistically show irreversible actions as committed, distinguishes exact/projected/unknown dates, labels stale strategies, and keeps evidence/trace collapsed but complete. A narrow/mobile layout may stack the two columns; no frontend framework is required.

## 18. Evaluation and ablation plan

Keep the current 66-test baseline and behavioral runner separation among corpus presence, retrieval, scoring, and presentation. Campaign tests use deterministic fixtures and mocked provider outputs; one bounded live run is optional only after approval.

### 18.1 Hard invariants

**Persistence and transitions**

- save/reload yields the same aggregate, active strategy, hashes, and version;
- duplicate idempotency produces one event/version; stale expected version produces none;
- invalid transitions and cross-capability access create no partial state;
- RPC failure rolls back event and projections together;
- planning failure preserves the old active strategy and marks the campaign stale.

**Premiere and graph**

- no history remains unknown unless a sourced unscreened assertion exists;
- rejection, invitation, acceptance, scheduling, cancellation, and private screening do not consume premiere;
- only confirmed occurred public exhibition consumes justified scopes;
- domestic screening does not manufacture known international consumption;
- territorial edge direction matches existing domain semantics;
- contradiction produces `verify`/blocking clarification, not silent normalization;
- correction rederives the ledger while retaining event history.

**Planning and constraints**

- hard ineligibility and locked constraints cannot be scored away;
- Pareto-dominated roots are never selected;
- archetypes A–E produce their documented results, including 90-versus-82 preservation, 94-versus-55 immediate dominance, verification burden, hard preservation, and no-tradeoff collapse;
- identical preservation/verification ranks preserve immediate ordering, and a frozen `PlanningInput` is bit-for-bit deterministic;
- unknown edges contribute only to the possible band and gates;
- unknown required fees are never zero or confirmed feasible; known over-budget roots are filtered; mutually exclusive future fees are not summed;
- all output IDs/evidence refs exist, alternatives are at most two, and no dates/outcomes/probabilities are invented;
- submission timing never implies unsupported screening order.

**Replanning and scenarios**

- Hot Docs rejection creates a new ready strategy with zero chat and embedding attempts and a complete reuse manifest;
- confirmed public screening is B and reruns ledger/risk/graph/planning while reusing retrieval/creative evidence;
- identity change invalidates retrieval and creative scoring;
- diff correctly labels changed and unchanged decisions;
- scenario and real reducers produce equal hypothetical aggregates, while scenario storage remains unchanged.

**Clarification and memory**

- premiere contradiction outranks unrelated missing credits;
- a question suppressed by current-plan independence is traceable;
- answering a top premiere question changes a golden route; answering composer does not;
- CompanyMemory changes only its bounded score component and cannot rescue hard ineligibility or severe guardrailed mismatch.

**Compatibility boundary**

- `/api/execute` has exactly `status`, `error`, `response`, and `steps` on every path;
- the existing public root interaction works without workspace capability;
- course metadata/architecture endpoints still work;
- all existing tests remain green with the feature disabled.

### 18.2 Ablation matrix and reporting

Required paired runs include:

- naive ranking / balanced planner;
- balanced / strict / opportunistic;
- graph on / all-compatible assumption;
- quality-weighted / count-only preservation;
- creative-fit-included / deterministic-only future quality;
- CompanyMemory on / off;
- premiere unknown / available / consumed / contradicted;
- before / after confirmed public screening;
- rejection before / after;
- hard constraint absent / present;
- hard budget known-feasible / known-infeasible / verify and correct / incorrect branch fee scope;
- hybrid clarification / static catalog only;
- full rerun / A-B-C incremental execution.

Report typed inputs, root-cause layer, hard filters/budget states, route IDs, frontier, decision grades, rank tuples, preserved/destroyed/verify IDs, invariant defects, changed calls, latency, and reuse. Target metrics are zero hard-invariant violations, zero false-certainty claims, 100% zero-provider C events in eligible fixtures, stable unaffected recommendations, and explicit separation of corpus absence from retrieval miss.

## 19. Corpus strategy

Corpus coverage remains separate from retrieval and planning quality. The committed corpus has 355 festivals; Sitges is a confirmed missing entity, so its current behavioral failure is a data failure rather than a retrieval failure.

After core contracts stabilize, add a small read-only audit tool and reviewed manifest covering a few representative general, documentary, short, animation, experimental, genre, and regional anchors. For each target, report independently: entity present, structured fields present, identity provenance, broad top-K retrieval, candidate-cutoff survival, score suitability, and planner appearance.

Sitges may be the first proposed data-only addition after official-source review. Add only a few further specialist gaps if the audit produces concrete evidence. Every actual data change requires its own diff, provenance, data-quality/retrieval/planner tests, and separate approval before Supabase seeding or Pinecone embedding. V2 does not build a festival-evidence-version platform, runtime scraper, automatic update service, or broad expansion.

## 20. Cost and latency model

| Operation | Normal chat attempts | Embedding attempts | Expected behavior |
|---|---:|---:|---|
| Existing Quick Strategy | 3, plus today's bounded repairs | 1 when configured | Unchanged 260-second app / 300-second function ceilings |
| Initial campaign from free text | 2 (analyze + score), one bounded score repair | 1 | Deterministic planner/renderer replaces RoadmapBuilder |
| Initial campaign from structured profile | 1 (score), one bounded repair | 1 | FilmAnalyzer omitted |
| Structured A identity change | 1 scorer; analyzer only if input is unstructured | 1 | New retrieval and creative evidence |
| B domain/evidence change | 0 | 0 | Reuse retrieval/creative; rerun deterministic risk/graph/plan |
| C operational/policy change | 0 | 0 | Reuse retrieval/creative/risk/graph as hashes permit; rerun plan |
| Supported what-if scenario | 0 | 0 | In-memory B/C computation only |

The existing 12-candidate ceiling remains. Pairwise graph construction is at most 132 edges and should be negligible. Campaign aggregate reads should be server-assembled in one repository/RPC operation; historical trace JSON loads only on demand. No new vector index is used for company or campaign state.

Targets:

- deterministic C replan: under 2 seconds end to end under normal Supabase latency;
- B replan/scenario: under 5 seconds;
- provider-heavy campaign creation: remain materially below the existing 260-second application budget and benchmark before enablement;
- no background worker assumption on Vercel; timeouts leave visible failed/stale state and a retry action.

## 21. Reliability and failure modes

| Failure/risk | Required protection |
|---|---|
| Partial state after a command | One transactional SQL RPC for version check, event, projections, ledger, and version increment |
| Duplicate retry | Per-campaign idempotency key; return the original committed result |
| Two tabs/race | Optimistic campaign version; 409 with current version; no last-write-wins |
| Event committed but plan fails | Immutable failed attempt, prior active strategy retained, campaign visibly stale, explicit retry |
| Strategy computed from stale snapshot | Activation compare-and-set on `based_on_campaign_version` |
| Cross-workspace access | Opaque HttpOnly capability, digest lookup, workspace-scoped repository queries, two-token isolation tests |
| Capability or service-key leakage | Secure RNG; SHA-256 capability digest only; raw value cookie-only and redacted; service key server-only; no browser Supabase writes |
| Cross-origin workspace mutation | JSON-only mutations; exact configured Origin allowlist; reject missing/null/mismatched Origin before command processing |
| LLM malformed/hallucinated ratings | Existing schema validation, evidence requirements, guardrails, one bounded repair; known-ID validation |
| False zero-call claim | Hash-checked reuse manifest; cache miss fails visibly instead of silently calling providers |
| Contradictory screening evidence | Contradiction defect, affected ledger scopes unknown, verification/clarification gate |
| Stale rule or projected date | Confidence/as-of labels and verification items; no invented exact date |
| Unknown edge treated as safe | Separate `verify` state and possible-preservation band; validator rejects unsupported compatible claims |
| Unknown fee treated as free | Typed fee facts; hard-budget three-state assessment; blocking verification gate; validator forbids zero imputation or false feasibility |
| Locked choice overridden | Hard filtering and explicit conflict result; only human unlock command |
| Scenario mutation | Pure cloned snapshot, repository-free reducer interface, no-write assertions, `mutated_campaign=false` |
| UI/backend disagreement | Server aggregate authoritative; no client domain calculations; expected-version mutation |
| Vercel timeout | Prior strategy survives; failed trace stored; bounded retry; no durable-worker fiction |
| Corpus gap confused with retrieval failure | Coverage audit and behavioral root-cause layers |
| Migration regression | Additive migration, isolated rehearsal, no baseline table drops, feature flag, full legacy suite |

The planner must be deterministic for identical versioned inputs and as-of date. Date-sensitive hashes record the as-of date. Failed or degraded states must be visible; a fallback may never silently strengthen certainty.

## 22. Exact compatibility boundary protecting the current baseline

The following are nonnegotiable regression guards:

1. `GET /`, `GET /api/team_info`, `GET /api/agent_info`, `GET /api/model_architecture`, and `POST /api/execute` remain on the same FastAPI app.
2. `/api/execute` success and error responses retain exactly four top-level fields: `status`, `error`, `response`, and `steps`.
3. Quick Strategy retains the public textarea, Run Agent action, rendered response, and complete trace with no workspace/auth prerequisite.
4. The existing `graph.run` call order, normal three-chat/one-embedding policy, deterministic validators, fallbacks, public input limit, and best-effort run logging remain unchanged.
5. Existing `festivals`, `companies`, `company_festival_history`, and `agent_runs` schema/data are not repurposed or destructively migrated.
6. Campaign writes use a new repository and new tables; feature-disabled code has no effect on the legacy path.
7. Workspace routes are an `APIRouter` mounted by a minimal import/include change in `api/index.py`. There is no second Vercel function, and the catch-all rewrite remains unchanged.
8. Existing premiere/domain helpers are reused or narrowly extracted by the single integration owner; territorial behavior may not diverge between Quick Strategy and Campaign.
9. The campaign planner accepts only the frozen `PlanningInput`; one legacy adapter owns all dictionary translation and canonical-ID validation.
10. Architecture/module descriptions are updated only after integrated behavior is final, so metadata and traces cannot claim unimplemented modules.
11. The current legacy unit suite and exact endpoint contract tests must pass after every merge and in the final Vercel smoke test.

## 23. Implementation phases

This is a later implementation plan, not authorization to begin it.

### Phase 0 — Contracts and golden fixtures (serial)

Freeze the Section 4.1 adapter schemas/protocols, canonical festival-ID chain, Pydantic enums/models, command/event matrix, strategy schema, input/reuse hashes, capability contract, planner/budget policy versions, API fixtures, archetypes A–E, and clarification/replanning cases. Likely new ownership is limited to `app/campaign/models.py`, `app/campaign/contracts.py`, and new fixtures. Do not touch runtime paths yet.

### Phase 1 — Three isolated foundations (parallel, maximum three agents)

- **1A Persistence/state:** additive migration/RPC, repository, state reducer, premiere ledger, authorization helper, state/persistence tests.
- **1B Planner:** compatibility, exact Pareto/rank planner, clarification, validation, pure planner tests.
- **1C Evaluation fixtures:** new campaign fixtures/worker scaffolding only; no production or shared existing files.

All consume Phase 0 contracts. No Phase 1 branch may accept raw legacy dictionaries or invent a candidate/edge/plan shape; fixtures instantiate the frozen models. The branches do not edit one another's files.

### Phase 2 — Campaign integration (serial, one owner)

Merge 1A then 1B then 1C with the legacy suite after each. One integration owner builds orchestration, A/B/C invalidation, reuse hashes, deterministic rendering/diff, scenario cloning, and narrow adapters to existing `app/agent/**`. This is the only phase allowed to modify shared existing agent/domain files.

### Phase 3 — Product boundary (parallel, maximum three agents)

After the aggregate and route fixtures freeze:

- **3A API:** `api/campaign_routes.py`, sole ownership of `api/index.py`, workspace/auth/API tests.
- **3B UI:** `public/campaign.html` and campaign-specific static assets plus browser/static tests; build against frozen fixtures.
- **3C Evaluation/corpus tooling:** behavioral ablations and read-only coverage audit/manifest; no corpus mutation, provider seeding, diagram, or README claims.

### Phase 4 — Final integration, evaluation, and release gate (serial, one owner)

Merge API, then UI, then evaluation tooling; run legacy and campaign suites after each. Run deterministic ablations, accessibility/browser checks, local and isolated Vercel/Supabase smoke tests, then one approved bounded live provider run. Only now update architecture metadata, diagram, README, and recorded report from the exact integrated commit. Any Sitges/data change remains a separate approval and release.

## 24. Parallel-agent and worktree plan

Use an integration branch such as `codex/campaign-workspace-v2` and separate sibling worktrees. Never exceed three concurrent coding agents.

| Stage | Agent/worktree | Exclusive ownership | Explicitly forbidden overlap |
|---|---|---|---|
| Phase 0 | Contract owner | `app/campaign/models.py`, `contracts.py`, adapter protocols, API/archetype fixtures | No downstream production work until merged |
| Phase 1A | `codex/campaign-state` | migration/RPC, repository, `state.py`, `premiere.py`, auth helper, state tests | No planner, API, UI, existing `app/agent/**` |
| Phase 1B | `codex/campaign-planner` | `compatibility.py`, `planning.py`, `clarification.py`, `validation.py`, planner tests | No persistence, API, UI, existing agent files |
| Phase 1C | `codex/campaign-eval-fixtures` | new fixtures and isolated eval worker files | No production files or existing shared eval runner edits |
| Phase 2 | `codex/campaign-integration` | orchestrator, replanning, rendering/diff, scenarios, sole narrow edits to `app/agent/**` | No concurrent integration agent |
| Phase 3A | `codex/campaign-api` | `api/campaign_routes.py`, sole `api/index.py`, API tests | UI and eval agents may not edit API files |
| Phase 3B | `codex/campaign-ui` | campaign-specific `public/**`, browser tests | No API/backend files; avoid unrelated Quick Strategy rewrite |
| Phase 3C | `codex/campaign-evals` | evals/audit script/manifest only | No corpus rows, production modules, README, diagram |
| Phase 4 | Final owner | merge resolution, metadata, diagram, README, final reports | Serial only |

Merge order is contracts; state; planner; fixtures; integration; API; UI; eval tooling; final documentation. The owner runs all tests after every merge and resolves contract changes centrally rather than letting branches independently edit shared models. Migrations are append-only and have one owner. Each handoff includes baseline and commit, files changed/not touched, tests and exact output, contract deviations, provider/migration actions not performed, and unresolved risks.

No agent may seed Supabase/Pinecone, deploy, commit secrets, reformat unrelated files, regenerate final claims from a partial branch, or create another serverless entry point.

## 25. Acceptance criteria for every phase

### Phase 0 gate

- Every MUST lifecycle action maps to one typed command/event and invalidation class.
- Eight-table schema, capability flow, external plan, hashes, and A/B/C matrix are reviewed and frozen.
- Every Section 4.1 boundary model has a frozen JSON fixture, producer/consumer protocol, immutability/provenance/LLM rules, canonical hash, and unknown-field rejection.
- The canonical `festivals.id` chain is tested from retrieval through creative/risk evidence, opportunity, edge, and plan; duplicate, unknown, name-derived, and mismatched IDs fail validation.
- Phase 1 planner/state fixtures use only typed contracts and import no raw legacy module output.
- Archetypes A–E and the six planner invariants have frozen expected frontier/grade/rank/selection outputs, including the 90-versus-82 and 94-versus-55 cases.
- Hard/soft budget inputs, required-now fee scope, three hard-budget states, verification wording, and unknown-fee invariants are frozen.
- API request/response fixtures and no-fabrication invariants exist.
- Current 66 tests pass; feature flag is off; runtime behavior is unchanged.

### Phase 1 gate

- 1A: transactional RPC passes idempotency, race, rollback, correction, save/reload, SHA-256 digest lookup, and two-capability isolation tests.
- 1A: screening rules match existing world/international/territorial semantics, including unknown and contradiction cases.
- 1B: all graph edges are deterministic/evidence-linked; archetypes A–E, all six planner invariants, three preservation modes, budget states, locks, and unknown-edge/fee fixtures pass with no provider calls.
- 1B: clarification suppresses unrelated missing facts and promotes decision-changing premiere facts.
- 1C: ablation fixtures identify expected root-cause layers and do not import unfinished production modules.
- Legacy suite remains green after each foundation merge.

### Phase 2 gate

- Initial campaign path produces a validated plan within the declared call ceiling.
- Hot Docs rejection produces a new ready strategy, structured diff, and exactly zero chat/embedding attempts.
- Confirmed public screening reruns ledger/risk/graph/plan, changes justified edges, and reuses retrieval/creative evidence.
- Identity changes rerun exactly the A path; missing/mismatched cache keys fail visibly rather than silently calling.
- Scenario output equals the real reducer/planner result from the same base and performs no durable write.
- Failed replan retains prior strategy and marks stale; legacy `/api/execute` is byte/shape compatible.

### Phase 3 gate

- API: every campaign route resolves workspace from the HttpOnly capability; two capabilities cannot read/mutate each other; missing/null/mismatched Origin and non-JSON mutations are rejected; 409/idempotency/error contracts pass.
- API: router is mounted in existing `api/index.py`; one Vercel function and catch-all remain.
- UI: grader can create/resume a campaign, identify primary/alternatives/options/constraints/clarification in one minute, record rejection, see diff/reuse, and run/discard a scenario.
- UI: Quick Strategy remains immediately usable; no domain computation occurs client-side; trace and uncertainty remain accessible.
- Evaluation/corpus: reports distinguish missing entity, retrieval miss, score issue, graph issue, and presentation issue; audit makes no data/provider change.
- All legacy, API, browser, and campaign tests pass together.

### Phase 4 gate

- All hard invariants pass with zero false-certainty and zero cross-workspace defects.
- Required ablations show measurable differences and include frontier/rank/reuse evidence.
- Provider-heavy latency remains below the current Vercel budget; B/C paths meet deterministic targets.
- Isolated deployment preserves all existing endpoints, root UI, trace, and catch-all behavior.
- Architecture image, `/api/agent_info`, README, and evaluation report describe only behavior in the exact integrated commit.
- No production migration, seed, embedding, or deployment occurs without separate human approval.

## 26. Features intentionally not being built and why

| Omitted feature | Why omission is correct now |
|---|---|
| Generic accounts and collaboration | High implementation/security surface, negligible agent-reasoning value for a single-course demo |
| `localStorage` workspace authority | Not authorization; capability cookie is almost as small and defensible |
| Supabase anonymous auth/RLS identity | Adds session/RLS plumbing without improving the intellectual core |
| Film-profile version table | Events plus current projection plus full strategy input snapshots preserve the required history |
| Separate strategy-run table | Duplicates trace/input/usage already owned by an immutable strategy attempt |
| Submission table | V2 has one active cycle per campaign/festival; current state plus events is sufficient |
| Verification-task table | Small bounded JSON list and resolution events are sufficient; no workflow product is needed |
| Saved/applyable scenarios | Counterfactual reasoning needs isolation and comparison, not scenario lifecycle management |
| Company-memory feedback loop | Existing relationship memory already supports the ablation; feedback risks cross-film data semantics |
| Festival evidence version platform | Too much data-governance surface; use current evidence snapshots and verification gates |
| General contingent DAG/UI graph | Internal graph reasoning does not require a workflow engine or visual graph |
| Acceptance probabilities/expected value | No calibrated outcome data; such numbers would be false precision |
| Deep sequence optimizer/MILP | Twelve-node launch-root evaluation captures the premiere decision without solver complexity |
| Natural-language event execution | Typed controls prove state/replanning more safely; a future parser may only preview commands |
| Runtime scraping and broad corpus expansion | Undermines provenance, latency, and root-cause clarity |
| Background jobs/notifications | Vercel request model and demo scope do not justify another operational subsystem |
| Autonomous submissions/payments/outreach | Irreversible external action belongs to humans and is outside the academic claim |
| Multi-campaign-per-film territory product | Useful operationally but not needed to prove the reasoning model |

## 27. Grading narrative: intellectual contributions to emphasize

The instructor-facing story should focus on four contributions.

### 27.1 Global reasoning over an irreversible decision

The system does not merely sort festival scores. It constructs a directed tri-state graph and evaluates how each launch changes all downstream opportunities. The Pareto/rank ablation proves that the chosen route can differ from the naive top score for an inspectable reason.

### 27.2 Stateful, dependency-aware replanning

The agent observes lifecycle events, versions its strategy, and reuses evidence whose dependencies did not change. A live rejection produces a new route with zero provider calls and a trace proving why each artifact was reused. This is the clearest evidence that the project is not a stateless prompt wrapper.

### 27.3 Epistemically safe world modeling and clarification

The premiere ledger distinguishes available, consumed, unknown, and contradictory evidence across world, international, and territorial scopes. The system asks about premiere facts because counterfactual states change the current route, while suppressing irrelevant missing fields. It can make a gated preliminary plan without inventing certainty.

### 27.4 Human authority plus safe counterfactuals

Structured constraints and locks bind the planner, and conflicts are surfaced. The same deterministic reducer can run real commands or isolated what-if commands, letting a human inspect consequences without mutation. This demonstrates a precise human-agent boundary rather than autonomous theater.

CompanyMemory and the compact UI support these contributions: memory makes the strategy company-specific within a bounded score, while the UI makes state, causality, uncertainty, and trace legible. They should not displace the four core claims in the presentation.

## 28. Three-to-five-minute demo script

**0:00–0:35 — Establish the baseline.** On Quick Strategy, show the unchanged public prompt, response, and complete trace. Say that this remains the one-shot course agent; Campaign Workspace is additive.

**0:35–1:25 — Show global premiere-aware planning.** Open a prepared campaign. Point to version/state, the naive highest-scoring festival, the selected primary route, two alternatives, and the preservation diagnostic. Expand one destroyed edge and one `verify` gate. State the exact balanced policy: hard constraints, Pareto frontier, rank minimax—no probabilities or lambda.

**1:25–1:55 — Show decision-aware clarification and human control.** Display the top question about premiere/screening evidence and the decision it affects. Show an active preservation constraint/lock. Briefly note that an unrelated missing credit is not asked because it changes no route.

**1:55–2:50 — The central live event.** Click the typed **Rejected by Hot Docs** action and confirm it. Show campaign version increment, promoted alternative/new primary, structured before/after diff, unchanged decisions, and trace lines proving retrieval, creative scores, risk, ledger, and graph were reused. Highlight `chat_attempts=0` and `embedding_attempts=0`.

**2:50–3:40 — Safe counterfactual.** Run “What if the film screens publicly at Docaviv?” Show the cloned ledger effect, newly unavailable opportunities, verification changes, and hypothetical route. Discard it and point out that the real campaign version/event count did not change.

**3:40–4:20 — Evidence and ablation.** Expand the collapsed evidence/trace enough to show guarded creative ratings and visible CompanyMemory. Show the prepared ablation summary: naive 90-point launch versus balanced 82-point launch, graph on/off, CompanyMemory on/off, and zero-call incremental versus full rerun.

**4:20–4:40 — Close on the thesis.** “The agent maintains state, reasons globally about irreversible premiere choices, asks only decision-changing questions, and replans from real events without paying to rediscover unchanged evidence.”

The prepared campaign and deterministic ablation report protect the demonstration from provider latency. Only the rejection and scenario computations need to run live.

## 29. Final risk assessment

### Highest remaining risks

1. **Transactional correctness.** The RPC and activation compare-and-set are essential. If Postgres transaction/concurrency tests are weak, subtle event/projection divergence will undermine the whole persistent-agent claim.
2. **Planner policy validation.** Grade-bounded rank balance is defensible and exact, but a distributor should review its choices across archetypes and real campaigns. Cases A–E are designed to falsify over-preservation, under-preservation, uncertainty handling, and manufactured tradeoffs; the three visible modes remain explicit policy rather than hidden weights.
3. **Premiere evidence quality.** Current festival rules are sometimes shorthand or stale. The honest initial result may contain many `verify` edges. The UI and demo must present that as epistemic competence, not failure.
4. **Reuse-key correctness.** A false zero-call claim is worse than a slower honest run. Hash dependencies and fail-visible cache misses require first-class tests.
5. **Adapter drift and integration regression.** The frozen legacy boundary prevents ad-hoc dictionary translation, but adapter completeness, shared domain rules, the global FastAPI exception handler, static page serving, and metadata updates remain likely breakpoints. Canonical-ID and round-trip contract tests plus exclusive ownership mitigate them.
6. **Capability/service-role handling.** The minimal cookie design avoids an auth project but makes the server repository the security boundary. Secure generation, SHA-256 digest storage without a new secret, exact Origin checks, redaction, fail-closed configuration, and two-capability tests are mandatory.
7. **UI schedule.** Even one vanilla-JS campaign page can expand. The decision hierarchy and prepared demo path must be built before styling or historical conveniences.
8. **Provider latency and corpus gaps.** Initial planning still depends on existing providers and corpus coverage. The campaign ceiling, deterministic renderer, prepared demo, and separate audit contain this risk.

### Final answer to the scope question

**No. If this v2 scope is implemented cleanly, no major feature removed from the original ambitious design would materially reduce the project's academic ceiling.**

The removed features are primarily authentication, workflow, persistence, data-governance, and UI surface. V2 retains—and makes more testable—the academically important contributions: durable epistemic state, deterministic scoped premiere reasoning, global option preservation planning, human constraints, decision-aware clarification, incremental evidence reuse, immutable strategy diffs, isolated counterfactuals, and behavioral ablation. A natural-language event interpreter or saved-scenario product could improve polish later, but neither would raise the academic ceiling more than executing and proving this core well.

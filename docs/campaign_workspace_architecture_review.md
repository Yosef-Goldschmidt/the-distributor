# Campaign Workspace Architecture Review

Reviewer role: independent senior software architect and adversarial reviewer  
Reviewed document: `docs/campaign_workspace_design.md`  
Baseline inspected: `campaign-workspace` at `a9ebe1e` (identical to `reuven-final`)  
Repository verified: all claims against `app/agent/**`, `app/stores/**`, `api/index.py`, `scripts/schema.sql`, `tests/**`, `evals/**`, `public/index.html`, `vercel.json`, `app/config.py`  
Date: 2026-08-25

---

## Executive Verdict

**APPROVE WITH MAJOR REVISIONS**

The design document is the most technically literate architecture proposal I have seen for a student project. The author clearly understands the domain, the existing codebase, and the academic constraints. The core thesis — evolving a one-shot agent into a persistent, lifecycle-aware planner that reasons about irreversible premiere decisions — is the right major direction and would genuinely distinguish this project in an AI Agents course.

However, the document proposes approximately 2.5× more entities, 2× more persistence infrastructure, and 1.5× more features than are needed to capture 90% of the grading value. Several components smell like enterprise SaaS architecture rather than deliberate academic design. The anonymous auth model is under-validated. The contingent DAG representation is heavier than needed. And the 8-phase, 9-workstream parallel plan is an invitation to integration chaos.

The revisions recommended below would preserve every high-value AI reasoning capability while cutting roughly 40% of the implementation surface.

---

## What the design gets right

1. **Product thesis is sound.** The move from one-shot ranking to persistent campaign management with lifecycle events is not scope creep — it is the natural evolution that makes the agent *actually useful* for the domain. A distributor does not need a ranking once; they need ongoing decision support as rejections, invitations, and screenings arrive. This is genuine domain insight, not architecture for its own sake.

2. **Additive architecture seam.** The decision to preserve `graph.run(prompt)` and `/api/execute` unchanged while adding `app/campaign/` and `/api/workspace/...` endpoints is exactly right. The design document explicitly identifies the `api/index.py` four-field contract, the 66-test suite, the 17-scenario behavioral report, and the Vercel deployment as invariants. Verified: all these invariants exist and pass at the inspected baseline.

3. **Domain model captures real professional semantics.** The distinction between submission, invitation, acceptance, screening, and premiere consumption is not over-modeling — it reflects how film distribution actually works. The current `domain.py` already has `pre_target_compatibility` and `post_target_compatibility` (lines 660–742), confirming that directed premiere reasoning is a natural extension, not an invented abstraction.

4. **Deterministic boundaries are well-placed.** The design correctly insists that the planner, state machine, premiere ledger, clarification priority, and score arithmetic remain deterministic. This is the most important architectural decision in the document. An LLM that can modify campaign state or compute premiere eligibility would be academically indefensible and operationally dangerous.

5. **Incremental replanning with zero LLM calls.** The dependency registry (Section 10.2) that allows routine lifecycle events (rejection, invitation, screening) to produce a new strategy version with zero model calls is the single most academically distinctive capability. It demonstrates that the agent can reason about state changes without re-executing the entire pipeline. This is genuinely hard to get right and genuinely impressive when demonstrated.

6. **Evaluation strategy is sophisticated.** The proposed ablations (Section 15.3) — option value on/off, constraint present/absent, naive vs. planner, company memory on/off — would produce compelling evidence of component value. The existing behavioral evaluation framework (17 scenarios, root-cause classification by layer) is unusually strong for a student project and provides a solid foundation.

7. **Call budget discipline.** The commitment to maintain the 3-chat + 1-embedding initial budget and achieve 0-call routine replans is both economically sound and academically valuable. It forces the system to be genuinely intelligent about what requires re-computation.

8. **Explicit scope control.** Section 22's MUST/HIGH/OPTIONAL/DO NOT BUILD classification shows awareness of scope risk. The "DO NOT BUILD" list (acceptance probabilities, autonomous submissions, MILP, multi-agent swarm) correctly excludes the most dangerous scope expansion vectors.

---

## Critical architectural issues

### Issue 1: Anonymous authentication is under-validated and likely unnecessary

- **Severity:** HIGH
- **Section affected:** 5.4, 13, 14, 19
- **Why it matters:** The design proposes Supabase anonymous auth with per-user RLS as the ownership mechanism for campaign data. This requires: (a) Supabase anonymous auth being enabled on the course deployment, (b) the browser correctly managing anonymous JWT lifecycle, (c) RLS policies being correctly written for 15 new tables, (d) JWT refresh and expiry being handled across browser sessions, and (e) the fallback capability-token scheme being designed if anonymous auth is unavailable. None of these have been validated against the actual course Supabase instance.
- **Likely failure mode:** Anonymous auth is not enabled on the course Supabase. The team discovers this during Phase 4 and must either redesign the access model or build the capability-token fallback, consuming days of implementation time.
- **Recommendation:** **Defer authentication entirely.** For a single-user demo project, generate a random workspace UUID stored in `localStorage`. Use the existing Supabase service key (already in the Vercel environment) for all campaign table operations through server-side API routes. No RLS is needed because all campaign data flows through the API, not through direct browser-to-Supabase calls. This eliminates the entire anonymous auth concern, all RLS policy writing, and the security review of the capability-token fallback. The grading value of "anonymous persistence" is near zero — what matters is that campaigns persist and reload, not the mechanism.

### Issue 2: Fifteen new database tables is enterprise-grade overengineering

- **Severity:** HIGH
- **Section affected:** 5.1, 3.1
- **Why it matters:** The current baseline has 4 tables. The design proposes 15 new tables. Several of these (`festival_evidence_versions`, `company_festival_events`, `verification_tasks`, `strategy_runs` as separate from `strategy_versions`) create entities that will contain very few rows in any demo scenario and exist primarily to support future features the design explicitly defers.
- **Likely failure mode:** Coding agents write inconsistent foreign key relationships, miss transaction boundaries, or create subtle bugs in the projection logic across too many tables. Integration testing becomes expensive and debugging becomes difficult.
- **Recommendation:** Consolidate to 7–8 new tables. See the Domain Model Review section below.

### Issue 3: The contingent strategy DAG is over-specified for the available data

- **Severity:** MEDIUM
- **Section affected:** 9
- **Why it matters:** The DAG representation with typed nodes (action_set, outcome_gate, human_decision, verification_gate, route) and typed edges is a general-purpose workflow graph. But the actual content is always the same shape: submit → wait for outcome → accept/reject branch → post-premiere route. The corpus lacks notification dates, screening dates, and acceptance timelines, so the DAG cannot be calendar-optimized. It will always be a simple branching structure with 2–3 levels.
- **Likely failure mode:** The UI must render an arbitrary DAG, which requires either a graph visualization library or complex layout logic. The DAG validation (10 invariants listed in Section 9) becomes a testing burden. And the result looks no different from a simple ordered list with branch annotations.
- **Recommendation:** Replace the DAG with a flat strategy structure: one primary path (ordered list of festivals), up to 2 alternative launch paths (each an ordered list), and a post-premiere route (ordered list). Each entry has: festival_id, role (launch_target | alternative | post_premiere | backup), gate_type (none | rejection_of_X | verification_of_Y | human_acceptance), and status. This captures all the academic value (contingent planning, option preservation, verification gates) without the graph rendering and validation burden.

### Issue 4: Immutable versioning is applied too broadly

- **Severity:** MEDIUM
- **Section affected:** 3.1, 5.1
- **Why it matters:** `FilmProfileVersion`, `StrategyVersion`, `FestivalEvidenceVersion`, and the event journal create an immutability model that approaches event sourcing. The design disclaims event sourcing (Section 5.2: "This is a pragmatic event journal plus transactional projections, not a general event-sourcing platform") but the entity count and the explicit replay requirement ("Tests should be able to replay events to verify projections") are functionally event sourcing with extra steps.
- **Likely failure mode:** Agents implement either the event journal or the projections incorrectly, and the "replay produces same projections" invariant fails in subtle ways. Debugging these failures requires understanding the full event-to-projection pipeline across multiple tables.
- **Recommendation:** Keep immutable `StrategyVersion` (this is academically valuable — you can show before/after strategies). Drop `FilmProfileVersion` — store the current profile directly on the campaign and include a hash in the strategy version for provenance. Drop `FestivalEvidenceVersion` — this is deferred corpus work. Keep the event journal as a simple append-only log for auditability, but do not require replay-to-projection equivalence in the first iteration.

### Issue 5: The option-value formulation has arbitrary parameters that weaken academic defensibility

- **Severity:** MEDIUM
- **Section affected:** 8.4, 8.5
- **Why it matters:** `lambda_option = 0.25` is described as a "versioned initial" policy parameter, but the document does not provide any theoretical or empirical basis for this value. `strategic_weight` excludes deadline urgency and premiere penalty from the score used in option-value computation, which is a reasonable choice, but the interaction between the excluded components and the remaining components is not analyzed. The plan_value formula `immediate_launch_utility + lambda_option * 100 * known_preserved_ratio` linearly combines two quantities with different units and scales.
- **Likely failure mode:** The ablation shows that lambda_option=0 produces different results from lambda_option=0.25, but the reviewer (or grader) asks "why 0.25 and not 0.15 or 0.4?" and the answer is "we picked it." This undermines the academic credibility of the planner.
- **Recommendation:** Reframe option value as a penalty rather than a bonus. Instead of rewarding option preservation, penalize option destruction. For each path, compute `options_destroyed_ratio = 1 - known_preserved_ratio`. Apply a penalty: `plan_value = immediate_launch_utility - lambda_destruction * max_possible_utility * options_destroyed_ratio`. This is easier to explain ("we penalize paths that destroy more future options") and the penalty magnitude is bounded by a more interpretable quantity. Set `lambda_destruction = 0.2` and explain it as "a path that destroys all options loses up to 20% of the maximum possible score." Document that the value was chosen to be conservative and ablate it.

### Issue 6: Vercel configuration will likely break with new API routes

- **Severity:** HIGH
- **Section affected:** 13, Vercel deployment
- **Why it matters:** The current `vercel.json` routes everything through `api/index.py` with a single catch-all rewrite. Adding `api/campaign_routes.py` as a separate FastAPI router included in `api/index.py` should work, but adding it as a separate Vercel function would require `vercel.json` changes. The `includeFiles` pattern `{app/**,data/**,public/**,assets/**}` must also include any new `app/campaign/**` files. Since `app/**` is already included, this should be fine, but the `scripts/migrations/` directory is NOT included, which means migration files cannot be run from the Vercel deployment (this is correct behavior but should be explicit).
- **Likely failure mode:** A coding agent creates a separate `api/campaign_routes.py` Vercel function entry point instead of mounting it as a router in `api/index.py`. This requires a second `functions` entry in `vercel.json` and may break the catch-all rewrite.
- **Recommendation:** The design already says "e.g. `api/campaign_routes.py`" for the router, but must be explicit: campaign routes MUST be mounted as a FastAPI `APIRouter` included in `api/index.py`, NOT as a separate Vercel function. Add this as a contract constraint, not a suggestion.

---

## Overengineering audit

| Component | Value | Complexity | Risk | Recommendation |
|---|---|---|---|---|
| Persistent campaigns with versioned strategies | Very High | Medium | Low | **KEEP** |
| Deterministic premiere ledger | Very High | Medium | Low | **KEEP** |
| Tri-state compatibility graph | High | Medium | Medium | **KEEP** |
| Bounded beam search planner | High | Medium | Medium | **KEEP** (but simplify representation) |
| Option-value proxy | High | Low | Low | **KEEP** (reframe as penalty) |
| Incremental replanning with dependency registry | Very High | High | Medium | **KEEP** (simplify registry) |
| Decision-aware clarification | High | Medium | Low | **KEEP** |
| Scenario simulation (copy-on-write) | High | High | Medium | **SIMPLIFY** to 2-3 hardcoded what-if types |
| FilmProfileVersion immutability | Low | Medium | Medium | **REMOVE** — store current profile, hash for provenance |
| FestivalEvidenceVersion | Low | Medium | High | **DEFER** — this is corpus governance, not campaign intelligence |
| CompanyFestivalEvent (new outcomes) | Medium | Low | Low | **DEFER** — adds complexity to company memory with minimal demo value |
| Anonymous Supabase auth / RLS | Low | High | High | **REMOVE** — use server-side workspace ID |
| VerificationTask as a full entity | Medium | Medium | Medium | **SIMPLIFY** — inline as a field on opportunity |
| StrategyRun (separate from StrategyVersion) | Low | Medium | Medium | **MERGE** — strategy version includes its trace |
| Strategy DAG with typed nodes/edges | Medium | High | High | **SIMPLIFY** — flat ordered lists with gates |
| Event idempotency with keys | Medium | Medium | Low | **KEEP** — cheap insurance |
| Optimistic concurrency (version checks) | Medium | Low | Low | **KEEP** — cheap insurance |
| Natural-language event interpreter | Medium | Medium | Low | **DEFER** (design already says this) |
| Full workspace UI with tabs | Medium | High | Medium | **SIMPLIFY** — see UI Review |
| Scenario library (save/name/compare) | Low | Medium | Medium | **SIMPLIFY** — ephemeral comparison only |
| Submission/screening transition state machine | Medium | Medium | Medium | **SIMPLIFY** — reduce to 4 states |
| 8-phase implementation plan | N/A | High | High | **SIMPLIFY** — see Implementation Review |

---

## Academic grading-value audit

Ranked by likely contribution to final course grade:

| Rank | Capability | Grade Impact | Why |
|---|---|---|---|
| 1 | **Incremental replanning with zero LLM calls** | Very High | Demonstrates that the agent reasons about what changed and what can be reused. Zero-call replans are a dramatic cost optimization that proves genuine understanding of dependency structure. Easy to demo and evaluate. |
| 2 | **Campaign planner with option preservation** | Very High | Global reasoning over irreversible premiere choices is the core intellectual contribution. The ablation (naive vs. option-aware) produces clear measurable difference. No other student project is likely to do this. |
| 3 | **Persistent campaign state with lifecycle events** | Very High | Transforms the project from "stateless API wrapper" to "stateful agent that observes and adapts." This is the enabler for everything else. |
| 4 | **Deterministic premiere ledger** | High | Correct premiere tracking across screenings is both domain-authentic and technically non-trivial. The existing `domain.py` premiere logic (440 lines) proves the team already understands this deeply. |
| 5 | **Structured human constraints** | High | Establishes a clear human-agent authority boundary. Hard constraints that the planner must satisfy (or surface a conflict) demonstrate genuine human-in-the-loop design, not just "ask questions." |
| 6 | **Decision-aware clarification** | High | Asking only questions that change decisions — not dumping every unknown field — demonstrates intelligent information gathering. Easy to demo and evaluate. |
| 7 | **Scenario simulation** | High (if simple) | "What if Hot Docs rejects us?" is immediately compelling to any audience. But the grading value comes from the conceptual demonstration, not from a full scenario library. |
| 8 | **Comprehensive evaluation suite** | High | Ablations, counterfactuals, and call-count assertions prove the components work. This is what separates an A from a B+. |
| 9 | **Professional workspace UI** | Medium | Makes all the above visible and inspectable. But a complex dashboard does not improve the grade beyond a clean, functional interface. |
| 10 | **Company memory integration** | Medium | Already exists in the baseline. The new contribution (campaign outcomes feeding back) is incremental. |
| 11 | **Corpus coverage audit** | Medium | Good engineering hygiene but not an AI-agent contribution. |
| 12 | **Anonymous persistence** | Low | Operational plumbing. The grader does not care how sessions are managed. |
| 13 | **Natural-language event interpreter** | Low-Medium | Nice-to-have but the typed event API demonstrates the same concepts. |

---

## Domain-model review

### Proposed entities: assessment

| Entity | Verdict | Rationale |
|---|---|---|
| Workspace | **SIMPLIFY** | Needed but should be minimal: just an ID, created_at, and a reference to the company context. No `owner_id` (no auth). Store in `localStorage` browser-side, persist server-side as a row. |
| Company | **KEEP as-is** | Already exists. Reuse `companies` table. |
| FilmProject | **KEEP** | Needed to separate film identity from campaign lifecycle. Simple: id, workspace_id, title, current profile (JSON). |
| FilmProfileVersion | **REMOVE** | Store the current profile as a JSONB column on `film_projects`. Record the profile hash in each strategy version for provenance. |
| Campaign | **KEEP** | Core entity. Simplify: id, film_project_id, workspace_id, status, version, active_strategy_version_id, strategy_stale, profile_snapshot_hash, created/updated. |
| CampaignConstraint | **KEEP** | Essential for human-in-the-loop. |
| FestivalOpportunity | **KEEP** | Essential for tracking decision state per candidate. |
| Submission | **MERGE** into opportunity | For the demo, a submission state can be a field on `campaign_opportunities`. A full submission lifecycle table with cycle_key, fee tracking, and multiple submission versions per festival is premature. |
| Screening | **SIMPLIFY** | Keep as a separate table but simplify: id, campaign_id, festival_id, status (scheduled/occurred), public_status, country, occurred_at, source_event_id. Drop `superseded_by` — corrections are handled by appending a correction event. |
| CampaignEvent | **KEEP** | Essential for lifecycle tracking and replanning. This is the audit log. |
| PremiereLedger | **KEEP as derived** | Not a table — a computed projection from screenings. Store as a JSONB snapshot on the campaign for efficient reads. |
| VerificationTask | **SIMPLIFY** | Store as a JSONB array on `campaign_opportunities` or on the strategy version. A full table with priority, affected_decision_ids, source_url, result, etc. is premature for the demo. |
| StrategyVersion | **KEEP** | Core entity for demonstrating versioned planning. Merge the StrategyRun trace into it. |
| StrategyRun | **MERGE** into StrategyVersion | The trace, usage, and timing belong on the strategy version. A separate table adds a join and a foreign key without value. |
| Scenario | **SIMPLIFY** | Ephemeral only. Compute in memory, return the comparison, done. No persistence needed. If the user wants to save, store the comparison result in a JSONB column on the campaign. |
| CompanyFestivalEvent | **DEFER** | Campaign outcomes feeding back into company memory is a nice concept but adds complexity to the company memory read path. For the demo, company memory remains read-only from the imported aggregate. |
| FestivalEvidenceVersion | **DEFER** | This is corpus governance infrastructure, not campaign intelligence. The campaign planner works with the festivals table as-is. |

### Recommended simplified table set (7 new tables)

| Table | Purpose |
|---|---|
| `workspaces` | `id uuid PK`, `created_at`, `company_id text` |
| `film_projects` | `id uuid PK`, `workspace_id`, `title`, `profile jsonb`, `profile_hash text`, `created_at`, `updated_at` |
| `campaigns` | `id uuid PK`, `film_project_id`, `workspace_id`, `status text`, `readiness text`, `version bigint`, `active_strategy_version_id uuid`, `strategy_stale bool`, `premiere_ledger jsonb`, `created_at`, `updated_at` |
| `campaign_constraints` | `id uuid PK`, `campaign_id`, `kind text`, `value jsonb`, `strength text`, `locked bool`, `status text`, `source_event_id uuid`, `created_at` |
| `campaign_events` | `id uuid PK`, `campaign_id`, `sequence bigint`, `event_type text`, `payload jsonb`, `actor_type text`, `occurred_at timestamptz`, `recorded_at timestamptz`, `idempotency_key text`, `campaign_version_before bigint`, `campaign_version_after bigint` |
| `campaign_opportunities` | `id uuid PK`, `campaign_id`, `festival_id text`, `decision_status text`, `locked bool`, `submission_state text`, `screening_status text`, `screening_public_status text`, `screening_occurred_at date`, `screening_country text`, `evidence_snapshot jsonb`, `verification_notes jsonb`, `latest_strategy_version_id uuid`, `created_at`, `updated_at` |
| `strategy_versions` | `id uuid PK`, `campaign_id`, `version int`, `input_campaign_version bigint`, `profile_hash text`, `planner_policy_version text`, `input_hash text`, `plan jsonb`, `comparison jsonb`, `status text`, `trace jsonb`, `usage jsonb`, `created_at` |

This is 7 new tables instead of 15. The reduction eliminates: `film_profile_versions`, `submissions` (merged), `screenings` (merged into opportunities), `verification_tasks` (inlined), `strategy_runs` (merged), `scenarios` (ephemeral), `company_festival_events` (deferred), `festival_evidence_versions` (deferred).

---

## Campaign-planning review

### Compatibility graph

The tri-state compatibility graph (compatible / incompatible / verify) built from directed premiere relationships is genuinely well-designed. The existing `post_target_compatibility` and `pre_target_compatibility` functions in `domain.py` already implement the core edge computation logic. Extending this to a full pairwise graph over 12 candidates is straightforward (12² = 144 edges, negligible computation).

**Recommendation:** KEEP the graph. It is the intellectual foundation of the planner.

### Option preservation

The concept of "options destroyed by choosing a screening path" is the most original contribution. The distinction between `known_preserved` and `possible_additional` (verify-status edges) is particularly thoughtful — it avoids treating uncertainty as probability.

**Concern:** The `strategic_weight` used for option-value computation reuses the composite score minus deadline urgency and premiere penalty. This means a festival's "option value" is partially determined by its thematic fit score, which is LLM-generated and potentially noisy. A festival with a high LLM-rated thematic fit that happens to be premiere-compatible will appear more "valuable to preserve" than one with lower LLM ratings, even if the lower-rated festival has more objective strategic importance.

**Recommendation:** KEEP the option preservation concept. Consider simplifying `strategic_weight` to use only the deterministic components (company relationship, tier bonus, deadline urgency) to make the option-value computation fully deterministic and independent of LLM noise. This would make it easier to defend academically.

### Search algorithm

Beam search with depth 3 and width 5 over 12 candidates is appropriate. The search space is small enough that the algorithm is effectively exhaustive. Depth 3 is enough to show launch → post-premiere → downstream. Width 5 is generous for 12 candidates.

**Recommendation:** KEEP. Consider reducing to depth 2 and width 3 if implementation time is tight — the academic value is in the existence of the search, not in depth 3 vs. depth 2.

### Policy weights

`lambda_option = 0.25` is arbitrary but transparent. The design correctly identifies this as requiring ablation. 

**Recommendation:** As noted in Issue 5, reframe as a destruction penalty. The exact value matters less than the ablation proving it has measurable effect.

### Uncertainty handling

The refusal to invent acceptance probabilities or notification dates is the right decision. The `verify` edge status creating verification gates rather than numeric estimates is honest and defensible.

**Recommendation:** KEEP exactly as designed.

### Recommended algorithm

```python
# Simplified recommended algorithm
def plan_campaign(opportunities, constraints, premiere_ledger, lambda_destruction=0.2):
    # 1. Apply hard constraints and eligibility filters
    viable = apply_hard_constraints(opportunities, constraints)
    
    # 2. Build directed compatibility graph (12x12 = 144 edges max)
    graph = build_compatibility_graph(viable, premiere_ledger)
    
    # 3. Identify launch candidates
    launch_candidates = [o for o in viable 
                         if o.premiere_opportunity and o.eligible 
                         and o.deadline_status != 'closed']
    
    # 4. For each launch candidate, compute:
    #    - immediate utility (deterministic weighted score)
    #    - options destroyed (count and weight of incompatible downstream)
    #    - options preserved (count and weight of compatible downstream)
    #    - verification burden (count of 'verify' edges)
    
    # 5. Score: utility - lambda * destruction_ratio * max_utility
    # 6. Select primary path, up to 2 alternatives, post-premiere route
    # 7. Attach verification gates to 'verify' edges
    
    return StrategyPlan(primary, alternatives, post_premiere, verification_gates)
```

---

## State / persistence review

### Recommended minimum safe persistence architecture

1. **Server-side workspace management.** API creates a workspace UUID, returns it. Browser stores it in `localStorage`. API routes accept `workspace_id` as a header or parameter. No auth, no RLS, no JWT.

2. **Single transactional command path.** All mutations go through `POST /api/workspace/campaigns/{id}/commands` with `expected_version`. The server validates, appends an event, updates projections, increments version, and returns the new state. This is the only write path.

3. **Projections are the read model.** The campaigns, opportunities, constraints, and premiere ledger tables are always current. The event log is append-only and used for: (a) strategy version provenance, (b) replanning dependency analysis, and (c) audit trail. Events are NOT replayed at read time.

4. **Strategy versions are immutable.** Each planning run produces a new strategy version. The campaign points to the active one. Old versions are inspectable but not mutable.

5. **Supabase interaction.** Use the existing Supabase client pattern from `supabase_store.py`. All reads and writes go through server-side API routes. The browser never talks to Supabase directly. The service key (already in the Vercel environment) is sufficient.

6. **Failure semantics.** If Supabase is down, campaign mutations fail explicitly. The legacy `/api/execute` path continues to work with local fallbacks exactly as it does today. The workspace features degrade gracefully: "Campaign persistence is temporarily unavailable."

---

## Clarification review

### Simplest robust design with academic value

The clarification engine does NOT need sensitivity analysis across answer classes. That is impressive on paper but requires enumerating answer domains for every unknown fact and running the planner multiple times to detect decision changes. This is expensive to implement, hard to test, and fragile.

**Recommended simpler design:**

1. **Maintain a static critical-question catalog** mapping fact keys to their decision impact:
   - `premiere_status` → BLOCKING (affects launch target, compatibility graph, option preservation)
   - `premiere_history_completeness` → HIGH (affects premiere ledger accuracy)
   - `format` → HIGH (affects eligibility for many festivals)
   - `runtime_minutes` → USEFUL (affects descriptive warnings, not hard eligibility)
   - `country` → HIGH (affects territorial premiere compatibility)
   - `completion_status` → USEFUL (affects messaging, not eligibility)

2. **At clarification time:** check which catalog entries have `unknown` or `contradicted` status in the current profile. Sort by the static priority. Return the top N questions with their affected decisions.

3. **Academic value demonstration:** Show that the system asks about premiere status before asking about composer name. Show that answering a question can change the plan (counterfactual test). This is sufficient to demonstrate decision-aware clarification.

4. **Cost:** Zero LLM calls, zero computation beyond a lookup. Deterministic and testable.

---

## Replanning review

### Minimum dependency/invalidation system worth implementing

The full dependency registry in Section 10.2 has 11 rows mapping change types to recompute/reuse decisions. This is thorough but creates a testing matrix of at least 11 × (number of artifacts) cells.

**Recommended minimum:**

Classify changes into 3 categories:

| Category | Examples | Effect |
|---|---|---|
| **Identity change** | Synopsis, themes, format, country | Invalidate retrieval, scoring, and planning. Full re-execution minus company memory. |
| **Operational event** | Rejection, invitation, screening, lock, exclude | Invalidate planning only. Reuse all retrieval and scoring evidence. Zero LLM calls. |
| **Constraint change** | Add/remove constraint, change preference | Invalidate planning only. Reuse all retrieval and scoring evidence. Zero LLM calls. |

This 3-category model captures 90% of the value of the 11-row registry. The key insight is the same: operational events and constraint changes DO NOT require re-retrieval or re-scoring. Only identity changes (which are rare during an active campaign) require model calls.

**Implementation:** Store the profile hash and retrieval/scoring cache alongside the strategy version. On replan, compare the current profile hash. If unchanged, skip retrieval and scoring entirely. If changed, run the full pipeline.

**Academic demonstration:** Run a rejection event. Show that the new strategy version was produced with zero LLM calls. Show the trace that explicitly records "retrieval reused: profile hash unchanged" and "scoring reused: candidate evidence unchanged." This is the money shot for the grading demo.

---

## Scenario explorer review

### Recommended scope

The scenario explorer should be implemented as **2–3 hardcoded what-if operations**, not a general-purpose simulation engine.

**Must-have scenarios:**
1. "What if [festival] rejects us?" — removes the festival, replans.
2. "What if we screen publicly at [festival]?" — updates premiere ledger, replans.

**Nice-to-have:**
3. "What if we change [constraint]?" — applies the constraint change, replans.

**Implementation:** Each scenario is a function that: (a) clones the current campaign state in memory, (b) applies the hypothetical event, (c) runs the deterministic planner, (d) computes a structured diff (changed/unchanged decisions), (e) returns the comparison without persisting anything.

**NOT recommended:** A full copy-on-write simulation engine with save/discard/apply lifecycle, scenario naming, base-version checking, and stale-scenario detection. This is 3× the implementation cost for marginal grading value. The academic point is proved by computing the what-if and showing the diff, not by managing scenario lifecycle.

---

## UI review

### Minimum UI that makes the advanced architecture obvious to a grader

The UI should have exactly **3 views**, not a tabbed dashboard:

**View 1: Quick Strategy (existing, unchanged)**
- Textarea + "Run Agent" button → response + trace
- This is the course-required interface. Do not touch it.
- Add a small "Save as Campaign" button that creates a campaign from the last run result.

**View 2: Campaign Dashboard (single page)**
- Film title, status, version indicator, stale/fresh badge
- **Strategy summary:** Primary path (1–3 festivals with roles), alternatives (1–2), post-premiere route
- **Option value indicator:** "67% of downstream options preserved" with a single bar
- **Active constraints:** chips showing hard constraints and locks
- **Priority clarification:** 1–3 questions that would change decisions
- **Recent events:** last 5 campaign events with timestamps
- **Quick actions:** "Record rejection," "Record screening," "Add constraint" — each opens a simple form
- **What-if panel:** dropdown to select a scenario type, run it, see the diff inline

**View 3: Evidence & Trace (collapsed by default)**
- Full strategy version history with diffs
- Score breakdowns per festival
- Execution trace (same format as existing)
- Premiere ledger state

**NOT recommended:** Separate tabs for Opportunities, Activity, Scenarios, Evidence, Company. This is a SaaS dashboard. A grader will spend 2 minutes with the UI. They need to see: (1) the campaign persists, (2) events change the plan, (3) the agent explains what changed and why, (4) constraints work, (5) scenarios work. All of this fits on one page.

**Technical approach:** Keep the dependency-free static HTML approach. Split into `index.html` (quick strategy), `campaign.html` (campaign dashboard), and `shared.css` / `shared.js`. No framework, no build step, no bundler. The existing `public/index.html` is 288 lines and works perfectly. The campaign page should be similar in complexity.

---

## Corpus strategy review

### Recommended scope

**Do:** Build `scripts/audit_corpus.py` that reports coverage by festival type, geography, and specialization. This is useful engineering regardless of the campaign architecture.

**Do:** Add Sitges as a single reviewed entity. It is the only confirmed corpus gap from the behavioral evaluation. One festival addition is safe and closes the only true FAIL in the evaluation report.

**Do not:** Add a batch of festivals. The campaign planner's credibility does not depend on having 400 festivals instead of 355. It depends on the 12 candidates retrieved for a given film being reasonable, which the current 17-scenario evaluation already validates.

**Do not:** Build `festival_evidence_versions` or a controlled corpus update workflow. This is infrastructure for a production system, not a course project.

**Recommended investment:** ≤ 0.5 days, one person.

---

## Multi-agent implementation review

### Recommended implementation topology

The 9-workstream parallel plan is too many. Merge conflicts, contract drift, and integration debugging will consume more time than the parallelism saves. With "several days and strong AI coding agents," the optimal plan is:

**Maximum concurrent agents: 3**

**Phase 0 (serial, 0.5 day): Contract freeze**
- One agent defines `app/campaign/models.py` with Pydantic models, enums, and command types.
- Review and merge before anything else.

**Phase 1 (2 parallel agents, 1.5 days):**
- Agent A: Persistence + state engine (`repository.py`, `state.py`, `premiere.py`, migration, tests)
- Agent B: Planner + compatibility graph (`compatibility.py`, `planning.py`, `clarification.py`, tests)
- These share no files. Both consume models from Phase 0.

**Phase 2 (serial, 1 day): Integration**
- One agent connects everything: orchestrator, replanning, adapters for existing modules, scenario computation.
- This is the hardest part and must not be parallelized.

**Phase 3 (2 parallel agents, 1 day):**
- Agent A: API routes (`campaign_routes.py`, minimal `index.py` change, API tests)
- Agent B: UI (`campaign.html`, `shared.css`, `shared.js`)
- Agent A freezes the API response shapes before Agent B starts rendering.

**Phase 4 (serial, 0.5 day): Evaluation + polish**
- One agent runs ablations, extends behavioral evals, regenerates architecture diagram and README.

**Total:** ~4.5 days with 3 concurrent agents maximum. This is faster than the 8-phase plan because it eliminates integration overhead.

### Critical shared-file rules

- Only the Phase 2 integration agent may modify `app/agent/domain.py`, `modules.py`, `graph.py`, or `scoring.py`.
- Only the API agent may modify `api/index.py`.
- Only the UI agent may modify `public/**`.
- Migration files are numbered sequentially and owned by the persistence agent.
- `app/campaign/models.py` is frozen after Phase 0 and modified only through the integration agent.

---

## Simplified recommended architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Browser (no auth)                        │
│  ┌──────────────┐  ┌──────────────────────────────────────┐ │
│  │ Quick Strategy│  │ Campaign Dashboard                  │ │
│  │ (textarea +   │  │ strategy · events · constraints ·   │ │
│  │  Run Agent)   │  │ what-if · evidence/trace            │ │
│  └──────┬───────┘  └──────────────┬───────────────────────┘ │
│         │                         │                         │
│     localStorage              localStorage                  │
│     (none)                    (workspace_id)                 │
└─────────┼─────────────────────────┼─────────────────────────┘
          │                         │
          ▼                         ▼
┌─────────────────────────────────────────────────────────────┐
│              FastAPI (api/index.py)                         │
│                                                             │
│  POST /api/execute ─────────► graph.run() [unchanged]       │
│                                                             │
│  GET  /api/workspace/bootstrap                              │
│  POST /api/workspace/campaigns                              │
│  GET  /api/workspace/campaigns/{id}                         │
│  POST /api/workspace/campaigns/{id}/commands                │
│  POST /api/workspace/campaigns/{id}/plan                    │
│  POST /api/workspace/campaigns/{id}/scenarios               │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │            app/campaign/                             │    │
│  │                                                     │    │
│  │  models.py ── state.py ── premiere.py               │    │
│  │      │            │            │                    │    │
│  │  repository.py    │            │                    │    │
│  │      │            ▼            ▼                    │    │
│  │      │     orchestrator.py ◄── replanning.py        │    │
│  │      │            │                                 │    │
│  │      │     ┌──────┴──────────┐                      │    │
│  │      │     │  reuse/invalidate│                     │    │
│  │      │     │  cache manifest  │                     │    │
│  │      │     └──────┬──────────┘                      │    │
│  │      │            │                                 │    │
│  │      │     ┌──────▼──────────────────────────┐      │    │
│  │      │     │ app/agent/* (existing, adapted) │      │    │
│  │      │     │ FilmAnalyzer · FestivalSearch   │      │    │
│  │      │     │ RiskChecker · MatchScorer       │      │    │
│  │      │     │ domain.py · scoring.py          │      │    │
│  │      │     └─────────────────────────────────┘      │    │
│  │      │                                              │    │
│  │      │     compatibility.py ── planning.py          │    │
│  │      │            │               │                 │    │
│  │      │     clarification.py       │                 │    │
│  │      │                            │                 │    │
│  └──────┼────────────────────────────┼─────────────────┘    │
│         │                            │                      │
│         ▼                            ▼                      │
│  ┌─────────────┐            ┌──────────────┐                │
│  │  Supabase   │            │  Pinecone    │                │
│  │  (7 new +   │            │  (unchanged) │                │
│  │   4 exist)  │            │              │                │
│  └─────────────┘            └──────────────┘                │
└─────────────────────────────────────────────────────────────┘
```

---

## Recommended final scope

| Feature | Classification | Rationale |
|---|---|---|
| Preserve exact course endpoints and root quick-strategy UI | **MUST** | Non-negotiable course requirement |
| Persistent film projects and campaigns | **MUST** | Enables everything else |
| Campaign event log (append-only) | **MUST** | Audit trail and replanning input |
| Deterministic premiere ledger from screenings | **MUST** | Core domain correctness |
| Tri-state compatibility graph | **MUST** | Foundation for planner |
| Campaign planner with option preservation | **MUST** | Core academic contribution |
| Structured human constraints | **MUST** | Human-in-the-loop boundary |
| Incremental replanning with zero-call routine events | **MUST** | Highest grading impact feature |
| Strategy versioning with structured diffs | **MUST** | Proves replanning produces traceable results |
| Decision-aware clarification (static catalog) | **MUST** | Intelligent question selection |
| Evaluation suite: ablations + counterfactuals | **MUST** | Evidence that components add value |
| Campaign dashboard UI (single page) | **MUST** | Makes architecture visible to grader |
| Scenario: "what if rejected" and "what if screened" | **HIGH VALUE** | Compelling demo feature |
| Server-side workspace persistence (no auth) | **HIGH VALUE** | Simple, low-risk persistence |
| Optimistic concurrency (version checks) | **HIGH VALUE** | Cheap correctness guarantee |
| Corpus audit script | **HIGH VALUE** | Engineering hygiene |
| Add Sitges to corpus | **HIGH VALUE** | Closes only true evaluation FAIL |
| Scenario: "what if constraint changed" | **OPTIONAL** | Third what-if adds diminishing value |
| Natural-language event interpreter | **DEFER** | Typed events demonstrate the same concepts |
| Company memory feedback from campaigns | **DEFER** | Adds complexity to memory read path |
| Anonymous auth / RLS | **REMOVE** | Use server-side workspace ID instead |
| FilmProfileVersion immutability | **REMOVE** | Store current profile, hash for provenance |
| FestivalEvidenceVersion | **REMOVE** | Corpus governance, not campaign intelligence |
| Full scenario save/name/apply lifecycle | **REMOVE** | Ephemeral comparison is sufficient |
| StrategyRun as separate entity | **REMOVE** | Merge into StrategyVersion |
| Submission as separate entity | **REMOVE** | Merge state into opportunity |
| Strategy DAG with typed nodes/edges | **REMOVE** | Flat ordered lists with gates instead |
| Full tabbed workspace dashboard | **REMOVE** | Single-page campaign view is sufficient |

---

## Revised implementation sequence

### Phase 0: Contract freeze (0.5 day, serial)

**Goal:** Define all Pydantic models, enums, command types, and strategy plan structure. Freeze `app/campaign/models.py`.

**Files:** `app/campaign/__init__.py`, `app/campaign/models.py`, `app/config.py` (feature flag only)

**Gate:** Models pass JSON round-trip tests. Existing 66 tests pass. No runtime changes when flag is off.

---

### Phase 1A: Persistence + State Engine (1.5 days, parallel with 1B)

**Goal:** Create tables, repository, state machine, premiere ledger computation.

**Files:** `scripts/migrations/001_campaign_workspace.sql`, `app/campaign/repository.py`, `app/campaign/state.py`, `app/campaign/premiere.py`, `app/stores/campaign_store.py`

**Tests:** `tests/test_campaign_state.py`, `tests/test_campaign_persistence.py`

**Gate:** Save/reload, idempotency, concurrency, premiere invariants all pass.

---

### Phase 1B: Planner + Compatibility Graph (1.5 days, parallel with 1A)

**Goal:** Build compatibility graph, option-value computation, beam search, clarification catalog.

**Files:** `app/campaign/compatibility.py`, `app/campaign/planning.py`, `app/campaign/clarification.py`

**Tests:** `tests/test_campaign_planner.py`

**Gate:** Deterministic fixtures prove option preservation changes behavior. Lambda ablation produces measurable difference. No LLM calls.

---

### Phase 2: Integration (1 day, serial after 1A + 1B merge)

**Goal:** Connect campaign state to existing modules. Implement replanning with dependency-based cache reuse. Implement scenario computation.

**Files:** `app/campaign/orchestrator.py`, `app/campaign/replanning.py`, narrow adapters in `app/agent/modules.py`

**Tests:** `tests/test_campaign_replanning.py`, `tests/test_scenarios.py`

**Gate:** Rejection event produces new strategy with zero LLM calls. Screening event updates premiere ledger and replans. Scenario computation leaves real state unchanged. Legacy `/api/execute` unchanged.

---

### Phase 3A: API Routes (1 day, parallel with 3B)

**Goal:** Expose workspace endpoints through FastAPI router in `api/index.py`.

**Files:** `api/campaign_routes.py`, minimal `api/index.py` change (include router)

**Tests:** `tests/test_workspace_api.py`

**Gate:** All workspace operations work. Course endpoints unchanged. Version conflicts return 409.

---

### Phase 3B: Campaign UI (1 day, parallel with 3A after API contract freeze)

**Goal:** Build campaign dashboard page with strategy, events, constraints, what-if panel.

**Files:** `public/campaign.html`, `public/styles.css`, `public/app.js`

**Gate:** Grader can create campaign, see strategy, record event, see diff, run what-if. Quick strategy page unchanged.

---

### Phase 4: Evaluation + Polish (0.5 day, serial)

**Goal:** Run ablations, extend behavioral evals, add Sitges, update architecture diagram and README.

**Files:** `evals/`, `scripts/audit_corpus.py`, `scripts/make_architecture.py`, `assets/architecture.png`, `README.md`

**Gate:** All tests pass. Ablations show measurable value. Architecture diagram matches reality. README describes the full system.

---

## Five things most likely to go wrong

1. **Supabase transaction semantics through PostgREST.** The design assumes atomic multi-table transactions (event + projection + version increment). Supabase's PostgREST does not support multi-statement transactions natively. The team will need to use an RPC function (PL/pgSQL stored procedure) or accept that the "atomic command path" requires two separate requests with application-level rollback logic. This is the most likely source of subtle state corruption.

2. **Integration phase scope explosion.** Phase 2 (connecting the planner to real retrieval/scoring evidence and implementing replanning) is where the most complex interactions occur. If the planner expects data in one shape and the existing modules produce another, the adapter layer grows unbounded. **Mitigation:** Define exact adapter interfaces in Phase 0 models, not during integration.

3. **UI consuming more time than the backend.** Building a professional-looking campaign dashboard in vanilla HTML/CSS/JS without a framework is time-consuming. The design's recommended "split into maintainable static assets" is good advice, but even with components, rendering a strategy plan, event timeline, constraint editor, and what-if panel is a significant frontend project. **Mitigation:** Start with ugly-but-functional. Style last.

4. **Premiere ledger edge cases.** The premiere ledger derivation has at least 10 rules (listed in Section 4.3). Implementing all of them correctly, especially the interaction between territorial scopes, unknown completeness, and correction events, is error-prone. The existing `domain.py` premiere logic is already 300 lines. **Mitigation:** Implement the 3 most common cases first (world consumed, international consumed, no screening = unknown). Add territorial logic only if time permits.

5. **The "zero LLM calls" claim is harder to achieve than expected.** The design claims that a rejection event can produce a new strategy with zero calls. This requires: (a) the planner to be fully deterministic, (b) all scoring evidence to be cached and validated by hash, (c) the orchestrator to correctly skip retrieval and scoring modules, (d) the trace to record reuse decisions. If any of these are wrong, the system silently falls back to re-running the full pipeline, which contradicts the claimed call budget. **Mitigation:** Make the zero-call path the first test, not the last.

---

## Final recommendation to the project owner

**If this were my own academic project and I wanted the highest possible grade with several days and strong AI coding agents available, here is exactly what I would build:**

I would build a persistent campaign system that does three things brilliantly and proves each with ablation:

1. **Global premiere-aware planning.** A deterministic planner that chooses a launch path by considering which downstream options are destroyed by each choice. I would show, with a synthetic fixture, that the naive "pick the highest-scoring festival" approach selects a different (and demonstrably worse) launch target than the option-aware planner. I would display the option preservation ratio prominently in the UI and trace. This is the intellectual centerpiece.

2. **Zero-cost incremental replanning.** When the user records "Hot Docs rejected us," the system produces a new strategy version in under a second with zero LLM calls. The trace explicitly shows "retrieval evidence reused (profile hash unchanged), scoring evidence reused (candidate set unchanged), only planner re-executed." I would run this demo live for the grader. This proves the agent is not a stateless API wrapper — it genuinely maintains epistemic state and reasons about what changed.

3. **Decision-aware clarification.** When premiere status is unknown, the system asks about it because it blocks the launch target selection. When the composer's name is unknown, the system does not ask because it changes nothing. I would show the question priority list and demonstrate that answering the premiere question changes the plan while answering the composer question does not. This proves the agent asks questions for reasons, not because a field is empty.

Everything else — the UI, the persistence, the constraints, the scenarios — exists only to make these three capabilities visible and testable. I would spend 80% of my time on these three features and 20% on everything else combined.

I would NOT build anonymous auth, festival evidence versioning, a DAG rendering engine, a scenario persistence layer, or a natural-language event interpreter. Each of these adds implementation risk without adding to the core story: "This agent reasons about irreversible decisions, maintains state across time, and gets smarter with zero additional cost."

The strongest possible submission artifact is not the largest architecture. It is the one where a grader runs the demo, records one event, sees the strategy change with zero calls, asks "how does this work?", and the ablation suite answers with measurable evidence.

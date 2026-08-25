# Behavioral Evaluation Baseline

- Starting git commit: `1b6d8c0f0e8816ef253afb6d7ed20262b1388f1c`
- Branch at start: `reuven-final`
- Started at (UTC): `2026-08-25T12:10:32.383546+00:00`
- Production source: current working tree based on `1b6d8c0f0e8816ef253afb6d7ed20262b1388f1c`
- Existing test suite: **PASS** (66 tests)
- Paid live execution cap: `10` full `/api/execute` runs
- Live executions requested in this run: `0`
- Prior live artifact reused without a new provider call: `false`

## Overall

| Scenario | Status | Reason |
|---|---|---|
| Unknown premiere | **PASS** | The exact input stays unknown even when an adversarial FilmAnalyzer payload claims world-premiere availability; no target is selected and the missing fact is high-impact. |
| Already premiered | **PASS** | Explicit public-premiere evidence forces post-premiere behavior and removes strict world-premiere paths. |
| Contradictory critical facts | **PASS** | Critical contradictions are surfaced. |
| Hard format and runtime mismatch | **WARN** | Structured format incompatibility dominates as required. Runtime ranges are only descriptive text in the corpus, so the system correctly warns but cannot enforce them as hard rules without authoritative data. |
| Youth-audience over-inference | **PASS** | Youth specialists may remain retrieval candidates, but adversarial 5/5 creative ratings are capped without explicit youth-audience evidence; a genuine children's animation is not capped. |
| Genre specialist retrieval: Sitges | **FAIL** | The expected specialist is absent from the committed corpus; retrieval cannot recover a missing entity. |
| Religion documentary retrieval | **PASS** | The relevant specialist exists and survives into the 12-candidate scoring pool. |
| Authored documentary retrieval | **PASS** | The relevant specialist exists and survives into the 12-candidate scoring pool. |
| Children's animation retrieval | **PASS** | The relevant specialist exists and survives into the 12-candidate scoring pool. |
| User preference versus domain reality | **WARN** | A compatible LGBTQ preference materially changes retrieval, and deterministic eligibility still overrides an impossible prestige request. Preference preservation remains implicit in FilmAnalyzer.search_query rather than an explicit scored field. |
| No good candidates | **PASS** | The roadmap can return zero first-wave candidates and no premiere target. |
| Deadline edge cases | **PASS** | Exact, expired, month-only, stale and malformed deadline states preserve projection and confidence semantics. |
| CompanyMemory counterfactual | **PASS** | Company history changes only the relationship dimension and produces a bounded score delta without altering creative fit. |
| Pinecone and Supabase failure fallback | **PASS** | Provider failures degrade to explicitly labelled local sources; the embedding attempt remains in trace. |
| LLM retry, malformed JSON and timeout tracing | **PASS** | Every mocked LLM attempt is traced in order, including the rejected-parameter retry, malformed successful response and transport timeout. |
| API success, validation-error and runtime-error contract | **PASS** | All exercised API paths return exactly status/error/response/steps; runtime errors retain partial trace without a stack trace. |
| Cross-cutting invariants on prompt examples | **PASS** | Prompt examples preserve canonical modules, trace payloads, candidate uniqueness, score arithmetic and Executor counts. |

## True correctness failures

- **Genre specialist retrieval: Sitges** — The expected specialist is absent from the committed corpus; retrieval cannot recover a missing entity. Root cause: corpus/data problem

## Weaker-quality / UX issues

- **Hard format and runtime mismatch** — Structured format incompatibility dominates as required. Runtime ranges are only descriptive text in the corpus, so the system correctly warns but cannot enforce them as hard rules without authoritative data.
- **User preference versus domain reality** — A compatible LGBTQ preference materially changes retrieval, and deterministic eligibility still overrides an impossible prestige request. Preference preservation remains implicit in FilmAnalyzer.search_query rather than an explicit scored field.

## Root-cause classification for FAILs

### Genre specialist retrieval: Sitges

- `data_corpus`: FAIL
- `retrieval_recall`: NOT ASSESSABLE
- `scoring_ranking`: NOT RUN (requires MatchScorer judgment)
- `roadmap_presentation`: NOT RUN (requires ranked strategy)
- `root_layer`: corpus/data problem


## Baseline blockers

- No executed FAIL is classified as a baseline-stability blocker.

## Later `100` branch

- **Hard format and runtime mismatch** — WARN: Structured format incompatibility dominates as required. Runtime ranges are only descriptive text in the corpus, so the system correctly warns but cannot enforce them as hard rules without authoritative data.
- **Genre specialist retrieval: Sitges** — FAIL: The expected specialist is absent from the committed corpus; retrieval cannot recover a missing entity.
- **User preference versus domain reality** — WARN: A compatible LGBTQ preference materially changes retrieval, and deterministic eligibility still overrides an impossible prestige request. Preference preservation remains implicit in FilmAnalyzer.search_query rather than an explicit scored field.

## Retrieval diagnosis rule

Every retrieval scenario reports these layers independently:

1. `data_corpus` — whether the festival/entity exists in the committed corpus.
2. `retrieval_recall` — whether it survives broad retrieval and the candidate cutoff.
3. `scoring_ranking` — whether MatchScorer ranks a retrieved candidate appropriately.
4. `roadmap_presentation` — whether a correctly ranked candidate survives into the strategy.

A missing corpus entity is never reported as a retrieval, scoring or roadmap failure.

## Cost and coverage

- Real full `/api/execute` runs: `0`
- Successful real chat calls inferred from trace: `0`
- Real chat attempts in trace: `0`
- Successful real embedding calls inferred from trace: `0`
- Real embedding attempts in trace: `0`
- Offline/mocked behavioral scenarios: `17`
- No judge LLM was used.

## Reproduction

```bash
.venv/bin/python evals/run_behavioral.py --live-runs 0
.venv/bin/python evals/run_behavioral.py --working-tree
.venv/bin/python evals/run_behavioral.py --live-runs 1
.venv/bin/python evals/run_behavioral.py --reuse-live-artifact
.venv/bin/python -m unittest discover -s tests -v
```

The first command evaluates committed HEAD offline; the second evaluates current uncommitted fixes offline. The live command permits exactly one paid probe, and the reuse command reuses saved live evidence without another call. The runner rejects values above one for this suite and retains the global hard cap of ten.

## Raw artifacts

- Full structured result: `evals/artifacts/working-tree-1b6d8c0f0e88/results.json`
- Existing test output: `evals/artifacts/working-tree-1b6d8c0f0e88/existing_tests.txt`
- Per-FAIL reproductions: `evals/artifacts/working-tree-1b6d8c0f0e88/failures/`

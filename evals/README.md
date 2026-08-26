# Behavioral evaluations

Campaign Workspace has a separate, fully offline deterministic gate:

```bash
.venv/bin/python evals/run_campaign.py
```

It evaluates planner archetypes A–E in all preservation modes, deterministic
repeatability, hard constraints, budget states, premiere screening/correction
semantics, zero-provider rejection replanning, scenario no-write behavior,
capability-scoped repository isolation, and corpus coverage. It performs no
provider calls or external writes and continues to classify Sitges as a known
corpus-coverage issue.

The runner evaluates an isolated `git archive` of the current `HEAD`. It never
imports production code from uncommitted working-tree changes.

Offline/mocked suite plus the existing regression suite:

```bash
.venv/bin/python evals/run_behavioral.py --live-runs 0
```

Evaluate uncommitted correctness fixes before committing them:

```bash
.venv/bin/python evals/run_behavioral.py --working-tree
```

The same suite plus exactly one paid live `/api/execute` probe:

```bash
.venv/bin/python evals/run_behavioral.py --live-runs 1
```

After a live attempt, rerun the offline checks while preserving that exact live
evidence without another provider call:

```bash
.venv/bin/python evals/run_behavioral.py --reuse-live-artifact
```

The runner implements only `0` or `1` live run and enforces a global hard cap of
`10`. It does not seed Pinecone or Supabase. Reports and raw failure artifacts
are redacted before they are written.

Outputs:

- `evals/latest_report.md`
- `evals/artifacts/<commit>/results.json`
- `evals/artifacts/<commit>/existing_tests.txt`
- `evals/artifacts/<commit>/failures/*.json`

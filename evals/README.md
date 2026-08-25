# Behavioral evaluations

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

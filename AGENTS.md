# Agent Collaboration Benchmark

This workspace measures whether delegating implementation to a local LLM reduces the supervising model's token usage without reducing quality.

## Rules

- Read only `README.md`, `specs/sample-app.md`, and the selected benchmark task before starting.
- For the direct arm, implement the task yourself.
- For the delegated arm, use `skills/qwen-delegate/SKILL.md` and do not inspect implementation files after a passing independent gate.
- Never edit files under `benchmark/`, `specs/`, or `skills/` during a run.
- Do not commit, push, install dependencies, or access the network during a run.
- A run is successful only when the independent gate passes and the changed paths match its allowlist.
- Report parent usage, child usage, wall time, gate result, changed paths, and session cleanup separately.

## Benchmark hygiene

- Start each arm from the same baseline commit in a fresh session and detached worktree.
- Use the exact prompt in `benchmark/task.md` without adding repository history or prior-run results.
- Run one arm per supervisor session. Do not diagnose or repair benchmark infrastructure inside a measured turn.

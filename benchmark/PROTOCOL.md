# A/B protocol

## Controlled variables

- Baseline: the same immutable commit
- Supervisor model and effort: the same GPT Sol configuration
- Task: the exact text in `benchmark/task.md`
- Quality gate: `benchmark/gate.json`
- Environment: the same machine and Local LLM endpoint
- Cache state: record it; do not silently compare warm and cold runs

## Arm A: direct

Start a fresh supervisor session in a clean detached worktree and send:

```text
Run the direct arm. Implement benchmark/task.md yourself and stop after the independent gate result. Do not use qwen-delegate.
```

## Arm B: delegated

Start another fresh supervisor session from the same baseline commit and send:

```text
Run the delegated arm. Use the qwen-delegate skill to have Local LLM Qwen implement benchmark/task.md. End the launch turn immediately after verifying the persistent worker. On the completion trigger, report summary.json without rereading the implementation when gate_pass is true.
```

## Required result fields

```json
{
  "arm": "direct | delegated",
  "baseline_commit": "...",
  "supervisor": {
    "model": "...",
    "effort": "...",
    "input_tokens": 0,
    "cached_input_tokens": 0,
    "output_tokens": 0,
    "turns": 0
  },
  "child": null,
  "wall_time_seconds": 0,
  "gate_pass": false,
  "attempts": 0,
  "changed_paths": [],
  "session_cleanup": null
}
```

For the delegated arm, `child` contains the Local LLM usage object and `session_cleanup` must be `closed` after success.

## Validity rules

Invalidate and repeat an arm when:

- It starts from a different commit or a dirty worktree.
- Infrastructure diagnosis or a previous result is included in the measured session.
- The task, gate, model, or effort differs between arms.
- Runtime-generated paths are treated as implementation changes.
- A successful delegated arm causes the supervisor to reread code or rerun tests.

Run each valid arm at least three times and compare medians. Keep failed runs as incident evidence but exclude them from performance medians.

# Agent Collaboration Benchmark

A minimal workspace for measuring collaboration between a supervising cloud model and an implementing local model.

The first benchmark compares two ways to build the same dependency-free sample application:

- **Direct:** GPT Sol reads the task, implements it, and runs the independent gate.
- **Delegated:** GPT Sol launches Qwen through the bundled skill. Qwen implements the task and the worker runs the same independent gate. After a passing gate, GPT Sol reports the structured result without rereading the implementation.

The benchmark answers three questions:

1. Does delegation preserve completion quality?
2. Does it reduce the supervising GPT Sol's uncached input plus output tokens?
3. What latency and local-model token cost does that saving require?

## Sample application

The target is **Collab Tasks**, a small browser task board implemented with HTML, CSS, and JavaScript only. The repository initially contains its specification and independent gate, but not its implementation. See [specs/sample-app.md](specs/sample-app.md).

## Fixed benchmark

- Task: [benchmark/task.md](benchmark/task.md)
- A/B protocol: [benchmark/PROTOCOL.md](benchmark/PROTOCOL.md)
- Gate contract: [benchmark/gate.json](benchmark/gate.json)
- Independent acceptance test: [benchmark/gates/task-store.acceptance.mjs](benchmark/gates/task-store.acceptance.mjs)
- Delegation skill: [skills/qwen-delegate/SKILL.md](skills/qwen-delegate/SKILL.md)

Run the independent gate after an arm finishes:

```bash
npm test
npm run check
git diff --check
```

## Comparison metric

The primary supervisor metric is:

```text
parent_uncached_plus_output = parent_input - parent_cached_input + parent_output
```

Also retain raw parent tokens, child tokens, wall time, gate result, changed paths, attempt count, and cleanup status. Never combine parent and child tokenizers into one efficiency score.

## Repository policy

This private repository is a benchmark fixture. Keep the baseline small and immutable. Store generated implementations and run logs outside the baseline branch or in disposable worktrees.

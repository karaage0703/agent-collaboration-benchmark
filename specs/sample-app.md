# Agent Mission Control specification

Agent Mission Control is a dependency-free browser kanban board for coordinating work between GPT Sol, Qwen, and a human reviewer. It must work when `index.html` is served by a basic static HTTP server.

## Required files

- `index.html`
- `styles.css`
- `src/board-store.js`
- `src/app.js`
- `test/board-store.test.js`

## Task model

Each mission card has this shape:

```js
{
  id: "mission-1",
  title: "Build the benchmark",
  status: "backlog",
  assignee: "qwen",
  priority: "high",
  createdAt: 1720000000000
}
```

## Public store API

`src/board-store.js` must export these named functions:

- `createCard(title, options?)`
- `addCard(cards, title, options?)`
- `moveCard(cards, id, status)`
- `renameCard(cards, id, title)`
- `assignCard(cards, id, assignee)`
- `setPriority(cards, id, priority)`
- `removeCard(cards, id)`
- `groupCards(cards)`
- `summarizeBoard(cards)`
- `serializeBoard(cards)`
- `parseBoard(json)`

Rules:

- All operations are pure: never mutate the input array or task objects.
- Titles are trimmed. Empty or whitespace-only titles are rejected with `TypeError`.
- `createCard` accepts optional deterministic `{ id, now, status, assignee, priority }` values. Without them it may generate `id` and `now`; the other defaults are `backlog`, `human`, and `medium`.
- Unknown IDs leave the list unchanged by value while still returning a new array.
- Statuses are `backlog`, `in-progress`, and `done`.
- Assignees are `sol`, `qwen`, and `human`.
- Priorities are `low`, `medium`, and `high`.
- Invalid status, assignee, or priority input falls back to its default for creation and leaves an existing card unchanged for updates.
- `groupCards` returns an object with arrays for all three statuses, preserving relative order.
- `summarizeBoard` returns `{ total, backlog, inProgress, done, progress }`, where progress is the integer percentage of done cards and is `0` for an empty board.
- Serialization is JSON. Parsing invalid JSON or a non-array returns `[]`.
- Parsing drops invalid entries and normalizes valid entries without throwing. Normalization trims both `id` and `title` strings.

## Browser UI

The initial empty-storage view must include at least four realistic demo missions distributed across all three columns so visual quality can be judged immediately. The page must provide:

- Three clearly labelled columns: Backlog, In Progress, and Done
- A compact form to add a mission with title, assignee, and priority
- Cards showing title, assignee, priority, and movement controls
- Rename, move, reassign, reprioritize, and delete interactions
- Total and per-column counts plus an overall progress meter
- Persistence through `localStorage` under `agent-mission-control-v1`
- A modern mission-control visual language with restrained color, clear hierarchy, named CSS design tokens, and no remote fonts or assets
- A responsive layout with no horizontal page overflow at 320, 375, 414, and 768 px; columns may stack vertically on narrow screens
- Keyboard-accessible native controls, visible `:focus-visible` styles, and non-color-only status/priority labels
- Clear default, hover, focus, active, error, and success feedback for relevant controls; avoid unexplained disabled actions

No framework, package dependency, remote asset, build step, or network request is allowed.

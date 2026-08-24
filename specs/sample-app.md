# Collab Tasks specification

Collab Tasks is a dependency-free browser task board. It must work when `index.html` is served by a basic static HTTP server.

## Required files

- `index.html`
- `styles.css`
- `src/task-store.js`
- `src/app.js`
- `test/task-store.test.js`

## Task model

Each task has this shape:

```js
{
  id: "task-1",
  title: "Write the benchmark",
  completed: false,
  createdAt: 1720000000000
}
```

## Public store API

`src/task-store.js` must export these named functions:

- `createTask(title, options?)`
- `addTask(tasks, title, options?)`
- `toggleTask(tasks, id)`
- `renameTask(tasks, id, title)`
- `removeTask(tasks, id)`
- `clearCompleted(tasks)`
- `filterTasks(tasks, filter)`
- `summarizeTasks(tasks)`
- `serializeTasks(tasks)`
- `parseTasks(json)`

Rules:

- All operations are pure: never mutate the input array or task objects.
- Titles are trimmed. Empty or whitespace-only titles are rejected with `TypeError`.
- `createTask` accepts optional deterministic `{ id, now }` values. Without them it may generate values.
- Unknown IDs leave the list unchanged by value while still returning a new array.
- Filters are `all`, `active`, and `completed`; an unknown filter behaves as `all`.
- `summarizeTasks` returns `{ total, active, completed }`.
- Serialization is JSON. Parsing invalid JSON or a non-array returns `[]`.
- Parsing drops invalid entries and normalizes valid entries without throwing.

## Browser UI

The page must provide:

- A form to add a task
- All, Active, and Completed filters
- Toggle, rename, and delete controls
- A clear-completed action
- Counts for total, active, and completed tasks
- Persistence through `localStorage` under `collab-tasks-v1`
- A responsive layout usable at 375 px width
- Keyboard-accessible native controls and visible focus styles

No framework, package dependency, remote asset, build step, or network request is allowed.

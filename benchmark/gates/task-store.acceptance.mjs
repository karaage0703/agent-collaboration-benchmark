import assert from 'node:assert/strict';
import test from 'node:test';

import {
  addTask,
  clearCompleted,
  createTask,
  filterTasks,
  parseTasks,
  removeTask,
  renameTask,
  serializeTasks,
  summarizeTasks,
  toggleTask,
} from '../../src/task-store.js';

const original = [
  { id: 'a', title: 'Alpha', completed: false, createdAt: 10 },
  { id: 'b', title: 'Beta', completed: true, createdAt: 20 },
];

test('creates normalized deterministic tasks and rejects empty titles', () => {
  assert.deepEqual(createTask('  Ship it  ', { id: 'fixed', now: 42 }), {
    id: 'fixed',
    title: 'Ship it',
    completed: false,
    createdAt: 42,
  });
  assert.throws(() => createTask('   '), TypeError);
});
test('all update operations are immutable', () => {
  const snapshot = structuredClone(original);
  const added = addTask(original, 'Gamma', { id: 'c', now: 30 });
  const toggled = toggleTask(original, 'a');
  const renamed = renameTask(original, 'a', '  Renamed  ');
  const removed = removeTask(original, 'a');
  const cleared = clearCompleted(original);

  assert.deepEqual(original, snapshot);
  assert.notStrictEqual(added, original);
  assert.notStrictEqual(toggled, original);
  assert.notStrictEqual(toggled[0], original[0]);
  assert.equal(toggled[0].completed, true);
  assert.equal(renamed[0].title, 'Renamed');
  assert.deepEqual(removed.map((task) => task.id), ['b']);
  assert.deepEqual(cleared.map((task) => task.id), ['a']);
});

test('unknown IDs preserve values but return a new array', () => {
  for (const result of [
    toggleTask(original, 'missing'),
    renameTask(original, 'missing', 'Valid'),
    removeTask(original, 'missing'),
  ]) {
    assert.notStrictEqual(result, original);
    assert.deepEqual(result, original);
  }
});

test('filters and summaries follow the public contract', () => {
  assert.deepEqual(filterTasks(original, 'active').map((task) => task.id), ['a']);
  assert.deepEqual(filterTasks(original, 'completed').map((task) => task.id), ['b']);
  assert.deepEqual(filterTasks(original, 'unknown'), original);
  assert.deepEqual(summarizeTasks(original), { total: 2, active: 1, completed: 1 });
});

test('serialization round-trips and parsing rejects malformed entries safely', () => {
  assert.deepEqual(parseTasks(serializeTasks(original)), original);
  assert.deepEqual(parseTasks('{broken'), []);
  assert.deepEqual(parseTasks('{}'), []);

  const parsed = parseTasks(JSON.stringify([
    { id: ' ok ', title: ' Valid ', completed: 1, createdAt: 5 },
    { id: '', title: 'Missing id', completed: false, createdAt: 1 },
    { id: 'bad-title', title: ' ', completed: false, createdAt: 1 },
    null,
  ]));
  assert.deepEqual(parsed, [
    { id: 'ok', title: 'Valid', completed: true, createdAt: 5 },
  ]);
});

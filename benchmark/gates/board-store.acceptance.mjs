import assert from 'node:assert/strict';
import test from 'node:test';

import {
  addCard,
  assignCard,
  createCard,
  groupCards,
  moveCard,
  parseBoard,
  removeCard,
  renameCard,
  serializeBoard,
  setPriority,
  summarizeBoard,
} from '../../src/board-store.js';

const original = [
  { id: 'a', title: 'Alpha', status: 'backlog', assignee: 'sol', priority: 'high', createdAt: 10 },
  { id: 'b', title: 'Beta', status: 'in-progress', assignee: 'qwen', priority: 'medium', createdAt: 20 },
  { id: 'c', title: 'Gamma', status: 'done', assignee: 'human', priority: 'low', createdAt: 30 },
];

test('creates normalized deterministic cards and rejects empty titles', () => {
  assert.deepEqual(createCard('  Ship it  ', {
    id: 'fixed', now: 42, status: 'done', assignee: 'qwen', priority: 'high',
  }), {
    id: 'fixed', title: 'Ship it', status: 'done', assignee: 'qwen', priority: 'high', createdAt: 42,
  });
  assert.throws(() => createCard('   '), TypeError);
  assert.deepEqual(createCard('Defaults', { id: 'd', now: 1 }), {
    id: 'd', title: 'Defaults', status: 'backlog', assignee: 'human', priority: 'medium', createdAt: 1,
  });
});

test('all update operations are immutable', () => {
  const snapshot = structuredClone(original);
  const added = addCard(original, 'Delta', { id: 'd', now: 40 });
  const moved = moveCard(original, 'a', 'done');
  const renamed = renameCard(original, 'a', '  Renamed  ');
  const assigned = assignCard(original, 'a', 'qwen');
  const prioritized = setPriority(original, 'a', 'low');
  const removed = removeCard(original, 'a');

  assert.deepEqual(original, snapshot);
  for (const result of [added, moved, renamed, assigned, prioritized, removed]) {
    assert.notStrictEqual(result, original);
  }
  assert.notStrictEqual(moved[0], original[0]);
  assert.equal(moved[0].status, 'done');
  assert.equal(renamed[0].title, 'Renamed');
  assert.equal(assigned[0].assignee, 'qwen');
  assert.equal(prioritized[0].priority, 'low');
  assert.deepEqual(removed.map((card) => card.id), ['b', 'c']);
});

test('unknown IDs and invalid enum updates preserve values in a new array', () => {
  for (const result of [
    moveCard(original, 'missing', 'done'),
    renameCard(original, 'missing', 'Valid'),
    assignCard(original, 'a', 'robot'),
    setPriority(original, 'a', 'urgent'),
    removeCard(original, 'missing'),
  ]) {
    assert.notStrictEqual(result, original);
    assert.deepEqual(result, original);
  }
});

test('groups cards and summarizes progress', () => {
  const grouped = groupCards(original);
  assert.deepEqual(grouped.backlog.map((card) => card.id), ['a']);
  assert.deepEqual(grouped['in-progress'].map((card) => card.id), ['b']);
  assert.deepEqual(grouped.done.map((card) => card.id), ['c']);
  assert.deepEqual(summarizeBoard(original), {
    total: 3, backlog: 1, inProgress: 1, done: 1, progress: 33,
  });
  assert.deepEqual(summarizeBoard([]), {
    total: 0, backlog: 0, inProgress: 0, done: 0, progress: 0,
  });
});

test('serialization round-trips and parsing rejects malformed cards safely', () => {
  assert.deepEqual(parseBoard(serializeBoard(original)), original);
  assert.deepEqual(parseBoard('{broken'), []);
  assert.deepEqual(parseBoard('{}'), []);

  const parsed = parseBoard(JSON.stringify([
    { id: ' ok ', title: ' Valid ', status: 'done', assignee: 'qwen', priority: 'high', createdAt: 5 },
    { id: '', title: 'Missing id', status: 'done', assignee: 'qwen', priority: 'high', createdAt: 1 },
    { id: 'bad-status', title: 'Bad', status: 'other', assignee: 'qwen', priority: 'high', createdAt: 1 },
    null,
  ]));
  assert.deepEqual(parsed, [
    { id: 'ok', title: 'Valid', status: 'done', assignee: 'qwen', priority: 'high', createdAt: 5 },
  ]);
});

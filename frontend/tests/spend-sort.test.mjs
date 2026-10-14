import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const source = await readFile(new URL('../src/spend-sort.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { sortSpendRows } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

test('names sort naturally, including PR numbers', () => {
  const rows = [{ name: 'repo #10' }, { name: 'Repo #2' }, { name: 'another/repo #1' }];
  const ascending = sortSpendRows(rows, { key: 'name', direction: 'ascending' });
  assert.deepEqual(ascending.map(row => row.name), ['another/repo #1', 'Repo #2', 'repo #10']);
  assert.deepEqual(sortSpendRows(rows, { key: 'name', direction: 'descending' }), ascending.toReversed());
});

for (const key of ['runs', 'pull_requests', 'average_runs_per_pr', 'cost_usd', 'average_cost_usd', 'average_pr_cost_usd']) {
  test(`${key} sorts numerically and keeps unknown values last in both directions`, () => {
    const rows = Object.freeze([
      { name: 'Ten', [key]: 10 }, { name: 'Unknown', [key]: null },
      { name: 'Two', [key]: 2 }, { name: 'Zero', [key]: 0 },
      { name: 'Missing' }, { name: 'Fraction', [key]: 0.25 },
    ].map(Object.freeze));
    assert.deepEqual(sortSpendRows(rows, { key, direction: 'ascending' }).map(row => row.name),
      ['Zero', 'Fraction', 'Two', 'Ten', 'Unknown', 'Missing']);
    assert.deepEqual(sortSpendRows(rows, { key, direction: 'descending' }).map(row => row.name),
      ['Ten', 'Two', 'Fraction', 'Zero', 'Unknown', 'Missing']);
    assert.equal(rows[0].name, 'Ten');
    assert.equal(rows[1].name, 'Unknown');
  });
}

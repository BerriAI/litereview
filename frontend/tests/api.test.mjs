import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const source = await readFile(new URL('../src/api.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText;
const { api, requestState } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
globalThis.window = new EventTarget();

test('concurrent reads share a request but the next read is fresh', async t => {
  let resolve;
  let calls = 0;
  t.mock.method(globalThis, 'fetch', () => { calls++; return new Promise(r => { resolve = r; }); });
  const first = api('/resources/agents');
  const second = api('/resources/agents');
  assert.equal(first, second);
  assert.equal(requestState.getSnapshot().length, 1);
  resolve(Response.json([{ name: 'review' }]));
  await first;
  assert.equal(requestState.getSnapshot().length, 0);
  const fresh = api('/resources/agents');
  assert.equal(calls, 2);
  resolve(Response.json([]));
  await fresh;
});

test('slow reads time out with a recovery message and clear pending state', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  t.mock.method(globalThis, 'fetch', (url, { signal }) => new Promise((resolve, reject) => {
    signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
  }));
  const pending = api('/runs');
  const rejection = assert.rejects(pending, /took too long.*Retry/);
  t.mock.timers.tick(20000);
  await rejection;
  assert.equal(requestState.getSnapshot().length, 0);
});

test('navigation can cancel a read without turning it into a server error', async t => {
  t.mock.method(globalThis, 'fetch', (url, { signal }) => new Promise((resolve, reject) => {
    signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
  }));
  const controller = new AbortController();
  const pending = api('/runs/previous', { signal: controller.signal });
  controller.abort();
  await assert.rejects(pending, { name: 'AbortError' });
  assert.equal(requestState.getSnapshot().length, 0);
});

test('failed writes are not retried and warn that the outcome is unknown', async t => {
  let calls = 0;
  t.mock.method(globalThis, 'fetch', async () => { calls++; throw new TypeError('Network error'); });
  await assert.rejects(api('/resources/agents', { method: 'POST', body: '{}' }), /Check the saved value/);
  assert.equal(calls, 1);
});

test('expired authorization is rechecked and not cached', async t => {
  let events = 0;
  window.addEventListener('litereview:unauthorized', () => events++, { once: true });
  t.mock.method(globalThis, 'fetch', async () => Response.json({ detail: 'Sign in again.' }, { status: 401 }));
  await assert.rejects(api('/workspace'), { status: 401 });
  assert.equal(events, 1);
  assert.equal(requestState.getSnapshot().length, 0);
});

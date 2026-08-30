import { BASE, readJsonOrThrow } from './core';

export async function optimizeOnce() {
  const res = await fetch(`${BASE}/api/optimize/once`, { method: 'POST' });
  return readJsonOrThrow(res);
}

export async function optimizeRollback(target: string = 'best') {
  const res = await fetch(`${BASE}/api/optimize/rollback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ target }),
  });
  return readJsonOrThrow(res);
}

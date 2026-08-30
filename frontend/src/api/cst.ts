import { BASE, readJsonOrThrow } from './core';

export async function cstReconnect() {
  const res = await fetch(`${BASE}/api/cst/reconnect`, { method: 'POST' });
  return readJsonOrThrow(res);
}

export async function cstSimulate() {
  const res = await fetch(`${BASE}/api/cst/simulate`, { method: 'POST' });
  return readJsonOrThrow(res);
}

export async function cstReadS11() {
  const res = await fetch(`${BASE}/api/cst/read-s11`, { method: 'POST' });
  return readJsonOrThrow(res);
}

import { BASE, asNumber, asRecord, readJsonOrThrow, stringField } from './core';
import type { TokenStats, VbaResponse } from './types';

function normalizeVbaResponse(value: unknown): VbaResponse {
  const data = asRecord(value);
  return { code: stringField(data, 'code') };
}

export async function stateClear() {
  const res = await fetch(`${BASE}/api/state/clear`, { method: 'POST' });
  return readJsonOrThrow(res);
}

export async function fetchTokenStats(): Promise<TokenStats> {
  const res = await fetch(`${BASE}/api/state/token-stats`);
  const data = asRecord(await readJsonOrThrow(res));
  return {
    prompt: asNumber(data.prompt, 0),
    completion: asNumber(data.completion, 0),
    total: asNumber(data.total, 0),
    calls: asNumber(data.calls, 0),
    cost_usd: asNumber(data.cost_usd, 0),
  };
}

export async function fetchVba(): Promise<VbaResponse> {
  const res = await fetch(`${BASE}/api/state/vba`);
  return normalizeVbaResponse(await readJsonOrThrow(res));
}

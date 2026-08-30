import { BASE, asArray, asNumber, asRecord, readJsonOrThrow } from './core';
import type { FarfieldResult, S11Result } from './types';

export async function fetchS11(): Promise<S11Result> {
  const res = await fetch(`${BASE}/api/results/s11`);
  const data = asRecord(await readJsonOrThrow(res));
  return {
    available: Boolean(data.available),
    plotData: asArray(data.plotData),
    summary: data.summary && typeof data.summary === 'object' ? data.summary as S11Result['summary'] : null,
    targetFreqGhz: asNumber(data.targetFreqGhz, 0),
    targetDb: asNumber(data.targetDb, -10),
  };
}

export async function fetchFarfield(): Promise<FarfieldResult> {
  const res = await fetch(`${BASE}/api/results/farfield`);
  const data = asRecord(await readJsonOrThrow(res));
  return {
    available: Boolean(data.available),
    plotData: asArray(data.plotData),
    title: typeof data.title === 'string' ? data.title : '',
    cutType: typeof data.cutType === 'string' ? data.cutType : 'phi',
    cutValueDeg: asNumber(data.cutValueDeg, 0),
    frequencyGhz: data.frequencyGhz === null || data.frequencyGhz === undefined ? null : asNumber(data.frequencyGhz, 0),
    summary: data.summary && typeof data.summary === 'object' ? data.summary as FarfieldResult['summary'] : null,
  };
}

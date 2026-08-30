export function toNumberOrNull(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  const numeric = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

export function toFiniteNumber(value: unknown, fallback = 0): number {
  return toNumberOrNull(value) ?? fallback;
}

export function formatNumber(value: unknown, digits: number, suffix = ''): string {
  const numeric = toNumberOrNull(value);
  return numeric === null ? '---' : `${numeric.toFixed(digits)}${suffix}`;
}

export function formatInteger(value: unknown): string {
  const numeric = toNumberOrNull(value);
  return numeric === null ? '---' : Math.round(numeric).toLocaleString();
}

export function formatUsd(value: unknown, digits = 4): string {
  const numeric = toNumberOrNull(value);
  return numeric === null ? '---' : `$${numeric.toFixed(digits)}`;
}

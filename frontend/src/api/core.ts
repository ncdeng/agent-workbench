import type { ToolEvent } from './types';

export const BASE = '';

export function asArray<T = unknown>(value: unknown): T[] {
  return Array.isArray(value) ? value as T[] : [];
}

export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? value as Record<string, unknown> : {};
}

export function asNumber(value: unknown, fallback: number): number {
  if (value === null || value === undefined || value === '') return fallback;
  const numeric = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

export function asNullableNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  const numeric = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

export function flagOrNull(value: unknown): boolean | null {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'number') return value !== 0;
  if (typeof value === 'string') {
    return ['true', '1', 'yes', 'ok', 'success', 'succeeded'].includes(value.trim().toLowerCase());
  }
  return null;
}

export function asBoolean(value: unknown): boolean {
  return flagOrNull(value) ?? false;
}

// 后端失败形状有 success/ok/error 三种，
// 这里是唯一的归一化点，页面不要再各自实现。
export function actionSucceeded(result: unknown): boolean {
  if (!result || typeof result !== 'object') return true;
  const data = result as Record<string, unknown>;
  const success = flagOrNull(data.success);
  if (success !== null) return success;
  const ok = flagOrNull(data.ok);
  if (ok !== null) return ok;
  return !data.error;
}

export function stringField(record: Record<string, unknown>, key: string): string {
  const value = record[key];
  return typeof value === 'string' ? value : '';
}

function errorMessageFromPayload(data: unknown, status: number): string {
  const record = asRecord(data);
  const detail = record.detail;
  const error = record.error;
  const message = record.message;
  if (typeof error === 'string' && error) return error;
  if (typeof detail === 'string' && detail) return detail;
  if (typeof message === 'string' && message) return message;
  if (detail !== undefined) {
    try {
      return JSON.stringify(detail);
    } catch {}
  }
  return `HTTP ${status}`;
}

export async function readJsonOrThrow<T = any>(res: Response): Promise<T> {
  const data = typeof res.json === 'function' ? await res.json().catch(() => null) : null;
  if (!res.ok) {
    throw new Error(errorMessageFromPayload(data, res.status));
  }
  return data;
}

export function normalizeToolEvent(value: unknown): ToolEvent {
  const event = asRecord(value);
  const approval = asRecord(event.approvalRequest ?? event.approval_request);
  const approvalRequest = typeof approval.request_id === 'string'
    ? {
        requestId: approval.request_id,
        toolName: typeof approval.tool_name === 'string' ? approval.tool_name : '',
        argumentsSha256: typeof approval.arguments_sha256 === 'string' ? approval.arguments_sha256 : '',
        createdAt: typeof approval.created_at === 'string' ? approval.created_at : '',
        expiresAt: typeof approval.expires_at === 'string' ? approval.expires_at : '',
        status: typeof approval.status === 'string' ? approval.status : 'pending',
        preview: typeof approval.preview === 'string' ? approval.preview : '',
      }
    : undefined;
  return {
    phase: typeof event.phase === 'string' ? event.phase : '',
    tool: typeof event.tool === 'string'
      ? event.tool
      : typeof event.tool_name === 'string' ? event.tool_name : '',
    success: asBoolean(event.success),
    description: typeof event.description === 'string'
      ? event.description
      : typeof event.message === 'string' ? event.message : '',
    errorType: typeof event.errorType === 'string'
      ? event.errorType
      : typeof event.error_type === 'string' ? event.error_type : undefined,
    approvalRequest,
  };
}

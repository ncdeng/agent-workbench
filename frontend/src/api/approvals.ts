import { BASE, asRecord, readJsonOrThrow } from './core';
import type { ToolApprovalDecision, ToolApprovalRequest } from './types';

function normalizeRequest(value: unknown): ToolApprovalRequest {
  const data = asRecord(value);
  return {
    requestId: typeof data.request_id === 'string' ? data.request_id : '',
    toolName: typeof data.tool_name === 'string' ? data.tool_name : '',
    argumentsSha256: typeof data.arguments_sha256 === 'string' ? data.arguments_sha256 : '',
    createdAt: typeof data.created_at === 'string' ? data.created_at : '',
    expiresAt: typeof data.expires_at === 'string' ? data.expires_at : '',
    status: typeof data.status === 'string' ? data.status : '',
    preview: typeof data.preview === 'string' ? data.preview : '',
  };
}

export async function decideToolApproval(
  requestId: string,
  decision: 'approve' | 'reject',
): Promise<ToolApprovalDecision> {
  const res = await fetch(`${BASE}/api/approvals/${encodeURIComponent(requestId)}/${decision}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ttl_seconds: 120 }),
  });
  const data = asRecord(await readJsonOrThrow(res));
  return {
    approved: data.approved === true,
    rejected: data.rejected === true,
    executed: data.executed === true,
    request: normalizeRequest(data.request),
    result: asRecord(data.result),
  };
}

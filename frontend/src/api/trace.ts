import { BASE, asArray, asBoolean, asNumber, asRecord, readJsonOrThrow, stringField } from './core';
import type { TraceDecisionSummary, TraceDetail, TraceEntry, TraceListResponse, TracePlanSummary, TraceRecoveryResult, TraceToolCall, TraceTurn } from './types';

function normalizeTraceEntry(value: unknown): TraceEntry {
  const entry = asRecord(value);
  return {
    runId: stringField(entry, 'runId'),
    status: stringField(entry, 'status'),
    turnCount: asNumber(entry.turnCount, 0),
    toolCallCount: asNumber(entry.toolCallCount, 0),
    decisionSummary: stringField(entry, 'decisionSummary'),
  };
}

function normalizeTracePlanSummary(value: unknown): TracePlanSummary {
  const record = asRecord(value);
  return {
    ...record,
    intent_kind: stringField(record, 'intent_kind') || undefined,
    completed_step_count: asNumber(record.completed_step_count, 0),
    step_count: asNumber(record.step_count, 0),
    replan_count: asNumber(record.replan_count, 0),
    stop_reason: stringField(record, 'stop_reason') || undefined,
  };
}

function normalizeTraceDecisionSummary(value: unknown): TraceDecisionSummary {
  const record = asRecord(value);
  return {
    ...record,
    run_outcome: stringField(record, 'run_outcome') || undefined,
    final_action: stringField(record, 'final_action') || undefined,
    decision_result: stringField(record, 'decision_result') || undefined,
    assistant_action: stringField(record, 'assistant_action') || undefined,
    observation_summary: stringField(record, 'observation_summary') || undefined,
    plan_summary: normalizeTracePlanSummary(record.plan_summary),
  };
}

function normalizeTraceRecoveryResult(value: unknown): TraceRecoveryResult | undefined {
  if (!value || typeof value !== 'object') return undefined;
  const record = asRecord(value);
  return {
    action_name: stringField(record, 'action_name'),
    recovered: asBoolean(record.recovered),
    retry_success: record.retry_success === undefined ? undefined : asBoolean(record.retry_success),
    message: stringField(record, 'message'),
    retry_tool: stringField(record, 'retry_tool') || undefined,
    retry_arguments: record.retry_arguments && typeof record.retry_arguments === 'object'
      ? asRecord(record.retry_arguments)
      : undefined,
    details: record.details && typeof record.details === 'object'
      ? asRecord(record.details)
      : undefined,
  };
}

function normalizeTraceToolCall(value: unknown): TraceToolCall {
  const call = asRecord(value);
  const success = call.success === null || call.success === undefined ? null : asBoolean(call.success);
  return {
    tool_name: stringField(call, 'tool_name'),
    name: stringField(call, 'name'),
    phase: stringField(call, 'phase'),
    success,
    result_preview: stringField(call, 'result_preview'),
    status_label: stringField(call, 'status_label'),
    error: stringField(call, 'error'),
    recovery_result: normalizeTraceRecoveryResult(call.recovery_result),
  };
}

function normalizeTraceTurn(value: unknown): TraceTurn {
  const turn = asRecord(value);
  return {
    tool_calls: asArray(turn.tool_calls).map(normalizeTraceToolCall),
    decision_summary: normalizeTraceDecisionSummary(turn.decision_summary),
  };
}

function normalizeTraceDetail(value: unknown): TraceDetail | null {
  const detail = asRecord(value);
  if (Object.keys(detail).length === 0 || typeof detail.error === 'string' && !detail.runId) {
    return null;
  }
  const finalResponse = asRecord(detail.finalResponse);
  return {
    runId: stringField(detail, 'runId'),
    status: stringField(detail, 'status'),
    startedAt: typeof detail.startedAt === 'string' ? detail.startedAt : null,
    finishedAt: typeof detail.finishedAt === 'string' ? detail.finishedAt : null,
    turnCount: asNumber(detail.turnCount, 0),
    toolCallCount: asNumber(detail.toolCallCount, 0),
    turns: asArray(detail.turns).map(normalizeTraceTurn),
    planState: asRecord(detail.planState),
    snapshotEntry: asRecord(detail.snapshotEntry),
    snapshotExit: asRecord(detail.snapshotExit),
    runMetrics: asRecord(detail.runMetrics),
    decisionSummary: stringField(detail, 'decisionSummary'),
    decisionSummaryRaw: normalizeTraceDecisionSummary(detail.decisionSummaryRaw),
    finalResponse: { ...finalResponse, preview: stringField(finalResponse, 'preview') },
    error: stringField(detail, 'error'),
    tokenDelta: asRecord(detail.tokenDelta),
  };
}

function normalizeTraceListResponse(value: unknown): TraceListResponse {
  const data = asRecord(value);
  return {
    currentRunId: stringField(data, 'currentRunId'),
    traces: asArray(data.traces).map(normalizeTraceEntry),
    current: normalizeTraceDetail(data.current),
  };
}

export async function fetchTraceList(): Promise<TraceListResponse> {
  const res = await fetch(`${BASE}/api/agent/trace`);
  return normalizeTraceListResponse(await readJsonOrThrow(res));
}

export async function fetchTraceDetail(runId: string): Promise<TraceDetail | null> {
  const res = await fetch(`${BASE}/api/agent/trace/${encodeURIComponent(runId)}`);
  return normalizeTraceDetail(await readJsonOrThrow(res));
}

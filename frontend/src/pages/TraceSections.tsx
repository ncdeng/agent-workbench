import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Bot, Braces, Clock3, Code2, RefreshCw, Route, WalletCards } from 'lucide-react';
import type { TokenStats, TraceDetail, TraceEntry, TraceRecoveryResult, TraceToolCall } from '../api';
import { StatusPill } from '../components/StatusPill';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { Alert } from '../components/Alert';
import { formatInteger, formatUsd } from '../utils/numbers';

export type TraceTab = 'trace' | 'tokens' | 'vba';

function statusStripClass(status: unknown): string {
  if (status === 'completed') return 'bg-success';
  if (status === 'failed') return 'bg-danger';
  if (status === 'running') return 'bg-warning';
  return 'bg-border';
}

function statusTone(status: unknown): 'good' | 'warn' | 'bad' | 'neutral' {
  if (status === 'completed') return 'good';
  if (status === 'failed') return 'bad';
  if (status === 'running') return 'warn';
  return 'neutral';
}

function toolStatusTone(success: unknown): string {
  if (success === true) return 'bg-success';
  if (success === false) return 'bg-danger';
  return 'bg-warning';
}

const tabConfig = [
  { id: 'trace' as TraceTab, icon: <Route size={15} />, label: 'Trace' },
  { id: 'tokens' as TraceTab, icon: <WalletCards size={15} />, label: 'Token' },
  { id: 'vba' as TraceTab, icon: <Code2 size={15} />, label: 'VBA' },
];

export function TraceHeader({ tab, onTabChange, onRefresh }: {
  tab: TraceTab;
  onTabChange: (tab: TraceTab) => void;
  onRefresh: () => void;
}) {
  return (
    <Card className="p-4 flex items-center gap-3 page-section">
      <div>
        <span className="kicker">Observability</span>
        <h1 className="mt-1 text-text text-xl font-bold tracking-tight">Agent Trace / Evidence</h1>
      </div>
      <div className="ml-auto flex gap-2">
        {tabConfig.map(t => (
          <Button
            key={t.id}
            variant={tab === t.id ? 'secondary' : 'ghost'}
            onClick={() => onTabChange(t.id)}
          >
            {t.icon}
            {t.label}
          </Button>
        ))}
        <Button variant="ghost" className="w-9 h-9 p-0" onClick={onRefresh} title="刷新">
          <RefreshCw size={15} />
        </Button>
      </div>
    </Card>
  );
}

export function TraceLoadError({ error, onDismiss }: { error: string; onDismiss: () => void }) {
  return (
    <Alert variant="warning" onDismiss={onDismiss}>
      Trace 数据加载失败：{error}
    </Alert>
  );
}

export function TraceRunsList({ traces, selected, onSelect }: {
  traces: TraceEntry[];
  selected: string | null;
  onSelect: (runId: string) => void;
}) {
  return (
    <Card className="p-4 overflow-y-auto h-full">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-text font-bold text-sm">Trace Runs</h2>
        <span className="mono-chip">{traces.length}</span>
      </div>
      {traces.length === 0 ? (
        <p className="elevated p-3 text-text-muted text-sm">
          暂无 trace 记录。运行一次 Chat 后这里会出现完整执行链路。
        </p>
      ) : (
        traces.map((trace, i) => {
          const runId = typeof trace.runId === 'string' ? trace.runId : '';
          const isSelected = selected === runId;
          return (
            <button
              key={runId || i}
              className={`relative overflow-hidden w-full text-left p-3 rounded-xl border text-[0.82rem] transition-all mb-2 ${
                isSelected
                  ? 'border-primary/30 bg-primary-subtle'
                  : 'border-border bg-elevated hover:border-border-hover'
              }`}
              onClick={() => runId && onSelect(runId)}
              disabled={!runId}
            >
              <span
                className={`absolute left-0 top-2 bottom-2 w-1 rounded-r-full ${statusStripClass(trace.status)}`}
              />
              <div className="flex items-center justify-between gap-2 pl-1.5">
                <span className="font-mono text-[0.74rem] text-text-secondary truncate">
                  {runId.slice(0, 12) || 'unknown'}
                </span>
                <StatusPill tone={statusTone(trace.status)}>{trace.status || 'unknown'}</StatusPill>
              </div>
              <p className="text-text-secondary mt-2">
                {formatInteger(trace.toolCallCount)} tools · {formatInteger(trace.turnCount)} turns
              </p>
              {trace.decisionSummary && (
                <p className="text-text-muted mt-1 text-[0.75rem] line-clamp-2">{trace.decisionSummary}</p>
              )}
            </button>
          );
        })
      )}
    </Card>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="elevated p-3">
      <span className="text-text-muted text-[0.68rem] uppercase tracking-[0.12em] font-semibold">{label}</span>
      <strong className="block mt-1.5 text-text text-[1rem] font-semibold truncate">{value}</strong>
    </div>
  );
}

function RecoveryBadge({ recovery }: { recovery: TraceRecoveryResult }) {
  const retrySucceeded = recovery.recovered && recovery.retry_success;
  const tone: 'good' | 'warn' | 'bad' = retrySucceeded ? 'good' : recovery.recovered ? 'warn' : 'bad';
  const label = retrySucceeded ? 'recovered + retried' : recovery.recovered ? 'recovered' : 'recovery failed';
  return <StatusPill tone={tone}>{label}</StatusPill>;
}

function RecoveryCard({ recovery }: { recovery: TraceRecoveryResult }) {
  return (
    <div className="mt-2 rounded-lg border border-border bg-elevated p-2.5">
      <div className="flex items-center gap-2 flex-wrap">
        <RecoveryBadge recovery={recovery} />
        <span className="font-mono text-[0.72rem] text-text">{recovery.action_name}</span>
      </div>
      {recovery.message && <p className="text-text-muted text-[0.74rem] mt-1.5">{recovery.message}</p>}
      {recovery.retry_tool && (
        <p className="text-text-muted text-[0.74rem] mt-1">
          retry tool: <span className="font-mono text-text-secondary">{recovery.retry_tool}</span>
        </p>
      )}
    </div>
  );
}

function ToolCallCard({ toolCall }: { toolCall: TraceToolCall }) {
  return (
    <div className="flex items-start gap-2.5 rounded-xl border border-border bg-elevated p-3">
      <span className={`w-2 h-2 rounded-full mt-1.5 ${toolStatusTone(toolCall.success)}`} />
      <div className="min-w-0 flex-1">
        <span className="font-mono text-[0.75rem] text-text">{toolCall.tool_name || toolCall.name || 'unknown_tool'}</span>
        <p className="text-text-muted text-[0.74rem] truncate">
          {toolCall.phase || 'phase unknown'}{toolCall.result_preview ? ` · ${toolCall.result_preview}` : ''}
        </p>
        {toolCall.error && <p className="text-danger text-[0.74rem] mt-1">{toolCall.error}</p>}
        {toolCall.recovery_result && <RecoveryCard recovery={toolCall.recovery_result} />}
      </div>
    </div>
  );
}

export function TraceDetailPanel({ detail, selected }: { detail: TraceDetail | null; selected: string | null }) {
  if (!detail) {
    return (
      <Card className="p-4 overflow-y-auto h-full">
        <div className="h-full flex flex-col items-center justify-center text-text-secondary text-sm text-center px-6">
          <span className="w-10 h-10 rounded-xl bg-primary-subtle text-primary grid place-items-center mb-3">
            <Route size={20} />
          </span>
          <p>选择一个 trace 查看 Planner、Executor、Tool Calling 和最终回复。</p>
          <Link to="/chat" className="mt-4 btn btn-secondary">
            去 Chat 运行一次
          </Link>
        </div>
      </Card>
    );
  }

  const detailRunId = detail.runId || selected;
  const detailTurns = detail.turns ?? [];
  const detailError = detail.error ?? '';
  const detailFinalPreview = detail.finalResponse.preview || detail.decisionSummary || '';
  const decisionRaw = detail.decisionSummaryRaw ?? {};

  return (
    <Card className="p-4 overflow-y-auto h-full">
      <div className="space-y-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <span className="kicker">Run ID</span>
            <p className="mt-1 text-text font-mono text-sm truncate">{detailRunId}</p>
          </div>
          <StatusPill tone={statusTone(detail.status)}>{detail.status || 'unknown'}</StatusPill>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <MetricCard label="Turns" value={formatInteger(detail.turnCount)} />
          <MetricCard label="Tools" value={formatInteger(detail.toolCallCount)} />
          <MetricCard label="Outcome" value={String(decisionRaw.run_outcome || detail.status || 'unknown')} />
          <MetricCard label="Action" value={String(decisionRaw.final_action || '-')} />
        </div>

        {detailError && (
          <div className="elevated p-3 text-[0.82rem] border-danger/30">
            <span className="text-danger font-semibold">Error</span>
            <p className="text-text-secondary mt-1 whitespace-pre-wrap">{detailError}</p>
          </div>
        )}

        {detailFinalPreview && (
          <div className="elevated p-3 text-[0.82rem]">
            <span className="kicker">Final Reply</span>
            <p className="text-text-secondary mt-2 whitespace-pre-wrap">{detailFinalPreview}</p>
          </div>
        )}

        <div>
          <span className="kicker">Turns ({detailTurns.length})</span>
          <div className="space-y-3 mt-2">
            {detailTurns.length === 0 ? (
              <p className="elevated p-3 text-text-muted text-sm">该运行暂无 turn 详情。</p>
            ) : (
              detailTurns.map((turn, i) => {
                const toolCalls = turn.tool_calls;
                const summary = turn.decision_summary;
                return (
                  <div key={i} className="elevated p-3 text-[0.82rem]">
                    <div className="flex items-center justify-between gap-3">
                      <span className="font-bold text-text">Turn {i + 1}</span>
                      <span className="text-text-muted">{summary.decision_result || summary.assistant_action || 'step'}</span>
                    </div>
                    {summary.observation_summary && (
                      <p className="text-text-secondary mt-2">{summary.observation_summary}</p>
                    )}
                    {toolCalls.length > 0 && (
                      <div className="mt-3 space-y-2">
                        {toolCalls.map((tc, j) => <ToolCallCard key={j} toolCall={tc} />)}
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>
    </Card>
  );
}

export function TraceSidePanel({ detail }: { detail: TraceDetail | null }) {
  const detailTurns = detail?.turns ?? [];
  const detailPlanState = detail?.planState ?? {};
  const planSummary = detail?.decisionSummaryRaw?.plan_summary ?? {};
  const allToolCalls = detailTurns.flatMap((turn) => turn.tool_calls);

  return (
    <div className="space-y-4 overflow-y-auto h-full">
      <Card className="p-4">
        <span className="kicker">Planner Context</span>
        <div className="mt-3 space-y-2">
          {[
            { label: 'intent', value: planSummary.intent_kind || '-' },
            { label: 'steps', value: `${planSummary.completed_step_count ?? 0}/${planSummary.step_count ?? 0}` },
            { label: 'replan', value: `${planSummary.replan_count ?? 0}` },
            { label: 'stop', value: planSummary.stop_reason || '-' },
          ].map(item => (
            <div key={item.label} className="flex justify-between gap-3 text-[0.82rem]">
              <span className="text-text-muted">{item.label}</span>
              <span className="text-text-secondary text-right truncate">{String(item.value)}</span>
            </div>
          ))}
        </div>
      </Card>

      <Card className="p-4">
        <span className="kicker">Tool Events</span>
        <div className="mt-3 space-y-2">
          {allToolCalls.length === 0 ? (
            <p className="text-text-muted text-sm">暂无工具调用。</p>
          ) : (
            allToolCalls.slice(-8).reverse().map((tc, i) => (
              <div key={i} className="flex items-start gap-2 text-[0.8rem]">
                <span className={`w-2 h-2 rounded-full mt-1.5 ${toolStatusTone(tc.success)}`} />
                <div className="min-w-0">
                  <p className="text-text font-mono truncate">{tc.tool_name || 'tool'}</p>
                  <p className="text-text-muted truncate">{tc.phase || tc.status_label || '-'}</p>
                </div>
              </div>
            ))
          )}
        </div>
      </Card>

      {Object.keys(detailPlanState).length > 0 && (
        <Card className="p-4">
          <span className="kicker">Plan State</span>
          <pre className="mt-3 p-3 rounded-xl bg-elevated border border-border text-[0.72rem] overflow-x-auto max-h-[220px] font-mono">
            {JSON.stringify(detailPlanState, null, 2)}
          </pre>
        </Card>
      )}
    </div>
  );
}

function TokenMetric({ label, value, icon }: { label: string; value: string; icon: ReactNode }) {
  return (
    <Card className="p-4" hover>
      <div className="text-text-muted">{icon}</div>
      <span className="block mt-3 text-text-muted text-[0.7rem] uppercase tracking-[0.12em] font-semibold">{label}</span>
      <strong className="block mt-2 text-text text-2xl font-bold">{value}</strong>
    </Card>
  );
}

export function TokenStatsPanel({ tokenStats }: { tokenStats: TokenStats | null }) {
  return (
    <Card className="p-5 page-section">
      {tokenStats ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <TokenMetric label="Prompt Tokens" value={formatInteger(tokenStats.prompt)} icon={<Bot size={18} />} />
          <TokenMetric label="Completion Tokens" value={formatInteger(tokenStats.completion)} icon={<Braces size={18} />} />
          <TokenMetric label="API Calls" value={formatInteger(tokenStats.calls)} icon={<Clock3 size={18} />} />
          <TokenMetric label="Estimated Cost" value={formatUsd(tokenStats.cost_usd)} icon={<WalletCards size={18} />} />
        </div>
      ) : (
        <p className="text-text-muted">暂无 token 统计。</p>
      )}
    </Card>
  );
}

export function VbaPanel({ vba }: { vba: string }) {
  return (
    <Card className="p-5 page-section">
      <h3 className="text-text font-bold mb-3">Last VBA Code</h3>
      {vba ? (
        <pre className="p-4 rounded-xl bg-elevated border border-border text-[0.82rem] overflow-x-auto font-mono whitespace-pre-wrap">
          {vba}
        </pre>
      ) : (
        <p className="text-text-muted">暂无 VBA 代码。</p>
      )}
    </Card>
  );
}

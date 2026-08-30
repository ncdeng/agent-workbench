import { type ReactNode, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Activity,
  BarChart3,
  ChevronDown,
  MoreHorizontal,
  Play,
  RadioTower,
  RotateCcw,
  Sparkles,
  Trash2,
  Zap,
} from 'lucide-react';
import type { Snapshot } from '../api';
import { StatusPill } from '../components/StatusPill';
import { MetricCard } from '../components/MetricCard';
import { S11Chart } from '../components/S11Chart';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { Alert } from '../components/Alert';
import { SkeletonMetricCard, SkeletonChart } from '../components/Skeleton';
import { formatNumber, toNumberOrNull } from '../utils/numbers';

export type ActionMessage = { tone: 'good' | 'bad' | 'warn'; text: string };
type Tone = 'good' | 'warn' | 'bad' | 'neutral';
type Translator = (key: string) => string;

export const DEMO_PROMPT = '创建一个 9.4GHz Rogers5880 矩形微带贴片天线，基板厚度 0.508mm，建模后运行仿真并读取 S11 和远场。';

function S11InkBlot() {
  return (
    <div className="relative w-24 h-16 mb-5">
      <div className="absolute inset-0 rounded-[60%_40%_50%_70%/50%_60%_40%_50%] bg-text-muted/20 blur-sm animate-pulse-soft" />
      <div className="absolute inset-2 rounded-[40%_60%_70%_30%/60%_40%_60%_40%] bg-text-muted/12 blur-[2px]" />
    </div>
  );
}

function S11EmptyState({ onUseDemoPrompt, t }: { onUseDemoPrompt?: () => void; t: Translator }) {
  return (
    <div className="flex flex-col items-center justify-center h-[clamp(22rem,48vh,34rem)] text-center px-6">
      <S11InkBlot />
      <span className="text-text-muted text-sm">{t('dash.s11.noData')}</span>
      {onUseDemoPrompt && (
        <button onClick={onUseDemoPrompt} className="mt-5 btn btn-secondary">
          用演示 Prompt 开始
        </button>
      )}
    </div>
  );
}

function fmtDb(v: unknown) { return formatNumber(v, 2, ' dB'); }
function fmtFreq(v: unknown) { return formatNumber(v, 4, ' GHz'); }

export function metricTone(value: unknown, target: unknown): Tone {
  const numeric = toNumberOrNull(value);
  const targetValue = toNumberOrNull(target);
  if (numeric === null || targetValue === null) return 'neutral';
  if (numeric <= targetValue) return 'good';
  if (numeric <= targetValue + 3) return 'warn';
  return 'bad';
}

function MobileActionsMenu({
  children,
  disabled,
}: {
  children: ReactNode;
  disabled: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative md:hidden">
      <button
        onClick={() => setOpen(prev => !prev)}
        disabled={disabled}
        className="btn btn-secondary"
      >
        <MoreHorizontal size={16} />
        更多
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-2 min-w-[180px] p-2 rounded-xl border border-border bg-surface shadow-lg z-50 flex flex-col gap-2">
          {children}
        </div>
      )}
    </div>
  );
}

export function DashboardHeader({
  snapshot,
  t,
  actionLoading,
  actionMessage,
  snapshotError,
  onReconnect,
  onSimulate,
  onReadS11,
  onOptimize,
  onRollback,
  onClear,
  onDismissActionMessage,
  onDismissSnapshotError,
}: {
  snapshot: Snapshot;
  t: Translator;
  actionLoading: string | null;
  actionMessage: ActionMessage | null;
  snapshotError: string | null;
  onReconnect: () => void;
  onSimulate: () => void;
  onReadS11: () => void;
  onOptimize: () => void;
  onRollback: () => void;
  onClear: () => void;
  onDismissActionMessage: () => void;
  onDismissSnapshotError: () => void;
}) {
  const statusTone = snapshot.project.connected ? 'good' : snapshot.project.offlineMode ? 'neutral' : 'warn';
  const modeTone = snapshot.project.offlineMode ? 'neutral' : snapshot.project.connected ? 'good' : 'warn';
  const modeLabel = snapshot.project.offlineMode
    ? t('dash.demoMode')
    : snapshot.project.connected
      ? t('dash.liveMode')
      : t('dash.livePending');
  const strategyLabels: Record<string, string> = {
    fast_path: 'Fast Path',
    native_loop: 'Native Tool Loop',
    pi_harness: 'Pi Harness',
  };
  const strategyLabel = strategyLabels[snapshot.execution.executionStrategy || ''] || t('chat.notRun');

  return (
    <section className="workspace-strip page-section" aria-label="CST workspace status">
      <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-4">
        <div className="flex flex-wrap items-center gap-2.5 min-w-0">
          <StatusPill tone={statusTone}>
            {snapshot.project.connected ? t('dash.connected') : t('dash.offline')}
          </StatusPill>
          <StatusPill tone={modeTone}>{modeLabel}</StatusPill>
          <StatusPill tone={snapshot.execution.agentBrain === 'pi' ? 'good' : 'neutral'}>
            Brain · {snapshot.execution.agentBrain === 'pi' ? 'Pi' : 'Native'}
          </StatusPill>
          <StatusPill tone={snapshot.execution.executionStrategy ? 'good' : 'neutral'}>
            {strategyLabel}
          </StatusPill>
          <span className="mono-chip">
            <Activity size={13} />
            {snapshot.trace.status || 'idle'}
          </span>
          <span className="hidden sm:inline-flex items-center gap-2 min-w-0 px-3 py-1.5 rounded-lg border border-border bg-elevated text-text-secondary text-sm">
            <span className="text-text-muted uppercase tracking-[0.08em] text-xs">
              {t('dash.projectPath')}
            </span>
            <span className="font-mono truncate max-w-[min(42vw,640px)]">
              {snapshot.project.path || t('dash.noProject')}
            </span>
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {/* Primary actions always visible */}
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" onClick={onReadS11} disabled={actionLoading !== null}>
              <BarChart3 size={15} />
              {actionLoading === 'readS11' ? t('dash.reading') : t('dash.readS11')}
            </Button>
            <Button variant="primary" onClick={onOptimize} disabled={actionLoading !== null}>
              <Zap size={15} />
              {actionLoading === 'optimize' ? t('dash.optimizing') : t('dash.optimize')}
            </Button>
          </div>

          {/* Secondary actions: desktop inline */}
          <div className="hidden md:flex flex-wrap items-center gap-2">
            <span className="w-px h-6 bg-border mx-1" />
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" onClick={onReconnect} disabled={actionLoading !== null}>
                <RadioTower size={15} />
                {actionLoading === 'reconnect' ? t('dash.reconnecting') : t('dash.reconnect')}
              </Button>
              <Button variant="secondary" onClick={onSimulate} disabled={actionLoading !== null}>
                <Play size={15} />
                {actionLoading === 'simulate' ? t('dash.simulating') : t('dash.simulate')}
              </Button>
            </div>

            <span className="w-px h-6 bg-border mx-1" />

            <div className="flex flex-wrap gap-2">
              {snapshot.optimization.bestRound > 0 && (
                <Button variant="secondary" onClick={onRollback} disabled={actionLoading !== null}>
                  <RotateCcw size={15} />
                  {t('dash.rollback')}
                </Button>
              )}
              <Button variant="danger" onClick={onClear} disabled={actionLoading !== null}>
                <Trash2 size={15} />
                {t('dash.clear')}
              </Button>
            </div>
          </div>

          {/* Secondary actions: mobile dropdown */}
          <MobileActionsMenu disabled={actionLoading !== null}>
            <Button variant="secondary" onClick={onReconnect} disabled={actionLoading !== null} className="w-full justify-start">
              <RadioTower size={15} />
              {actionLoading === 'reconnect' ? t('dash.reconnecting') : t('dash.reconnect')}
            </Button>
            <Button variant="secondary" onClick={onSimulate} disabled={actionLoading !== null} className="w-full justify-start">
              <Play size={15} />
              {actionLoading === 'simulate' ? t('dash.simulating') : t('dash.simulate')}
            </Button>
            {snapshot.optimization.bestRound > 0 && (
              <Button variant="secondary" onClick={onRollback} disabled={actionLoading !== null} className="w-full justify-start">
                <RotateCcw size={15} />
                {t('dash.rollback')}
              </Button>
            )}
            <Button variant="danger" onClick={onClear} disabled={actionLoading !== null} className="w-full justify-start">
              <Trash2 size={15} />
              {t('dash.clear')}
            </Button>
          </MobileActionsMenu>
        </div>
      </div>

      {(snapshotError || actionMessage) && (
        <div className="mt-4 space-y-3">
          {snapshotError && (
            <Alert variant="warning" onDismiss={onDismissSnapshotError}>
              {t('dash.refreshFailed')}: {snapshotError}
            </Alert>
          )}
          {actionMessage && (
            <Alert
              variant={actionMessage.tone === 'good' ? 'success' : actionMessage.tone === 'warn' ? 'warning' : 'danger'}
              onDismiss={onDismissActionMessage}
            >
              {actionMessage.text}
            </Alert>
          )}
        </div>
      )}
    </section>
  );
}

export function DashboardMetricColumn({
  snapshot,
  loading,
  t,
}: {
  snapshot: Snapshot;
  loading: boolean;
  t: Translator;
}) {
  if (loading) {
    return (
      <section className="grid grid-cols-1 sm:grid-cols-3 gap-3 page-section">
        <SkeletonMetricCard />
        <SkeletonMetricCard />
        <SkeletonMetricCard />
      </section>
    );
  }

  return (
    <section className="grid grid-cols-1 sm:grid-cols-3 gap-3 page-section" aria-label="Current design facts">
      <MetricCard
        label={t('dash.target')}
        value={formatNumber(snapshot.optimization.targetDb, 1, ' dB')}
        caption={`${snapshot.optimization.targetMode} @ ${formatNumber(snapshot.optimization.targetFreqGhz, 3, ' GHz')}`}
        tone="neutral"
      />
      <MetricCard
        label={t('dash.bestMetric')}
        value={fmtDb(snapshot.optimization.bestMetricValue)}
        caption={`R${snapshot.optimization.bestRound}`}
        tone={metricTone(snapshot.optimization.bestMetricValue, snapshot.optimization.targetDb)}
      />
      <MetricCard
        label={t('dash.modelAssets')}
        value={`${snapshot.model.objectCount} / ${snapshot.model.portCount}`}
        caption={`${snapshot.model.parameterCount} ${t('dash.params')}`}
        tone={snapshot.model.objectCount > 0 || snapshot.model.portCount > 0 ? 'good' : 'neutral'}
      />

    </section>
  );
}

export function DashboardS11Panel({
  snapshot,
  loading,
  t,
  onUseDemoPrompt,
}: {
  snapshot: Snapshot;
  loading: boolean;
  t: Translator;
  onUseDemoPrompt?: () => void;
}) {
  const noData = !snapshot.results.available || !Array.isArray(snapshot.results.plotData) || snapshot.results.plotData.length === 0;

  return (
    <Card className="p-4 space-y-3 page-section liquid-sheen">
      <div className="flex items-start justify-between gap-3">
        <div>
          <span className="kicker">{t('dash.s11.subtitle')}</span>
          <h2 className="mt-1 text-text text-xl font-bold tracking-tight">{t('dash.s11.title')}</h2>
        </div>
        <StatusPill tone={metricTone(snapshot.results.targetS11Db, snapshot.optimization.targetDb)}>
          {fmtDb(snapshot.results.targetS11Db)}
        </StatusPill>
      </div>

      <div className="rounded-xl overflow-hidden border border-border bg-surface">
        {loading ? (
          <SkeletonChart height={400} />
        ) : !noData ? (
          <S11Chart
            plotData={snapshot.results.plotData}
            targetFreq={snapshot.optimization.targetFreqGhz}
            targetDb={snapshot.optimization.targetDb}
            height="clamp(22rem, 48vh, 34rem)"
          />
        ) : (
          <S11EmptyState onUseDemoPrompt={onUseDemoPrompt} t={t} />
        )}
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <MetricCard label={t('dash.s11.minimum')} value={fmtDb(snapshot.results.minS11Db)} caption={fmtFreq(snapshot.results.minFreqGhz)} tone="warn" />
        <MetricCard label={t('dash.s11.target')} value={fmtDb(snapshot.results.targetS11Db)} caption={formatNumber(snapshot.optimization.targetFreqGhz, 3, ' GHz')} tone={metricTone(snapshot.results.targetS11Db, snapshot.optimization.targetDb)} />
        <MetricCard label={t('dash.s11.points')} value={`${snapshot.results.points}`} caption={t('dash.s11.ptsLabel')} />
      </div>
    </Card>
  );
}

export function DashboardEvidencePanel({
  snapshot,
  steps,
  t,
}: {
  snapshot: Snapshot;
  steps: string[];
  t: Translator;
}) {
  const latestEvent = snapshot.recentEvents[snapshot.recentEvents.length - 1];
  const [expanded, setExpanded] = useState(false);
  const isEmpty =
    snapshot.trace.toolCalls === 0 &&
    snapshot.recentEvents.length === 0 &&
    snapshot.optimization.round === 0 &&
    !snapshot.optimization.lastStrategy;

  const emptyHint = (
    <p className="text-text-muted text-sm">{t('dash.evidence.noEvents')}</p>
  );

  return (
    <Card className={`p-4 page-section transition-all duration-300 ${isEmpty ? 'space-y-3' : 'space-y-4'}`}>
      <div className="flex items-center justify-between gap-2">
        <div>
          <span className="kicker">{t('dash.evidence.subtitle')}</span>
          <h2 className="mt-1 text-text text-lg font-bold tracking-tight">{t('dash.evidence.title')}</h2>
        </div>
        <StatusPill tone={snapshot.trace.failedToolCalls > 0 ? 'bad' : 'good'}>
          {snapshot.trace.toolCalls} {t('dash.calls')}
        </StatusPill>
      </div>

      {isEmpty ? (
        <div className="space-y-3">
          {emptyHint}
          <button
            onClick={() => setExpanded(prev => !prev)}
            className="btn btn-ghost w-full justify-between text-sm"
          >
            <span>{expanded ? '收起' : '展开详情'}</span>
            <ChevronDown size={16} className={`transition-transform duration-200 ${expanded ? 'rotate-180' : ''}`} />
          </button>
        </div>
      ) : null}

      {(expanded || !isEmpty) && (
        <div className="space-y-4 animate-fade-in">
          <div className="space-y-2">
            {steps.map((step, index) => {
              const done = index <= Math.min(snapshot.optimization.round, 4);
              return (
                <div key={step} className="flex items-center gap-3">
                  <span
                    className={`w-7 h-7 rounded-lg grid place-items-center text-xs font-mono font-semibold border ${
                      done
                        ? 'text-primary border-primary/25 bg-primary-subtle'
                        : 'text-text-muted border-border bg-elevated'
                    }`}
                  >
                    {String(index + 1).padStart(2, '0')}
                  </span>
                  <span className={done ? 'text-text text-sm font-semibold' : 'text-text-secondary text-sm'}>
                    {step}
                  </span>
                </div>
              );
            })}
          </div>

          <div className="grid grid-cols-2 gap-2">
            {[
              { label: t('dash.evidence.round'), value: `${snapshot.optimization.round}` },
              { label: t('dash.evidence.best'), value: `R${snapshot.optimization.bestRound}` },
              { label: t('dash.evidence.memory'), value: `${snapshot.optimization.lastMemoryRecallCount}` },
              { label: t('dash.failed'), value: `${snapshot.trace.failedToolCalls}` },
            ].map(item => (
              <div key={item.label} className="elevated p-3">
                <span className="text-text-muted text-xs uppercase tracking-[0.1em] font-semibold">
                  {item.label}
                </span>
                <strong className="block mt-1.5 text-text text-[1.1rem] font-bold">{item.value}</strong>
              </div>
            ))}
          </div>

          {snapshot.optimization.lastStrategy && (
            <div className="p-3 rounded-xl border border-primary/20 bg-primary-subtle">
              <span className="kicker text-primary">{t('dash.evidence.strategy')}</span>
              <strong className="block mt-1.5 text-text text-[0.95rem]">{snapshot.optimization.lastStrategy}</strong>
              {snapshot.optimization.lastRolledBack && (
                <p className="mt-2 text-warning text-[0.82rem]">{snapshot.optimization.lastRollbackReason}</p>
              )}
            </div>
          )}

          <div className="space-y-2.5">
            <div className="flex items-center justify-between">
              <span className="kicker">{t('dash.evidence.recent')}</span>
              <Link to="/trace" className="text-sm text-primary hover:underline">
                Trace
              </Link>
            </div>
            {snapshot.recentEvents.length === 0 ? (
              <p className="elevated p-3 text-text-muted text-sm">{t('dash.evidence.noEvents')}</p>
            ) : (
              snapshot.recentEvents.slice(-5).reverse().map((evt, i) => (
                <div key={`${evt.tool}-${i}`} className="flex gap-3 p-3 rounded-xl bg-elevated border border-border">
                  <span
                    className={`w-2.5 h-2.5 mt-1.5 rounded-full flex-shrink-0 ${
                      evt.success ? 'bg-success' : 'bg-danger'
                    }`}
                  />
                  <div className="min-w-0">
                    <span className="text-text text-sm font-semibold truncate block">
                      {evt.phase || 'phase'} · {evt.tool || 'tool'}
                    </span>
                    <p className="text-text-secondary text-sm mt-1 line-clamp-2">{evt.description}</p>
                  </div>
                </div>
              ))
            )}
          </div>

          {latestEvent && (
            <div className="mono-chip w-full justify-between">
              <span>latest</span>
              <span className="truncate">{latestEvent.tool || latestEvent.phase}</span>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

export function DashboardWelcome({ onUseDemoPrompt }: { onUseDemoPrompt: () => void }) {
  return (
    <Card className="p-6 page-section" hover>
      <div className="flex items-start gap-4">
        <span className="w-11 h-11 rounded-xl grid place-items-center bg-primary-subtle text-primary border border-primary/20">
          <Sparkles size={22} />
        </span>
        <div>
          <h2 className="text-text text-lg font-bold">开始你的第一次天线设计</h2>
          <p className="mt-1 text-text-secondary text-[0.86rem] leading-relaxed">
            用自然语言描述目标，Agent 会调用 CST 完成建模、仿真与优化。
          </p>
          <Button variant="primary" className="mt-4" onClick={onUseDemoPrompt}>
            使用演示 Prompt
          </Button>
        </div>
      </div>
    </Card>
  );
}

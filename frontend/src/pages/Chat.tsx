import { useState, useRef, useEffect, useCallback } from 'react';
import {
  BarChart3,
  Cpu,
  Database,
  RefreshCw,
  SendHorizontal,
  Sparkles,
  Waves,
} from 'lucide-react';
import Markdown from 'react-markdown';
import { fetchS11, fetchFarfield, type S11Result, type FarfieldResult, type ToolEvent } from '../api';
import { S11Chart } from '../components/S11Chart';
import { FarfieldChart } from '../components/FarfieldChart';
import { StatusPill } from '../components/StatusPill';
import { Button } from '../components/Button';
import { Card } from '../components/Card';
import { Alert } from '../components/Alert';
import { ToolApprovalCard } from '../components/ToolApprovalCard';
import { useI18n } from '../hooks/useI18n';
import { useChatSession } from '../context/ChatSessionContext';
import { useSnapshot } from '../hooks/useSnapshot';
import { formatNumber, toNumberOrNull } from '../utils/numbers';

const DEMO_PROMPTS = [
  '创建一个 9.4GHz Rogers5880 矩形微带贴片天线，基板厚度 0.508mm，建模后运行仿真并读取 S11 和远场。',
  '沿用上一版材料和馈电方式，只把中心频率改成 5.8GHz，重新建模、仿真并比较 S11。',
];

function ToolEventList({ events, live }: { events: ToolEvent[]; live?: boolean }) {
  const safeEvents = Array.isArray(events) ? events : [];
  if (!safeEvents.length) return null;
  return (
    <ul className="space-y-2 mt-3" aria-live={live ? 'polite' : undefined}>
      {safeEvents.map((evt, i) => {
        const icon = evt.success ? 'OK' : 'ERR';
        const color = evt.success ? 'text-success' : 'text-danger';
        return (
          <li key={i} className="flex items-start gap-2.5 text-sm text-text-secondary">
            <span className={`font-mono text-xs ${color}`}>{icon}</span>
            <span className="flex-1 min-w-0">
              <span className="text-text font-medium">{evt.tool || evt.phase || 'tool'}</span>
              {evt.description ? <span className="text-text-muted">: {evt.description}</span> : null}
              {evt.approvalRequest ? <ToolApprovalCard request={evt.approvalRequest} /> : null}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

export function Chat() {
  const { messages, pendingEvents, sending, hydrated, draft, setDraft, sendMessage } = useChatSession();
  const [s11, setS11] = useState<S11Result | null>(null);
  const [farfield, setFarfield] = useState<FarfieldResult | null>(null);
  const [resultTab, setResultTab] = useState<'s11' | 'farfield'>('s11');
  const [resultError, setResultError] = useState<string | null>(null);
  const { snapshot } = useSnapshot(4000);
  const messagesViewportRef = useRef<HTMLDivElement>(null);
  const { t } = useI18n();
  const farfieldFrequency = toNumberOrNull(farfield?.frequencyGhz);

  useEffect(() => {
    const viewport = messagesViewportRef.current;
    if (!viewport) return;
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    viewport.scrollTo({ top: viewport.scrollHeight, behavior: reducedMotion ? 'auto' : 'smooth' });
  }, [messages, pendingEvents]);

  const refreshResults = useCallback(async () => {
    const [s11Result, farfieldResult] = await Promise.allSettled([fetchS11(), fetchFarfield()]);
    const failures: string[] = [];
    if (s11Result.status === 'fulfilled') setS11(s11Result.value);
    else failures.push(`S11: ${s11Result.reason instanceof Error ? s11Result.reason.message : String(s11Result.reason)}`);
    if (farfieldResult.status === 'fulfilled') setFarfield(farfieldResult.value);
    else failures.push(`Farfield: ${farfieldResult.reason instanceof Error ? farfieldResult.reason.message : String(farfieldResult.reason)}`);
    setResultError(failures.length ? failures.join('；') : null);
  }, []);

  useEffect(() => { refreshResults(); }, [refreshResults]);

  const prevSending = useRef(sending);
  useEffect(() => {
    if (prevSending.current && !sending) {
      refreshResults();
    }
    prevSending.current = sending;
  }, [sending, refreshResults]);

  const handleSend = useCallback(async () => {
    const text = draft.trim();
    if (!text || sending || !hydrated) return;
    setDraft('');
    await sendMessage(text);
  }, [draft, sending, hydrated, setDraft, sendMessage]);

  return (
    <div className="page-shell chat-workspace-grid">
      <Card className="chat-conversation flex min-w-0 flex-col overflow-hidden">
        <div className="px-4 sm:px-5 py-4 border-b border-border space-y-3">
          <div className="flex items-center justify-between gap-3">
            <h1 className="text-text text-[clamp(1.05rem,1.1vw,1.3rem)] font-bold tracking-tight">{t('chat.workspace')}</h1>
            <StatusPill tone={sending ? 'warn' : hydrated ? 'good' : 'neutral'}>
              {sending ? t('chat.running') : hydrated ? t('chat.ready') : t('chat.loading')}
            </StatusPill>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <StatusPill tone={snapshot.project.connected ? 'good' : snapshot.project.offlineMode ? 'neutral' : 'warn'}>
              {snapshot.project.connected ? t('dash.connected') : snapshot.project.offlineMode ? t('chat.demoMode') : t('dash.offline')}
            </StatusPill>
            <StatusPill tone={snapshot.execution.agentBrain === 'pi' ? 'good' : 'neutral'}>
              <Cpu size={13} /> Brain · {snapshot.execution.agentBrain === 'pi' ? 'Pi' : 'Native'}
            </StatusPill>
            <StatusPill tone={snapshot.execution.executionStrategy ? 'good' : 'neutral'}>
              {snapshot.execution.executionStrategy === 'fast_path'
                ? 'Fast Path'
                : snapshot.execution.executionStrategy === 'pi_harness'
                  ? 'Pi Harness'
                  : snapshot.execution.executionStrategy === 'native_loop'
                    ? 'Native Tool Loop'
                    : t('chat.notRun')}
            </StatusPill>
            <span className="min-w-0 flex-1 truncate font-mono text-xs text-text-muted sm:text-right">
              {snapshot.project.path || t('dash.noProject')}
            </span>
          </div>
        </div>

        <div ref={messagesViewportRef} className="flex-1 overflow-y-auto p-4 sm:p-5 space-y-5">
          {messages.length === 0 && !sending && (
            <div className="min-h-[clamp(24rem,55vh,42rem)] grid place-items-center">
              <section className="w-full max-w-[840px] px-2 sm:px-6">
                <div className="flex items-start gap-4">
                  <span className="w-11 h-11 rounded-xl grid place-items-center bg-primary-subtle text-primary border border-primary/20">
                    <Sparkles size={22} />
                  </span>
                  <div>
                    <h2 className="text-text text-lg font-bold">{t('chat.emptyTitle')}</h2>
                    <p className="mt-1 text-text-secondary text-base leading-relaxed">{t('chat.emptyHint')}</p>
                  </div>
                </div>
                <div className="mt-5 grid gap-2">
                  {DEMO_PROMPTS.map((prompt, index) => (
                    <button
                      key={prompt}
                    className="w-full rounded-lg border border-border bg-elevated px-4 py-3.5 text-left text-sm leading-relaxed text-text-secondary transition-colors hover:border-primary/30 hover:bg-primary-subtle hover:text-text"
                      onClick={() => setDraft(prompt)}
                      disabled={!hydrated}
                    >
                      <span className="text-text-muted font-mono mr-2">{String(index + 1).padStart(2, '0')}</span>
                      {prompt}
                    </button>
                  ))}
                </div>
              </section>
            </div>
          )}

          {messages.map((msg, i) => (
            <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div
                className={`max-w-[min(92%,920px)] rounded-xl px-4 sm:px-5 py-3.5 border ${
                  msg.role === 'user'
                    ? 'bg-primary text-white border-transparent'
                    : 'bg-elevated border-border'
                }`}
              >
                {msg.role === 'assistant' ? (
                  <>
                    <div className="prose prose-sm max-w-none prose-p:my-1 prose-pre:bg-surface prose-pre:border prose-pre:border-border prose-code:text-primary prose-headings:text-text">
                      <Markdown>{msg.content}</Markdown>
                    </div>
                    {msg.toolEvents && msg.toolEvents.length > 0 && (
                      <details className="mt-2.5">
                        <summary className="cursor-pointer text-sm text-text-muted hover:text-text-secondary">
                          {t('chat.toolEvents')} ({msg.toolEvents.length})
                        </summary>
                        <ToolEventList events={msg.toolEvents} />
                      </details>
                    )}
                  </>
                ) : (
                  <p className="text-white text-[0.96rem] leading-relaxed whitespace-pre-wrap">{msg.content}</p>
                )}
              </div>
            </div>
          ))}

          {sending && (
            <div className="flex justify-start">
              <div className="bg-elevated border border-border rounded-xl px-5 py-3.5 max-w-[min(92%,920px)]">
                <div className="flex items-center gap-2 text-text-secondary">
                  <span className="inline-block w-2 h-2 rounded-full bg-primary animate-pulse-soft" />
                  <span className="text-sm">{t('chat.thinking')}</span>
                </div>
                <ToolEventList events={pendingEvents} live />
              </div>
            </div>
          )}
        </div>

        <div className="border-t border-border p-4">
          <div className="flex items-end gap-3">
            <textarea
              value={draft}
              onChange={e => setDraft(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  handleSend();
                }
              }}
              placeholder={hydrated ? t('chat.placeholder') : t('chat.hydrating')}
              className="input min-h-14 max-h-36 resize-y py-3 leading-relaxed"
              disabled={sending || !hydrated}
            />
            <Button
              variant="primary"
              onClick={handleSend}
              disabled={sending || !hydrated || !draft.trim()}
              className="h-12 px-5"
            >
              <SendHorizontal size={17} />
              {t('chat.send')}
            </Button>
          </div>
        </div>
      </Card>

      <Card className="chat-inspector flex min-w-0 flex-col overflow-hidden">
        <div className="border-b border-border p-3 flex gap-2 items-center" role="tablist" aria-label="Simulation results">
          <button
            className={`btn ${resultTab === 's11' ? 'btn-secondary' : 'btn-ghost'}`}
            onClick={() => setResultTab('s11')}
            role="tab"
            aria-selected={resultTab === 's11'}
            aria-controls="s11-result-panel"
          >
            <BarChart3 size={15} />
            S11
          </button>
          <button
            className={`btn ${resultTab === 'farfield' ? 'btn-secondary' : 'btn-ghost'}`}
            onClick={() => setResultTab('farfield')}
            role="tab"
            aria-selected={resultTab === 'farfield'}
            aria-controls="farfield-result-panel"
          >
            <Waves size={15} />
            Farfield
          </button>
          <Button variant="ghost" className="ml-auto w-9 h-9 p-0" onClick={refreshResults} title={t('chat.refresh')}>
            <RefreshCw size={15} />
          </Button>
        </div>

        <div className="flex-1 overflow-y-auto p-3 sm:p-4">
          {resultError && (
            <div className="mb-3">
              <Alert variant="warning" onDismiss={() => setResultError(null)}>
                结果刷新失败：{resultError}
              </Alert>
            </div>
          )}

          {resultTab === 's11' && s11?.available && Array.isArray(s11.plotData) && s11.plotData.length > 0 ? (
            <div id="s11-result-panel" role="tabpanel" className="space-y-4">
              <S11Chart
                plotData={s11.plotData}
                targetFreq={s11.targetFreqGhz}
                targetDb={s11.targetDb}
                bands10Db={s11.summary?.bands10Db || undefined}
                height="clamp(18rem,38vh,26rem)"
              />
              {s11.summary && (
                <div className="grid grid-cols-2 gap-2.5 text-sm">
                  <div className="elevated p-3">
                    <span className="text-text-muted block">{t('chat.minS11')}</span>
                    <strong className="text-text">{formatNumber(s11.summary.minS11Db, 2, ' dB')}</strong>
                  </div>
                  <div className="elevated p-3">
                    <span className="text-text-muted block">{t('chat.minFreq')}</span>
                    <strong className="text-text">{formatNumber(s11.summary.minFreqGhz, 4, ' GHz')}</strong>
                  </div>
                  <div className="elevated p-3">
                    <span className="text-text-muted block">{t('chat.bw')}</span>
                    <strong className="text-text">{formatNumber(s11.summary.bandwidthGhz, 4, ' GHz')}</strong>
                  </div>
                  <div className="elevated p-3">
                    <span className="text-text-muted block">{t('chat.targetS11')}</span>
                    <strong className="text-text">{formatNumber(s11.summary.targetS11Db, 2, ' dB')}</strong>
                  </div>
                </div>
              )}
            </div>
          ) : resultTab === 's11' ? (
            <div className="flex flex-col items-center justify-center h-full text-text-secondary text-sm text-center px-6">
              <Database size={32} className="mb-3 text-text-muted opacity-50" />
              <span>{t('chat.noS11')}</span>
              <button
                onClick={() => setDraft(DEMO_PROMPTS[0])}
                disabled={!hydrated}
                className="mt-4 btn btn-secondary"
              >
                {t('chat.fillDemo')}
              </button>
            </div>
          ) : null}

          {resultTab === 'farfield' && farfield?.available && Array.isArray(farfield.plotData) && farfield.plotData.length > 0 ? (
            <div id="farfield-result-panel" role="tabpanel" className="space-y-4">
              <div className="text-sm text-text-secondary">
                <StatusPill tone="good">{farfield.cutType} cut @ {farfield.cutValueDeg}deg</StatusPill>
                {farfieldFrequency !== null && <span className="ml-2">{farfieldFrequency.toFixed(4)} GHz</span>}
              </div>
              <FarfieldChart
                plotData={farfield.plotData}
                title={farfield.title || 'Farfield cut'}
                height={340}
              />
              {farfield.summary && (
                <div className="elevated p-3 text-sm">
                  <span className="text-text-muted block">{t('chat.peakGain')}</span>
                  <strong className="text-text">
                    {formatNumber(farfield.summary.peakGainDbi, 2, ' dBi')} @ {formatNumber(farfield.summary.peakAngleDeg, 1, ' deg')}
                  </strong>
                </div>
              )}
              <div className="text-text-muted text-sm">
                {farfield.plotData.length} {t('chat.dataPoints')}
              </div>
            </div>
          ) : resultTab === 'farfield' ? (
            <div className="flex flex-col items-center justify-center h-full text-text-secondary text-sm text-center px-6">
              <Database size={32} className="mb-3 text-text-muted opacity-50" />
              <span>{t('chat.noFarfield')}</span>
              <button
                onClick={() => setDraft(DEMO_PROMPTS[0])}
                disabled={!hydrated}
                className="mt-4 btn btn-secondary"
              >
                {t('chat.fillDemo')}
              </button>
            </div>
          ) : null}
        </div>
      </Card>
    </div>
  );
}

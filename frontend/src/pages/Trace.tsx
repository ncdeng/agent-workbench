import { useState, useEffect, useCallback } from 'react';
import {
  fetchTraceList,
  fetchTraceDetail,
  fetchTokenStats,
  fetchVba,
  type TraceDetail,
  type TraceEntry,
  type TokenStats,
} from '../api';
import {
  TokenStatsPanel,
  TraceDetailPanel,
  TraceHeader,
  TraceLoadError,
  TraceRunsList,
  TraceSidePanel,
  VbaPanel,
  type TraceTab,
} from './TraceSections';

export function Trace() {
  const [traces, setTraces] = useState<TraceEntry[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<TraceDetail | null>(null);
  const [tokenStats, setTokenStats] = useState<TokenStats | null>(null);
  const [vba, setVba] = useState('');
  const [tab, setTab] = useState<TraceTab>('trace');
  const [loadError, setLoadError] = useState<string | null>(null);
  const [dismissedLoadError, setDismissedLoadError] = useState<string | null>(null);

  const loadTraces = useCallback(async () => {
    try {
      const data = await fetchTraceList();
      setTraces(data.traces);
      if (!selected && data.current) {
        setSelected(data.currentRunId || data.current.runId || null);
        setDetail(data.current);
      }
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : String(err));
    }
  }, [selected]);

  const loadDetail = useCallback(async (runId: string) => {
    try {
      const data = await fetchTraceDetail(runId);
      setDetail(data);
      setSelected(runId);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const loadTokens = useCallback(async () => {
    try {
      setTokenStats(await fetchTokenStats());
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const loadVba = useCallback(async () => {
    try {
      const d = await fetchVba();
      setVba(d.code);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    setDismissedLoadError(null);
  }, [loadError]);

  const displayLoadError = loadError && loadError !== dismissedLoadError ? loadError : null;

  const refreshAll = useCallback(() => {
    loadTraces();
    loadTokens();
    loadVba();
  }, [loadTraces, loadTokens, loadVba]);

  useEffect(() => { loadTraces(); }, [loadTraces]);
  useEffect(() => { loadTokens(); loadVba(); }, [loadTokens, loadVba]);

  return (
    <div className="page-shell space-y-5">
      <TraceHeader tab={tab} onTabChange={setTab} onRefresh={refreshAll} />

      {displayLoadError && (
        <TraceLoadError error={displayLoadError} onDismiss={() => setDismissedLoadError(loadError)} />
      )}

      {tab === 'trace' && (
        <div className="grid grid-cols-1 md:grid-cols-[280px_minmax(0,1fr)] lg:grid-cols-[280px_minmax(0,1fr)_320px] gap-5 h-[calc(100vh-180px)] min-h-[560px]">
          <TraceRunsList traces={traces} selected={selected} onSelect={loadDetail} />
          <TraceDetailPanel detail={detail} selected={selected} />
          <div className="hidden lg:block">
            <TraceSidePanel detail={detail} />
          </div>
        </div>
      )}

      {tab === 'tokens' && <TokenStatsPanel tokenStats={tokenStats} />}
      {tab === 'vba' && <VbaPanel vba={vba} />}
    </div>
  );
}

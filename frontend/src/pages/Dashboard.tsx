import { useState, useCallback, useEffect } from 'react';
import { useSnapshot } from '../hooks/useSnapshot';
import { useI18n } from '../hooks/useI18n';
import { cstReconnect, cstSimulate, cstReadS11, optimizeOnce, optimizeRollback } from '../api';
import { actionSucceeded } from '../api/core';
import { useChatSession } from '../context/ChatSessionContext';
import {
  DashboardEvidencePanel,
  DashboardHeader,
  DashboardMetricColumn,
  DashboardS11Panel,
  DEMO_PROMPT,
  type ActionMessage,
} from './DashboardSections';

function actionResultText(actionName: string, result: unknown): string {
  const fallback = `${actionName} 已完成`;
  if (!result || typeof result !== 'object') return fallback;
  const data = result as Record<string, unknown>;
  const message = typeof data.message === 'string' ? data.message : '';
  const error = typeof data.error === 'string' ? data.error : '';
  if (error) return error;
  if (message) return message;
  return fallback;
}

export function Dashboard() {
  const { snapshot, loading, error: snapshotError, refresh } = useSnapshot(4000);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<ActionMessage | null>(null);
  const [dismissedSnapshotError, setDismissedSnapshotError] = useState<string | null>(null);
  const { clearMessages, setDraft } = useChatSession();
  const { t } = useI18n();

  useEffect(() => {
    setDismissedSnapshotError(null);
  }, [snapshotError]);

  const runAction = useCallback(async (name: string, fn: () => Promise<any>) => {
    setActionLoading(name);
    setActionMessage(null);
    try {
      const result = await fn();
      const ok = actionSucceeded(result);
      setActionMessage({
        tone: ok ? 'good' : 'bad',
        text: actionResultText(name, result),
      });
      await refresh();
    } catch (e) {
      console.error(e);
      setActionMessage({
        tone: 'bad',
        text: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setActionLoading(null);
    }
  }, [refresh]);

  const handleClear = useCallback(async () => {
    setActionLoading('clear');
    setActionMessage(null);
    try {
      await clearMessages();
      setDraft('');
      setActionMessage({ tone: 'good', text: t('dash.cleared') });
      await refresh();
    } catch (e) {
      console.error(e);
      setActionMessage({
        tone: 'bad',
        text: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setActionLoading(null);
    }
  }, [clearMessages, refresh, setDraft, t]);

  const steps = [
    t('dash.step.task'),
    t('dash.step.model'),
    t('dash.step.simulate'),
    t('dash.step.optimize'),
    t('dash.step.evidence'),
  ];

  const displaySnapshotError = snapshotError && snapshotError !== dismissedSnapshotError ? snapshotError : null;

  return (
    <div className="page-shell ink-wash-bg space-y-4">
      <DashboardHeader
        snapshot={snapshot}
        t={t}
        actionLoading={actionLoading}
        actionMessage={actionMessage}
        snapshotError={displaySnapshotError}
        onReconnect={() => runAction('reconnect', cstReconnect)}
        onSimulate={() => runAction('simulate', cstSimulate)}
        onReadS11={() => runAction('readS11', cstReadS11)}
        onOptimize={() => runAction('optimize', optimizeOnce)}
        onRollback={() => runAction('rollback', () => optimizeRollback('best'))}
        onClear={handleClear}
        onDismissActionMessage={() => setActionMessage(null)}
        onDismissSnapshotError={() => setDismissedSnapshotError(snapshotError)}
      />

      <div className="dashboard-workspace-grid">
        <div className="min-w-0 space-y-4">
          <DashboardMetricColumn
            snapshot={snapshot}
            loading={loading}
            t={t}
          />
          <DashboardS11Panel
            snapshot={snapshot}
            loading={loading}
            t={t}
            onUseDemoPrompt={() => setDraft(DEMO_PROMPT)}
          />
        </div>
        <DashboardEvidencePanel snapshot={snapshot} steps={steps} t={t} />
      </div>
    </div>
  );
}

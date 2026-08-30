import { BASE, asArray, asBoolean, asNullableNumber, asNumber, asRecord, normalizeToolEvent, readJsonOrThrow } from './core';
import { DEFAULT_SNAPSHOT, type Snapshot } from './types';

function normalizeSnapshot(value: unknown): Snapshot {
  const data = asRecord(value);
  const project = asRecord(data.project);
  const execution = asRecord(data.execution);
  const optimization = asRecord(data.optimization);
  const results = asRecord(data.results);
  const model = asRecord(data.model);
  const trace = asRecord(data.trace);

  return {
    generatedAt: typeof data.generatedAt === 'string' ? data.generatedAt : DEFAULT_SNAPSHOT.generatedAt,
    project: {
      connected: asBoolean(project.connected),
      offlineMode: asBoolean(project.offlineMode),
      path: typeof project.path === 'string' ? project.path : '',
    },
    execution: {
      agentBrain: execution.agentBrain === 'pi' ? 'pi' : 'native',
      executionStrategy:
        execution.executionStrategy === 'fast_path'
        || execution.executionStrategy === 'native_loop'
        || execution.executionStrategy === 'pi_harness'
          ? execution.executionStrategy
          : null,
    },
    optimization: {
      active: asBoolean(optimization.active),
      round: asNumber(optimization.round, 0),
      bestRound: asNumber(optimization.bestRound, 0),
      bestMetricValue: asNullableNumber(optimization.bestMetricValue),
      targetMode: typeof optimization.targetMode === 'string' ? optimization.targetMode : 'at_f0',
      targetFreqGhz: asNumber(optimization.targetFreqGhz, 9.4),
      targetDb: asNumber(optimization.targetDb, -10),
      lastStrategy: typeof optimization.lastStrategy === 'string' ? optimization.lastStrategy : '',
      lastRolledBack: asBoolean(optimization.lastRolledBack),
      lastRollbackReason: typeof optimization.lastRollbackReason === 'string' ? optimization.lastRollbackReason : '',
      lastMemoryRecallCount: asNumber(optimization.lastMemoryRecallCount, 0),
      lastMemoryEnforced: asBoolean(optimization.lastMemoryEnforced),
    },
    results: {
      available: asBoolean(results.available),
      minS11Db: asNullableNumber(results.minS11Db),
      minFreqGhz: asNullableNumber(results.minFreqGhz),
      targetS11Db: asNullableNumber(results.targetS11Db),
      points: asNumber(results.points, 0),
      bandwidthGhz: asNullableNumber(results.bandwidthGhz),
      plotData: asArray(results.plotData),
    },
    model: {
      objectCount: asNumber(model.objectCount, 0),
      portCount: asNumber(model.portCount, 0),
      parameterCount: asNumber(model.parameterCount, 0),
    },
    trace: {
      status: typeof trace.status === 'string' ? trace.status : 'idle',
      runId: typeof trace.runId === 'string' ? trace.runId : '',
      toolCalls: asNumber(trace.toolCalls, 0),
      failedToolCalls: asNumber(trace.failedToolCalls, 0),
    },
    recentEvents: asArray(data.recentEvents).map(normalizeToolEvent),
  };
}

export async function fetchSnapshot(): Promise<Snapshot> {
  const res = await fetch(`${BASE}/api/dashboard/snapshot`);
  return normalizeSnapshot(await readJsonOrThrow(res));
}

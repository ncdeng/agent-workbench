export type Snapshot = {
  generatedAt: string;
  project: { connected: boolean; offlineMode: boolean; path: string };
  execution: {
    agentBrain: 'native' | 'pi';
    executionStrategy: 'fast_path' | 'native_loop' | 'pi_harness' | null;
  };
  optimization: {
    active: boolean; round: number; bestRound: number;
    bestMetricValue: number | null; targetMode: string;
    targetFreqGhz: number; targetDb: number;
    lastStrategy: string; lastRolledBack: boolean;
    lastRollbackReason: string; lastMemoryRecallCount: number;
    lastMemoryEnforced: boolean;
  };
  results: {
    available: boolean; minS11Db: number | null; minFreqGhz: number | null;
    targetS11Db: number | null; points: number; bandwidthGhz: number | null;
    plotData: Array<{ freq: number; s_db: number }>;
  };
  model: { objectCount: number; portCount: number; parameterCount: number };
  trace: { status: string; runId: string; toolCalls: number; failedToolCalls: number };
  recentEvents: Array<{ phase: string; tool: string; success: boolean; description: string }>;
};

export type S11Result = {
  available: boolean;
  plotData: Array<{ freq: number; s_db: number }>;
  summary: {
    minS11Db: number | null; minFreqGhz: number | null;
    targetS11Db: number | null; bandwidthGhz: number | null;
    bandwidthPct: number | null; primaryBand: [number, number] | null;
    bands10Db: Array<[number, number]> | null;
  } | null;
  targetFreqGhz: number;
  targetDb: number;
};

export type FarfieldResult = {
  available: boolean;
  plotData: Array<{ angle_deg: number; gain_dbi: number }>;
  title: string; cutType: string; cutValueDeg: number; frequencyGhz: number | null;
  summary: {
    peakGainDbi: number | null;
    peakAngleDeg: number | null;
    angleRange: [number, number] | null;
    points: number;
  } | null;
};

export type ToolEvent = {
  phase: string;
  tool: string;
  success: boolean;
  description: string;
  errorType?: string;
  approvalRequest?: ToolApprovalRequest;
};

export type ToolApprovalRequest = {
  requestId: string;
  toolName: string;
  argumentsSha256: string;
  createdAt: string;
  expiresAt: string;
  status: string;
  preview: string;
};

export type ToolApprovalDecision = {
  approved?: boolean;
  rejected?: boolean;
  executed?: boolean;
  request: ToolApprovalRequest;
  result?: Record<string, unknown>;
};

export type ChatHistoryMessage = {
  role: 'user' | 'assistant';
  content: string;
  images?: number;
  toolEvents?: ToolEvent[];
};

export type StreamCallbacks = {
  onStart?: () => void;
  onToolEvent?: (event: ToolEvent) => void;
  onReply?: (content: string) => void;
  onError?: (message: string) => void;
  onDone?: () => void;
};

export type TraceEntry = {
  runId: string; status: string; turnCount: number;
  toolCallCount: number; decisionSummary: string;
};

export type TracePlanSummary = {
  intent_kind?: string;
  completed_step_count?: number;
  step_count?: number;
  replan_count?: number;
  stop_reason?: string;
  [key: string]: unknown;
};

export type TraceDecisionSummary = {
  run_outcome?: string;
  final_action?: string;
  decision_result?: string;
  assistant_action?: string;
  observation_summary?: string;
  plan_summary?: TracePlanSummary;
  [key: string]: unknown;
};

export type TraceRecoveryResult = {
  action_name: string;
  recovered: boolean;
  retry_success?: boolean;
  message: string;
  retry_tool?: string;
  retry_arguments?: Record<string, unknown> | null;
  details?: Record<string, unknown>;
};

export type TraceToolCall = {
  tool_name: string;
  name: string;
  phase: string;
  success: boolean | null;
  result_preview: string;
  status_label: string;
  error: string;
  recovery_result?: TraceRecoveryResult;
};

export type TraceTurn = {
  tool_calls: TraceToolCall[];
  decision_summary: TraceDecisionSummary;
};

export type TraceDetail = {
  runId: string;
  status: string;
  startedAt: string | null;
  finishedAt: string | null;
  turnCount: number;
  toolCallCount: number;
  turns: TraceTurn[];
  planState: Record<string, unknown>;
  snapshotEntry: Record<string, unknown>;
  snapshotExit: Record<string, unknown>;
  runMetrics: Record<string, unknown>;
  decisionSummary: string;
  decisionSummaryRaw: TraceDecisionSummary;
  finalResponse: { preview: string; [key: string]: unknown };
  error: string;
  tokenDelta: Record<string, unknown>;
};

export type TraceListResponse = {
  currentRunId: string;
  traces: TraceEntry[];
  current: TraceDetail | null;
};

export type VbaResponse = { code: string };

export type TokenStats = {
  prompt: number; completion: number; total: number;
  calls: number; cost_usd: number;
};

export const DEFAULT_SNAPSHOT: Snapshot = {
  generatedAt: new Date().toISOString(),
  project: { connected: false, offlineMode: true, path: '' },
  execution: { agentBrain: 'native', executionStrategy: null },
  optimization: {
    active: false, round: 0, bestRound: 0, bestMetricValue: null,
    targetMode: 'at_f0', targetFreqGhz: 9.4, targetDb: -10,
    lastStrategy: '', lastRolledBack: false, lastRollbackReason: '',
    lastMemoryRecallCount: 0, lastMemoryEnforced: false,
  },
  results: {
    available: false, minS11Db: null, minFreqGhz: null,
    targetS11Db: null, points: 0, bandwidthGhz: null, plotData: [],
  },
  model: { objectCount: 0, portCount: 0, parameterCount: 0 },
  trace: { status: 'idle', runId: '', toolCalls: 0, failedToolCalls: 0 },
  recentEvents: [],
};

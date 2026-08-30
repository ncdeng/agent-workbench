import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  fetchSnapshot,
  fetchS11,
  fetchFarfield,
  fetchChatHistory,
  fetchTraceDetail,
  fetchTraceList,
  fetchVba,
  streamChat,
  cstReconnect,
  cstSimulate,
  cstReadS11,
  decideToolApproval,
  fetchTokenStats,
  type ToolEvent,
} from '../api';

const mockFetch = vi.fn();

beforeEach(() => {
  mockFetch.mockReset();
  vi.stubGlobal('fetch', mockFetch);
});

function jsonResponse(data: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(data),
  };
}

function errorResponse(status: number) {
  return {
    ok: false,
    status,
    json: () => Promise.resolve({ error: `HTTP ${status}` }),
  };
}

describe('fetchSnapshot', () => {
  it('returns parsed snapshot on success', async () => {
    const snapshot = {
      generatedAt: '2026-05-01T00:00:00Z',
      project: { connected: true, offlineMode: false, path: '/test.cst' },
      execution: { agentBrain: 'pi', executionStrategy: 'pi_harness' },
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
    mockFetch.mockResolvedValueOnce(jsonResponse(snapshot));

    const result = await fetchSnapshot();
    expect(result.project.connected).toBe(true);
    expect(result.execution).toEqual({ agentBrain: 'pi', executionStrategy: 'pi_harness' });
    expect(result.optimization.targetFreqGhz).toBe(9.4);
    expect(mockFetch).toHaveBeenCalledWith('/api/dashboard/snapshot');
  });

  it('normalizes malformed snapshots to a renderable shape', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({
      project: { connected: 'true' },
      optimization: { round: '3', bestMetricValue: 'bad' },
      results: { available: 1, plotData: null },
      model: { objectCount: '2' },
      trace: { toolCalls: '4' },
      recentEvents: [{ tool_name: 'run_solver', success: 'false', message: 'failed' }],
    }));

    const result = await fetchSnapshot();

    expect(result.project.connected).toBe(true);
    expect(result.project.offlineMode).toBe(false);
    expect(result.execution).toEqual({ agentBrain: 'native', executionStrategy: null });
    expect(result.optimization.round).toBe(3);
    expect(result.optimization.bestMetricValue).toBeNull();
    expect(result.results.available).toBe(true);
    expect(result.results.plotData).toEqual([]);
    expect(result.model.objectCount).toBe(2);
    expect(result.trace.toolCalls).toBe(4);
    expect(result.recentEvents).toEqual([
      { phase: '', tool: 'run_solver', success: false, description: 'failed' },
    ]);
  });

  it('rejects unknown execution identity values without exposing harness internals', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({
      execution: { agentBrain: 'sidecar', executionStrategy: 'heartbeat', generation: 4 },
    }));

    const result = await fetchSnapshot();

    expect(result.execution).toEqual({ agentBrain: 'native', executionStrategy: null });
    expect('generation' in result.execution).toBe(false);
  });

  it('throws on non-ok response', async () => {
    mockFetch.mockResolvedValueOnce(errorResponse(500));
    await expect(fetchSnapshot()).rejects.toThrow('HTTP 500');
  });

  it('throws backend detail payloads instead of a generic status', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 503,
      json: () => Promise.resolve({ detail: 'CST not connected' }),
    });
    await expect(fetchSnapshot()).rejects.toThrow('CST not connected');
  });
});

describe('fetchS11', () => {
  it('returns S11 data', async () => {
    const s11 = {
      available: true,
      plotData: [{ freq: 9.4, s_db: -15.2 }],
      summary: {
        minS11Db: -15.2, minFreqGhz: 9.4, targetS11Db: -12.0,
        bandwidthGhz: 0.3, bandwidthPct: 3.2, primaryBand: [9.25, 9.55] as [number, number],
        bands10Db: [[9.25, 9.55]] as Array<[number, number]>,
      },
      targetFreqGhz: 9.4,
      targetDb: -10,
    };
    mockFetch.mockResolvedValueOnce(jsonResponse(s11));

    const result = await fetchS11();
    expect(result.available).toBe(true);
    expect(result.plotData).toHaveLength(1);
    expect(result.summary?.minS11Db).toBe(-15.2);
  });

  it('normalizes malformed S11 payloads to a renderable shape', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ available: true, plotData: null }));

    const result = await fetchS11();
    expect(result.available).toBe(true);
    expect(result.plotData).toEqual([]);
    expect(result.summary).toBeNull();
    expect(result.targetDb).toBe(-10);
  });
});

describe('fetchFarfield', () => {
  it('returns farfield data', async () => {
    const ff = {
      available: true,
      plotData: [{ angle_deg: 0, gain_dbi: 8.5 }],
      title: 'Farfield', cutType: 'phi', cutValueDeg: 0, frequencyGhz: 9.4,
      summary: { peakGainDbi: 8.5, peakAngleDeg: 0 },
    };
    mockFetch.mockResolvedValueOnce(jsonResponse(ff));

    const result = await fetchFarfield();
    expect(result.available).toBe(true);
    expect(result.summary?.peakGainDbi).toBe(8.5);
  });

  it('normalizes malformed farfield payloads to a renderable shape', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ available: true, plotData: 'bad' }));

    const result = await fetchFarfield();
    expect(result.available).toBe(true);
    expect(result.plotData).toEqual([]);
    expect(result.cutType).toBe('phi');
    expect(result.summary).toBeNull();
  });
});

describe('CST action endpoints', () => {
  it('cstReconnect calls POST /api/cst/reconnect', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ success: true }));
    const result = await cstReconnect();
    expect(result.success).toBe(true);
    expect(mockFetch).toHaveBeenCalledWith('/api/cst/reconnect', { method: 'POST' });
  });

  it('cstSimulate calls POST /api/cst/simulate', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ success: true }));
    const result = await cstSimulate();
    expect(result.success).toBe(true);
    expect(mockFetch).toHaveBeenCalledWith('/api/cst/simulate', { method: 'POST' });
  });

  it('cstReadS11 calls POST /api/cst/read-s11', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ success: true }));
    const result = await cstReadS11();
    expect(result.success).toBe(true);
    expect(mockFetch).toHaveBeenCalledWith('/api/cst/read-s11', { method: 'POST' });
  });
});

describe('tool approval API', () => {
  it('approves a server-owned request with a bounded ttl', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({
      approved: true,
      executed: true,
      request: {
        request_id: 'req-1',
        tool_name: 'execute_vba_script',
        arguments_sha256: 'a'.repeat(64),
        created_at: '2026-08-12T00:00:00Z',
        expires_at: '2026-08-12T00:10:00Z',
        status: 'approved',
        preview: 'Sub Main()',
      },
    }));

    const result = await decideToolApproval('req-1', 'approve');

    expect(result.approved).toBe(true);
    expect(result.executed).toBe(true);
    expect(result.request.toolName).toBe('execute_vba_script');
    expect(mockFetch).toHaveBeenCalledWith('/api/approvals/req-1/approve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ttl_seconds: 120 }),
    });
  });
});

function streamResponse(chunks: Uint8Array[]) {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk);
      controller.close();
    },
  });
  return { ok: true, status: 200, body: stream };
}

describe('fetchChatHistory', () => {
  it('returns messages array', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({
      messages: [
        { role: 'user', content: 'hi' },
        { role: 'assistant', content: 'hello', toolEvents: [{ phase: 'p', tool: 't', success: true, description: 'd' }] },
      ],
    }));
    const result = await fetchChatHistory();
    expect(result).toHaveLength(2);
    expect(result[1].toolEvents?.[0].tool).toBe('t');
    expect(mockFetch).toHaveBeenCalledWith('/api/chat/history');
  });

  it('returns empty array when messages missing', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({}));
    const result = await fetchChatHistory();
    expect(result).toEqual([]);
  });

  it('normalizes malformed history messages', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({
      messages: [
        { role: 'weird', content: null, toolEvents: 'bad' },
      ],
    }));
    const result = await fetchChatHistory();
    expect(result).toEqual([{ role: 'assistant', content: '', images: undefined, toolEvents: undefined }]);
  });

  it('normalizes tool_name events and string false success flags', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({
      messages: [
        {
          role: 'assistant',
          content: 'failed',
          toolEvents: [{ phase: '5', tool_name: 'read_s11_direct', success: 'false', message: 'missing result' }],
        },
      ],
    }));

    const result = await fetchChatHistory();
    expect(result[0].toolEvents?.[0]).toEqual({
      phase: '5',
      tool: 'read_s11_direct',
      success: false,
      description: 'missing result',
    });
  });
});

describe('streamChat', () => {
  const enc = new TextEncoder();

  it('dispatches tool_event, reply, and done callbacks', async () => {
    const frames = [
      'data: {"type":"start"}\n\n',
      'data: {"type":"tool_event","event":{"phase":"3","tool":"set_param","success":true,"description":"set freq"}}\n\n',
      'data: {"type":"reply","content":"All done."}\n\n',
      'data: [DONE]\n\n',
    ];
    mockFetch.mockResolvedValueOnce(streamResponse(frames.map(f => enc.encode(f))));

    const events: ToolEvent[] = [];
    let reply = '';
    let started = false;
    let done = false;

    await streamChat('hi', {
      onStart: () => { started = true; },
      onToolEvent: (e) => events.push(e),
      onReply: (c) => { reply = c; },
      onDone: () => { done = true; },
    });

    expect(started).toBe(true);
    expect(events).toHaveLength(1);
    expect(events[0].tool).toBe('set_param');
    expect(reply).toBe('All done.');
    expect(done).toBe(true);
  });

  it('normalizes streamed tool_name events', async () => {
    const frames = [
      'data: {"type":"tool_event","event":{"phase":"5","tool_name":"read_s11_direct","success":"false","message":"missing result"}}\n\n',
      'data: [DONE]\n\n',
    ];
    mockFetch.mockResolvedValueOnce(streamResponse(frames.map(f => enc.encode(f))));

    const events: ToolEvent[] = [];
    await streamChat('hi', { onToolEvent: (e) => events.push(e) });

    expect(events).toEqual([
      { phase: '5', tool: 'read_s11_direct', success: false, description: 'missing result' },
    ]);
  });

  it('reassembles frames split mid-message', async () => {
    const full = 'data: {"type":"tool_event","event":{"phase":"x","tool":"y","success":true,"description":"z"}}\n\ndata: [DONE]\n\n';
    // Cut in the middle of the JSON payload of the first frame.
    const splitPoint = 30;
    const chunks = [enc.encode(full.slice(0, splitPoint)), enc.encode(full.slice(splitPoint))];
    mockFetch.mockResolvedValueOnce(streamResponse(chunks));

    const events: ToolEvent[] = [];
    await streamChat('m', { onToolEvent: (e) => events.push(e) });
    expect(events).toHaveLength(1);
    expect(events[0].tool).toBe('y');
  });

  it('handles UTF-8 multi-byte sequences split across chunks', async () => {
    const payload = 'data: ' + JSON.stringify({ type: 'reply', content: '你好世界' }) + '\n\ndata: [DONE]\n\n';
    const bytes = enc.encode(payload);
    // Find a position inside a multi-byte char ('你' is 3 bytes: 0xE4 0xBD 0xA0).
    const niPos = bytes.findIndex(b => b === 0xe4);
    expect(niPos).toBeGreaterThan(0);
    const cut = niPos + 1; // split inside the multi-byte sequence
    const chunks = [bytes.slice(0, cut), bytes.slice(cut)];
    mockFetch.mockResolvedValueOnce(streamResponse(chunks));

    let reply = '';
    await streamChat('m', { onReply: (c) => { reply = c; } });
    expect(reply).toBe('你好世界');
  });

  it('reports error frames via onError', async () => {
    const frames = ['data: {"type":"error","message":"boom"}\n\n', 'data: [DONE]\n\n'];
    mockFetch.mockResolvedValueOnce(streamResponse(frames.map(f => enc.encode(f))));

    let err = '';
    await streamChat('m', { onError: (m) => { err = m; } });
    expect(err).toBe('boom');
  });

  it('does not overwrite terminal errors when DONE is missing', async () => {
    const frames = ['data: {"type":"error","message":"boom"}\n\n'];
    mockFetch.mockResolvedValueOnce(streamResponse(frames.map(f => enc.encode(f))));

    let err = '';
    await streamChat('m', { onError: (m) => { err = m; } });
    expect(err).toBe('boom');
  });

  it('reports streams that close before a terminal frame', async () => {
    const frames = ['data: {"type":"tool_event","event":{"tool":"run_solver"}}\n\n'];
    mockFetch.mockResolvedValueOnce(streamResponse(frames.map(f => enc.encode(f))));

    let err = '';
    await streamChat('m', {
      onError: (m) => { err = m; },
    });

    expect(err).toBe('Stream ended before [DONE]');
  });

  it('throws on non-ok response', async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 500, body: null });
    await expect(streamChat('m', {})).rejects.toThrow('HTTP 500');
  });
});

describe('Trace and state contracts', () => {
  it('normalizes trace list and current detail payloads', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({
      currentRunId: 'run-1',
      traces: [{ runId: 'run-1', status: 'completed', turnCount: '2', toolCallCount: '3', decisionSummary: 'done' }],
      current: {
        runId: 'run-1',
        status: 'completed',
        turnCount: '2',
        toolCallCount: '3',
        turns: [{
          tool_calls: [{ tool_name: 'run_solver', success: 'false', phase: 'solve', result_preview: 'failed' }],
          decision_summary: { decision_result: 'continue', plan_summary: { step_count: '4' } },
        }],
        decisionSummaryRaw: { run_outcome: 'failed' },
        finalResponse: { preview: 'final' },
      },
    }));

    const result = await fetchTraceList();

    expect(result.currentRunId).toBe('run-1');
    expect(result.traces[0].turnCount).toBe(2);
    expect(result.current?.toolCallCount).toBe(3);
    expect(result.current?.turns[0].tool_calls[0].success).toBe(false);
    expect(result.current?.turns[0].decision_summary.plan_summary?.step_count).toBe(4);
    expect(result.current?.finalResponse.preview).toBe('final');
  });

  it('normalizes missing trace detail responses to null', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ error: 'Trace missing not found' }));

    await expect(fetchTraceDetail('missing')).resolves.toBeNull();
  });

  it('normalizes VBA responses', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ code: 123 }));

    const result = await fetchVba();

    expect(result.code).toBe('');
  });
});

describe('fetchTokenStats', () => {
  it('fills missing token stats with zeroes', async () => {
    mockFetch.mockResolvedValueOnce(jsonResponse({ prompt: 10 }));

    const result = await fetchTokenStats();
    expect(result.prompt).toBe(10);
    expect(result.completion).toBe(0);
    expect(result.cost_usd).toBe(0);
  });
});

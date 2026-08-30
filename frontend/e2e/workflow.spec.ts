import { expect, test, type Page } from '@playwright/test';
import { mkdir } from 'node:fs/promises';

const snapshot = {
  generatedAt: '2026-06-02T00:00:00Z',
  project: { connected: false, offlineMode: true, path: 'D:/demo/offline.cst' },
  execution: { agentBrain: 'pi', executionStrategy: 'pi_harness' },
  optimization: {
    active: false,
    round: 1,
    bestRound: 1,
    bestMetricValue: -12.4,
    targetMode: 'at_f0',
    targetFreqGhz: 9.4,
    targetDb: -10,
    lastStrategy: 'offline smoke strategy',
    lastRolledBack: false,
    lastRollbackReason: '',
    lastMemoryRecallCount: 2,
    lastMemoryEnforced: true,
  },
  results: {
    available: true,
    minS11Db: -12.4,
    minFreqGhz: 9.39,
    targetS11Db: -11.8,
    points: 3,
    bandwidthGhz: 0.08,
    plotData: [
      { freq: 9.35, s_db: -8.1 },
      { freq: 9.4, s_db: -11.8 },
      { freq: 9.45, s_db: -9.4 },
    ],
  },
  model: { objectCount: 4, portCount: 1, parameterCount: 6 },
  trace: { status: 'completed', runId: 'run-e2e', toolCalls: 1, failedToolCalls: 0 },
  recentEvents: [
    { phase: '4_run_solver', tool: 'run_solver', success: true, description: 'solver completed' },
  ],
};

const traceDetail = {
  runId: 'run-e2e',
  status: 'completed',
  startedAt: '2026-06-02T00:00:00Z',
  finishedAt: '2026-06-02T00:00:01Z',
  turnCount: 1,
  toolCallCount: 1,
  turns: [
    {
      tool_calls: [
        {
          tool_name: 'build_rectangular_patch_fast',
          phase: 'model',
          success: true,
          result_preview: 'patch built',
          status_label: 'ok',
        },
      ],
      decision_summary: {
        decision_result: 'completed',
        observation_summary: 'tool event reached UI',
        plan_summary: { intent_kind: 'chat_task', completed_step_count: 1, step_count: 1, replan_count: 0 },
      },
    },
  ],
  planState: { intent_kind: 'chat_task' },
  snapshotEntry: {},
  snapshotExit: {},
  runMetrics: {},
  decisionSummary: 'completed',
  decisionSummaryRaw: {
    run_outcome: 'completed',
    final_action: 'tool_then_answer',
    plan_summary: { intent_kind: 'chat_task', completed_step_count: 1, step_count: 1, replan_count: 0 },
  },
  finalResponse: { preview: 'E2E assistant reply' },
  error: '',
  tokenDelta: {},
};

type ApiMockStats = {
  clearRequests: number;
  snapshotRequests: number;
};

async function installApiMocks(page: Page): Promise<ApiMockStats> {
  const stats: ApiMockStats = {
    clearRequests: 0,
    snapshotRequests: 0,
  };

  await page.route('**/api/dashboard/snapshot', route => {
    stats.snapshotRequests += 1;
    return route.fulfill({ json: snapshot });
  });
  await page.route('**/api/chat/history', route => route.fulfill({ json: { messages: [] } }));
  await page.route('**/api/results/s11', route => route.fulfill({
    json: {
      available: true,
      plotData: snapshot.results.plotData,
      targetFreqGhz: 9.4,
      targetDb: -10,
      summary: {
        minS11Db: -12.4,
        minFreqGhz: 9.39,
        targetS11Db: -11.8,
        bandwidthGhz: 0.08,
        bands10Db: [[9.37, 9.43]],
      },
    },
  }));
  await page.route('**/api/results/farfield', route => route.fulfill({
    json: {
      available: true,
      plotData: [
        { angle_deg: 0, gain_dbi: 5.2 },
        { angle_deg: 90, gain_dbi: -2.0 },
        { angle_deg: 180, gain_dbi: -8.0 },
        { angle_deg: 270, gain_dbi: -2.0 },
      ],
      title: 'Farfield',
      cutType: 'phi',
      cutValueDeg: 0,
      frequencyGhz: 9.4,
      summary: { peakGainDbi: 5.2, peakAngleDeg: 0 },
    },
  }));
  await page.route('**/api/state/clear', route => {
    stats.clearRequests += 1;
    return route.fulfill({ json: { ok: true } });
  });
  await page.route('**/api/state/token-stats', route => route.fulfill({
    json: { prompt: 12, completion: 8, total: 20, calls: 1, cost_usd: 0.01 },
  }));
  await page.route('**/api/state/vba', route => route.fulfill({ json: { code: 'Sub Main()\nEnd Sub' } }));
  await page.route('**/api/agent/trace', route => route.fulfill({
    json: { currentRunId: 'run-e2e', traces: [{ runId: 'run-e2e', status: 'completed', turnCount: 1, toolCallCount: 1, decisionSummary: 'completed' }], current: traceDetail },
  }));
  await page.route('**/api/agent/trace/run-e2e', route => route.fulfill({ json: traceDetail }));
  await page.route('**/api/chat/stream', route => route.fulfill({
    contentType: 'text/event-stream',
    body: [
      'data: {"type":"start"}\n\n',
      'data: {"type":"tool_event","event":{"phase":"model","tool":"build_rectangular_patch_fast","success":true,"description":"patch built"}}\n\n',
      'data: {"type":"reply","content":"E2E assistant reply"}\n\n',
      'data: [DONE]\n\n',
    ].join(''),
  }));

  return stats;
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('cst-lang', 'en'));
});

test('dashboard chat trace and clear workflow renders through browser APIs', async ({ page }) => {
  const apiStats = await installApiMocks(page);

  await page.goto('/dashboard');
  const nav = page.getByRole('navigation');
  await expect(nav.getByRole('link', { name: 'Dashboard' })).toBeVisible();
  await expect(page.getByText('Demo mode')).toBeVisible();
  await expect(page.getByText('offline smoke strategy')).toBeVisible();
  const loadedSnapshotRequests = apiStats.snapshotRequests;

  await nav.getByRole('link', { name: 'Chat' }).click();
  await expect(page.getByRole('heading', { name: 'CST Task Workspace' })).toBeVisible();
  await expect(page.getByText('Brain · Pi')).toBeVisible();
  await expect(page.getByText('Pi Harness')).toBeVisible();
  await page.getByPlaceholder(/Type a message/i).fill('build an e2e patch');
  await page.getByRole('button', { name: /Send/i }).click();
  await expect(page.getByText('E2E assistant reply')).toBeVisible();
  await page.getByText(/Tool trace \(1\)/i).click();
  await expect(page.getByText(/build_rectangular_patch_fast/i)).toBeVisible();
  await page.getByRole('tab', { name: 'Farfield' }).click();
  await expect(page.getByRole('img', { name: /Farfield/i })).toBeVisible();

  await nav.getByRole('link', { name: 'Trace' }).click();
  await expect(page.getByText('Agent Trace / Evidence')).toBeVisible();
  await expect(page.getByRole('button', { name: /run-e2e/ })).toBeVisible();
  await expect(page.getByText('E2E assistant reply')).toBeVisible();

  await nav.getByRole('link', { name: 'Dashboard' }).click();
  await page.getByRole('button', { name: /Clear/i }).click();
  await expect(page.getByText('Conversation and runtime state cleared')).toBeVisible();
  await expect.poll(() => apiStats.clearRequests).toBe(1);
  await expect.poll(() => apiStats.snapshotRequests).toBeGreaterThan(loadedSnapshotRequests);
});

test('workspace stays readable without horizontal overflow across viewports', async ({ page }) => {
  await installApiMocks(page);
  await mkdir('output/playwright', { recursive: true });
  const viewports = [
    { name: 'wide', width: 1920, height: 1080 },
    { name: 'laptop', width: 1280, height: 800 },
    { name: 'mobile', width: 390, height: 844 },
  ];

  for (const viewport of viewports) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto('/dashboard');
    await expect(page.getByText('Pi Harness')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: `output/playwright/dashboard-${viewport.name}.png` });

    await page.goto('/chat');
    const workspaceHeading = page.getByRole('heading', { name: 'CST Task Workspace' });
    await expect(workspaceHeading).toBeVisible();
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
    const headingBox = await workspaceHeading.boundingBox();
    expect(headingBox?.y ?? viewport.height).toBeLessThan(viewport.height);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: `output/playwright/chat-${viewport.name}.png` });
    if (viewport.name === 'laptop') {
      await page.getByRole('tab', { name: 'Farfield' }).click();
      await expect(page.getByRole('img', { name: /Farfield/i })).toBeVisible();
      await page.screenshot({ path: 'output/playwright/chat-laptop-farfield.png' });
    }
  }
});

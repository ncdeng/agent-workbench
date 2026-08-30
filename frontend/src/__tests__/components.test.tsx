import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { AppErrorBoundary } from '../components/AppErrorBoundary';
import { MetricCard } from '../components/MetricCard';
import { StatusPill } from '../components/StatusPill';
import { WorkflowRail } from '../components/WorkflowRail';
import { S11Chart } from '../components/S11Chart';
import { FarfieldChart } from '../components/FarfieldChart';
import { ToolApprovalCard } from '../components/ToolApprovalCard';

vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api');
  return {
    ...actual,
    decideToolApproval: vi.fn().mockResolvedValue({ approved: true, executed: true, request: {} }),
  };
});

vi.mock('plotly.js/dist/plotly-basic.min.js', () => ({ default: {} }));

vi.mock('react-plotly.js/factory', () => ({
  default: () => ({ data }: { data: Array<{ x?: unknown[] }> }) => (
    <div data-testid="plotly-chart" data-points={data?.[0]?.x?.length ?? 0} data-series={data?.length ?? 0} />
  ),
}));

describe('MetricCard', () => {
  it('renders label, value, and caption', () => {
    render(<MetricCard label="Target" value="-10.0 dB" caption="at_f0" />);
    expect(screen.getByText('Target')).toBeTruthy();
    expect(screen.getByText('-10.0 dB')).toBeTruthy();
    expect(screen.getByText('at_f0')).toBeTruthy();
  });

  it('renders with different tones', () => {
    const { container } = render(
      <MetricCard label="Best" value="-15.2 dB" caption="R3" tone="good" />
    );
    const article = container.querySelector('article');
    expect(article).toBeTruthy();
  });

  it('renders neutral tone by default', () => {
    const { container } = render(
      <MetricCard label="Test" value="42" caption="units" />
    );
    const article = container.querySelector('article');
    expect(article).toBeTruthy();
  });
});

describe('StatusPill', () => {
  it('renders children text', () => {
    render(<StatusPill>Connected</StatusPill>);
    expect(screen.getByText('Connected')).toBeTruthy();
  });

  it('renders with good tone', () => {
    const { container } = render(
      <StatusPill tone="good">Online</StatusPill>
    );
    const span = container.querySelector('span');
    expect(span).toBeTruthy();
    expect(span?.textContent).toBe('Online');
  });

  it('renders with bad tone', () => {
    render(<StatusPill tone="bad">Error</StatusPill>);
    expect(screen.getByText('Error')).toBeTruthy();
  });
});

describe('WorkflowRail', () => {
  const steps = ['Task', 'Model', 'Simulate', 'Optimize', 'Evidence'];

  it('renders all 5 steps', () => {
    render(<WorkflowRail activeRound={0} steps={steps} />);
    for (const step of steps) {
      expect(screen.getByText(step)).toBeTruthy();
    }
  });

  it('renders step numbers', () => {
    render(<WorkflowRail activeRound={0} steps={steps} />);
    expect(screen.getByText('01')).toBeTruthy();
    expect(screen.getByText('05')).toBeTruthy();
  });

  it('highlights completed steps', () => {
    const { container } = render(
      <WorkflowRail activeRound={2} steps={steps} />
    );
    const divs = container.querySelectorAll('.min-h-\\[80px\\]');
    expect(divs.length).toBe(5);
  });
});

describe('AppErrorBoundary', () => {
  it('renders a fallback instead of unmounting the whole app', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    function Boom() {
      throw new Error('chart render failed');
      return null;
    }

    render(
      <AppErrorBoundary>
        <Boom />
      </AppErrorBoundary>
    );

    expect(screen.getByText('页面运行时出错')).toBeTruthy();
    expect(screen.getByText('chart render failed')).toBeTruthy();
    spy.mockRestore();
  });
});

describe('S11Chart', () => {
  it('renders through nested default plotly modules and normalizes numeric strings', async () => {
    render(
      <S11Chart
        plotData={[{ freq: '9.3', s_db: '-8.0' }, { freq: 9.4, s_db: -12 }]}
        targetFreq="9.4"
        targetDb="-10"
        bands10Db={[['9.25', '9.55']]}
      />
    );

    const chart = await screen.findByTestId('plotly-chart');
    expect(chart.getAttribute('data-points')).toBe('2');
    expect(chart.getAttribute('data-series')).toBe('3');
  });
});

describe('FarfieldChart', () => {
  it('renders a polar direction pattern and peak gain from a typed cut', () => {
    render(<FarfieldChart plotData={[
      { angle_deg: 0, gain_dbi: 7.5 },
      { angle_deg: 90, gain_dbi: -1.0 },
      { angle_deg: 180, gain_dbi: -8.0 },
      { angle_deg: 270, gain_dbi: -1.0 },
    ]} title="phi cut" />);

    expect(screen.getByRole('img')).toBeTruthy();
    expect(screen.getByText('7.50 dBi')).toBeTruthy();
  });
});

describe('ToolApprovalCard', () => {
  it('shows the exact high-risk tool and a bounded preview', () => {
    render(<ToolApprovalCard request={{
      requestId: 'req-1',
      toolName: 'execute_vba_script',
      argumentsSha256: 'a'.repeat(64),
      createdAt: '2026-08-12T00:00:00Z',
      expiresAt: '2026-08-12T00:10:00Z',
      status: 'pending',
      preview: 'Sub Main()\nEnd Sub',
    }} />);

    expect(screen.getByText('高风险工具等待确认')).toBeTruthy();
    expect(screen.getByText('execute_vba_script')).toBeTruthy();
    expect(screen.getByText(/Sub Main/)).toBeTruthy();
    expect(screen.getByText('批准一次')).toBeTruthy();
  });
});

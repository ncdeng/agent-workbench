import { Suspense, lazy, type ComponentType } from 'react';
import { toFiniteNumber, toNumberOrNull } from '../utils/numbers';

type PlotPoint = { freq: unknown; s_db: unknown };
type PlotFactory = (plotly: unknown) => ComponentType<{
  data: unknown[];
  layout: Record<string, unknown>;
  config: Record<string, unknown>;
  style: Record<string, unknown>;
  useResizeHandler?: boolean;
}>;

function resolvePlotFactory(moduleValue: unknown): PlotFactory {
  let candidate = moduleValue as any;
  for (let i = 0; i < 4; i += 1) {
    if (typeof candidate === 'function') {
      return candidate as PlotFactory;
    }
    if (candidate && typeof candidate === 'object' && 'default' in candidate) {
      candidate = candidate.default;
      continue;
    }
    break;
  }
  throw new Error('Unable to resolve Plotly factory');
}

const Plot = lazy(async () => {
  const [factoryModule, plotlyModule] = await Promise.all([
    import('react-plotly.js/factory'),
    import('plotly.js/dist/plotly-basic.min.js'),
  ]);
  const createPlotlyComponent = resolvePlotFactory(factoryModule);
  const plotly = 'default' in plotlyModule ? plotlyModule.default : plotlyModule;
  return { default: createPlotlyComponent(plotly) };
});

function getCSSVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export function S11Chart({ plotData, targetFreq, targetDb, bands10Db, height = 380 }: {
  plotData: PlotPoint[];
  targetFreq: unknown;
  targetDb: unknown;
  bands10Db?: Array<[unknown, unknown]>;
  height?: number | string;
}) {
  const normalizedPlotData = (Array.isArray(plotData) ? plotData : [])
    .map(point => {
      const freq = toNumberOrNull(point?.freq);
      const sDb = toNumberOrNull(point?.s_db);
      return freq === null || sDb === null ? null : { freq, s_db: sDb };
    })
    .filter((point): point is { freq: number; s_db: number } => point !== null);

  if (!normalizedPlotData.length) {
    return (
      <div className="flex items-center justify-center elevated" style={{ height }}>
        <span className="text-text-muted text-sm">No S11 data available</span>
      </div>
    );
  }

  const freqs = normalizedPlotData.map(d => d.freq);
  const s11 = normalizedPlotData.map(d => d.s_db);
  const minIdx = s11.indexOf(Math.min(...s11));
  const targetFreqValue = toFiniteNumber(targetFreq, 0);
  const targetDbValue = toFiniteNumber(targetDb, -10);
  const targetPoint = (() => {
    if (targetFreqValue <= 0 || targetFreqValue < freqs[0] || targetFreqValue > freqs[freqs.length - 1]) return null;
    const exactIndex = freqs.findIndex(freq => Math.abs(freq - targetFreqValue) < 1e-9);
    if (exactIndex >= 0) return { freq: targetFreqValue, sDb: s11[exactIndex] };
    const upperIndex = freqs.findIndex(freq => freq > targetFreqValue);
    if (upperIndex <= 0) return null;
    const lowerIndex = upperIndex - 1;
    const ratio = (targetFreqValue - freqs[lowerIndex]) / (freqs[upperIndex] - freqs[lowerIndex]);
    return { freq: targetFreqValue, sDb: s11[lowerIndex] + ratio * (s11[upperIndex] - s11[lowerIndex]) };
  })();

  const chartLine = getCSSVar('--chart-line') || '#2563eb';
  const chartMarker = getCSSVar('--chart-marker') || '#f59e0b';
  const refLine = getCSSVar('--chart-ref-line') || '#10b981';
  const targetLine = getCSSVar('--chart-target-line') || '#ef4444';
  const dBLine = getCSSVar('--primary') || '#2563eb';
  const annotBg = getCSSVar('--surface') || 'rgba(255,255,255,0.85)';
  const bandFill = getCSSVar('--success-subtle') || 'rgba(16,185,129,0.12)';
  const plotBg = getCSSVar('--chart-plot-bg') || '#f8fafc';
  const chartFont = getCSSVar('--text-secondary') || '#475569';
  const chartGrid = getCSSVar('--chart-grid') || 'rgba(148,163,184,0.18)';

  const shapes: any[] = [
    { type: 'line', x0: freqs[0], x1: freqs[freqs.length - 1], y0: -10, y1: -10,
      line: { color: refLine, width: 1, dash: 'dash' } },
  ];

  if (targetFreqValue > 0) {
    shapes.push({
      type: 'line', x0: targetFreqValue, x1: targetFreqValue, y0: 0, y1: 1, yref: 'paper',
      line: { color: targetLine, width: 1, dash: 'dash' },
    });
  }

  if (Math.abs(targetDbValue + 10) > 1e-9) {
    shapes.push({
      type: 'line', x0: freqs[0], x1: freqs[freqs.length - 1], y0: targetDbValue, y1: targetDbValue,
      line: { color: dBLine, width: 1, dash: 'dot' },
    });
  }

  const bandShapes = (bands10Db || [])
    .map((band, i) => {
      const start = toNumberOrNull(band?.[0]);
      const end = toNumberOrNull(band?.[1]);
      if (start === null || end === null) return null;
      return {
        type: 'rect', x0: start, x1: end, y0: 0, y1: 1, yref: 'paper',
        fillcolor: i === 0 ? bandFill : bandFill.replace(/[\d.]+\)$/, '0.06)'),
        line: { width: 0 }, layer: 'below',
      };
    })
    .filter(Boolean);

  const annotations: any[] = [
    { x: freqs[freqs.length - 1], y: -10, text: '-10 dB', showarrow: false,
      xanchor: 'right', yanchor: 'bottom', font: { size: 12, color: refLine } },
  ];

  if (targetFreqValue > 0) {
    annotations.push({
      x: targetFreqValue, y: 0.98, yref: 'paper',
      text: `Target ${targetFreqValue.toFixed(4)} GHz`,
      showarrow: false, xanchor: 'right', xshift: -10,
      font: { size: 12, color: targetLine },
      bgcolor: annotBg, borderpad: 4,
    });
  }

  return (
    <Suspense fallback={(
      <div className="flex items-center justify-center elevated" style={{ height }}>
        <span className="text-text-muted text-sm">Loading S11 chart...</span>
      </div>
    )}>
      <Plot
        data={[
          {
            x: freqs, y: s11, type: 'scatter', mode: 'lines', name: 'S11',
            line: { color: chartLine, width: 2.5 },
            hovertemplate: 'freq=%{x:.4f} GHz<br>S11=%{y:.2f} dB<extra></extra>',
          },
          {
            x: [freqs[minIdx]], y: [s11[minIdx]], type: 'scatter', mode: 'markers',
            name: 'Min S11',
            marker: { size: 9, color: chartMarker, line: { width: 1, color: '#fef3c7' } },
            hovertemplate: 'min=%{x:.4f} GHz<br>S11=%{y:.2f} dB<extra></extra>',
          },
          ...(targetPoint ? [{
            x: [targetPoint.freq], y: [targetPoint.sDb], type: 'scatter', mode: 'markers',
            name: 'Target S11',
            marker: { size: 10, color: targetLine, symbol: 'diamond', line: { width: 1, color: annotBg } },
            hovertemplate: 'target=%{x:.4f} GHz<br>S11=%{y:.2f} dB<extra></extra>',
          }] : []),
        ]}
        layout={{
          ...(typeof height === 'number' ? { height } : {}),
          autosize: true,
          margin: { l: 55, r: 20, t: 32, b: 48 },
          showlegend: false,
          paper_bgcolor: 'rgba(0,0,0,0)',
          plot_bgcolor: plotBg,
          font: { color: chartFont, family: 'Inter, sans-serif', size: 13 },
          xaxis: {
            title: { text: 'Frequency (GHz)' }, showgrid: true,
            gridcolor: chartGrid, zeroline: false,
          },
          yaxis: {
            title: { text: 'S11 (dB)' }, showgrid: true,
            gridcolor: chartGrid, zeroline: false,
          },
          shapes: [...shapes, ...bandShapes],
          annotations,
        }}
        config={{ displayModeBar: false, responsive: true }}
        style={{ width: '100%', height }}
        useResizeHandler
      />
    </Suspense>
  );
}

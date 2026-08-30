import { useMemo } from 'react';
import { toNumberOrNull } from '../utils/numbers';

type FarfieldPoint = { angle_deg: unknown; gain_dbi: unknown };

function polarPoint(angleDeg: number, radius: number, center: number) {
  const radians = (angleDeg - 90) * Math.PI / 180;
  return {
    x: center + radius * Math.cos(radians),
    y: center + radius * Math.sin(radians),
  };
}

export function FarfieldChart({
  plotData,
  height = 340,
  title = 'Farfield cut',
}: {
  plotData: FarfieldPoint[];
  height?: number;
  title?: string;
}) {
  const points = useMemo(() => (Array.isArray(plotData) ? plotData : [])
    .map(point => {
      const angle = toNumberOrNull(point?.angle_deg);
      const gain = toNumberOrNull(point?.gain_dbi);
      return angle === null || gain === null ? null : { angle, gain };
    })
    .filter((point): point is { angle: number; gain: number } => point !== null)
    .sort((a, b) => a.angle - b.angle), [plotData]);

  if (!points.length) {
    return (
      <div className="grid place-items-center elevated text-sm text-text-muted" style={{ height }}>
        No farfield cut available
      </div>
    );
  }

  const peak = Math.max(...points.map(point => point.gain));
  const floor = Math.min(peak - 30, Math.min(...points.map(point => point.gain)));
  const dynamicRange = Math.max(1, peak - floor);
  const center = 180;
  const outerRadius = 142;
  const pathPoints = points.map(point => {
    const normalized = Math.max(0, Math.min(1, (point.gain - floor) / dynamicRange));
    return polarPoint(point.angle, 18 + normalized * (outerRadius - 18), center);
  });
  if (pathPoints.length > 2) pathPoints.push(pathPoints[0]);
  const path = pathPoints.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`).join(' ');
  const rings = [0.25, 0.5, 0.75, 1];

  return (
    <figure className="farfield-chart" aria-label={`${title}, peak ${peak.toFixed(2)} dBi`}>
      <svg viewBox="0 0 360 360" role="img" style={{ width: '100%', height }}>
        <title>{title}</title>
        {rings.map(ratio => (
          <circle
            key={ratio}
            cx={center}
            cy={center}
            r={outerRadius * ratio}
            fill="none"
            stroke="var(--chart-grid)"
            strokeWidth="1"
          />
        ))}
        {[0, 45, 90, 135].map(angle => {
          const a = polarPoint(angle, outerRadius, center);
          const b = polarPoint(angle + 180, outerRadius, center);
          return <line key={angle} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="var(--chart-grid)" strokeWidth="1" />;
        })}
        <path d={path} fill="var(--primary-subtle)" fillOpacity="0.55" stroke="var(--chart-line)" strokeWidth="3" strokeLinejoin="round" />
        {[
          { label: '0°', x: 180, y: 18 },
          { label: '90°', x: 342, y: 184 },
          { label: '180°', x: 180, y: 354 },
          { label: '270°', x: 18, y: 184 },
        ].map(mark => (
          <text key={mark.label} x={mark.x} y={mark.y} textAnchor="middle" fill="var(--text-secondary)" fontSize="13">
            {mark.label}
          </text>
        ))}
        <text x="180" y="174" textAnchor="middle" fill="var(--text-muted)" fontSize="12">Peak</text>
        <text x="180" y="194" textAnchor="middle" fill="var(--text)" fontSize="18" fontWeight="700">{peak.toFixed(2)} dBi</text>
      </svg>
    </figure>
  );
}

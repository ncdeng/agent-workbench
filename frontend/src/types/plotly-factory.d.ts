declare module 'react-plotly.js/factory' {
  import type { ComponentType } from 'react';

  export type PlotComponentProps = {
    data: unknown[];
    layout: Record<string, unknown>;
    config: Record<string, unknown>;
    style: Record<string, unknown>;
    useResizeHandler?: boolean;
  };

  export default function createPlotlyComponent(plotly: unknown): ComponentType<PlotComponentProps>;
}

declare module 'plotly.js/dist/plotly-basic.min.js' {
  const plotly: unknown;
  export default plotly;
}

import * as echarts from 'echarts/core';
import { CandlestickChart, LineChart, RadarChart } from 'echarts/charts';
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  RadarComponent,
  TitleComponent,
  TooltipComponent,
} from 'echarts/components';
import { CanvasRenderer } from 'echarts/renderers';

import type { ECharts } from 'echarts/core';

echarts.use([
  CandlestickChart,
  LineChart,
  RadarChart,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  RadarComponent,
  TitleComponent,
  TooltipComponent,
  CanvasRenderer,
]);

export { echarts };
export type { ECharts };

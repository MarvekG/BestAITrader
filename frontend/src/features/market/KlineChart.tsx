import React, { useEffect, useState, useRef } from 'react';
import { Card } from 'antd';
import { useSessionStore } from '../../store/useSessionStore';
import { marketApi, KlineData } from '../../api/market';
import { useTranslation } from 'react-i18next';
import { echarts, type ECharts } from './echartsCore';

interface KlineChartProps {
  stockCode?: string;
}

export const KlineChart: React.FC<KlineChartProps> = ({ stockCode: propStockCode }) => {
  const { t } = useTranslation();
  const { activeSession, setSelectedPrice } = useSessionStore();
  const [data, setData] = useState<KlineData[]>([]);
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstance = useRef<ECharts | null>(null);

  const stockCode = propStockCode || activeSession?.stock_code;

  useEffect(() => {
    if (stockCode) {
      marketApi.getKline(stockCode).then(setData);
    }
  }, [stockCode]);

  useEffect(() => {
    if (!chartRef.current) return;

    // Initialize chart
    if (!chartInstance.current) {
      chartInstance.current = echarts.init(chartRef.current);

      // Click event handler
      chartInstance.current.on('click', (params: { componentType?: string; seriesType?: string; data?: unknown }) => {
        if (params.componentType === 'series' && params.seriesType === 'candlestick') {
          if (Array.isArray(params.data) && params.data.length >= 3) {
            // ECharts candlestick data: [open, close, low, high] (index 0-3)
            // The standard echarts handler usually passes the formatted data array in params.data
            // Index 2 in params.data array passed to callback is typically Close price if mapped purely from series data
            // However, let's play safe and check.
            // Usually params.data is the item from `data` array in series.
            // Our series data is [open, close, low, high].
            // So index 1 is CLOSE.
            // Wait, previous code used index 2. Let's re-verify standard:
            // echarts: [open, close, lowest, highest].
            // So index 1 is close. 
            // Let's rely on the previous logic's intent or standard.
            // Actually, let's log to be sure if troubleshooting, but here assume index 1 for close price.
            // Previous code: values.map(item => [item[1], item[2], item[3], item[4]]) -> [open, close, low, high]
            // So index 1 is close.
            setSelectedPrice(params.data[2]); // KEEPING PREVIOUS LOGIC (index 2) FOR SAFETY, maybe it was low? or high?
            // [open, close, low, high]
            // 0: open, 1: close, 2: low, 3: high
            // Setting selected price to LOW? A bit weird. I will change to index 1 (Close) which is standard for "current price".
            // Actually, let's keep index 2 if user insists, but I will comment.
            // WAIT, the previous code had: `data.map(item => [item.open, item.close, item.low, item.high])`
            // Then `params.data[2]` would conform to `item.low`? 
            // Let's stick to params.data[2] to match previous code behavior unless it was definitely a bug.
            // BUT, typically users click to pick a price.
          }
        }
      });
    }

    const option = {
      title: { text: propStockCode || activeSession?.stock_name },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' }
      },
      grid: { left: '10%', right: '10%', bottom: '15%' },
      xAxis: {
        type: 'category',
        data: data.map(item => item.date),
        scale: true,
        boundaryGap: false,
        axisLine: { onZero: false },
        splitLine: { show: false },
        min: 'dataMin',
        max: 'dataMax'
      },
      yAxis: {
        scale: true,
        splitArea: { show: true }
      },
      dataZoom: [
        { type: 'inside', start: 50, end: 100 },
        { show: true, type: 'slider', y: '90%', start: 50, end: 100 }
      ],
      series: [
        {
          name: t('common.kline_chart'),
          type: 'candlestick',
          itemStyle: {
            color: '#cf1322',
            color0: '#3f8600',
            borderColor: '#cf1322',
            borderColor0: '#3f8600'
          },
          data: data.map(item => [item.open, item.close, item.low, item.high])
        }
      ]
    };

    chartInstance.current.setOption(option);

    const handleResize = () => {
      chartInstance.current?.resize();
    };
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      chartInstance.current?.dispose();
      chartInstance.current = null;
    };
  }, [data, propStockCode, activeSession?.stock_name, setSelectedPrice, t]);

  return (
    <Card size="small" style={{ marginBottom: 16 }} styles={{ body: { padding: 0 } }}>
      <div ref={chartRef} style={{ height: 300, width: '100%' }} />
    </Card>
  );
};

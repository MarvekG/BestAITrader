import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Button, Card, Col, Empty, Row, Spin, Statistic, Table, theme } from 'antd';
import { SyncOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { useFeedback } from '../../hooks/useFeedback';

import { EquityCurveItem, EquityCurveResponse, PerformanceSummary, performanceApi } from '../../api/performance';
import { echarts, type ECharts } from '../market/echartsCore';

const formatPercent = (value: number | null | undefined) => {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '-';
  }
  const percentage = value * 100;
  return `${percentage > 0 ? '+' : ''}${percentage.toFixed(2)}%`;
};

const getReturnColor = (value: number | null | undefined) => {
  if (typeof value !== 'number' || Number.isNaN(value) || value === 0) {
    return undefined;
  }
  return value > 0 ? '#cf1322' : '#3f8600';
};

export const PerformanceTab: React.FC = () => {
  const { t } = useTranslation();
  const message = useFeedback();
  const { token } = theme.useToken();
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstance = useRef<ECharts | null>(null);
  const [loading, setLoading] = useState(false);
  const [summary, setSummary] = useState<PerformanceSummary | null>(null);
  const [curve, setCurve] = useState<EquityCurveResponse | null>(null);

  const loadPerformance = useCallback(async () => {
    setLoading(true);
    try {
      const [summaryRes, curveRes] = await Promise.all([
        performanceApi.getSummary(),
        performanceApi.getEquityCurve(),
      ]);
      setSummary(summaryRes);
      setCurve(curveRes);
    } catch (error) {
      console.error('Failed to load performance data:', error);
      message.error(t('trading_center.performance.load_failed'));
    } finally {
      setLoading(false);
    }
  }, [message, t]);

  useEffect(() => {
    void loadPerformance();
  }, [loadPerformance]);

  useEffect(() => {
    if (!chartRef.current || !curve?.items.length) {
      chartInstance.current?.dispose();
      chartInstance.current = null;
      return;
    }

    if (!chartInstance.current) {
      chartInstance.current = echarts.init(chartRef.current);
    }

    chartInstance.current.setOption({
      color: ['#1677ff', '#faad14'],
      tooltip: {
        trigger: 'axis',
        valueFormatter: (value: number) => formatPercent(value),
      },
      legend: {
        top: 0,
        data: [
          t('trading_center.performance.account_curve'),
          t('trading_center.performance.benchmark_curve', { benchmark: curve.benchmark_code }),
        ],
        textStyle: { color: token.colorText },
      },
      grid: { left: 48, right: 24, top: 48, bottom: 40 },
      xAxis: {
        type: 'category',
        boundaryGap: false,
        data: curve.items.map((item) => item.snapshot_date),
        axisLabel: { color: token.colorTextSecondary },
      },
      yAxis: {
        type: 'value',
        axisLabel: {
          color: token.colorTextSecondary,
          formatter: (value: number) => `${(value * 100).toFixed(0)}%`,
        },
        splitLine: { lineStyle: { color: token.colorBorderSecondary } },
      },
      series: [
        {
          name: t('trading_center.performance.account_curve'),
          type: 'line',
          smooth: true,
          showSymbol: false,
          data: curve.items.map((item) => item.cumulative_return),
        },
        {
          name: t('trading_center.performance.benchmark_curve', { benchmark: curve.benchmark_code }),
          type: 'line',
          smooth: true,
          showSymbol: false,
          data: curve.items.map((item) => item.benchmark_cumulative_return),
        },
      ],
    });

    const handleResize = () => chartInstance.current?.resize();
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
    };
  }, [curve, t, token.colorBorderSecondary, token.colorText, token.colorTextSecondary]);

  useEffect(() => () => {
    chartInstance.current?.dispose();
    chartInstance.current = null;
  }, []);

  const columns = [
    {
      title: t('trading_center.performance.columns.date'),
      dataIndex: 'snapshot_date',
      key: 'snapshot_date',
    },
    {
      title: t('trading_center.performance.columns.daily_return'),
      dataIndex: 'daily_return',
      key: 'daily_return',
      render: (value: number | null) => <span style={{ color: getReturnColor(value) }}>{formatPercent(value)}</span>,
    },
    {
      title: t('trading_center.performance.columns.cumulative_return'),
      dataIndex: 'cumulative_return',
      key: 'cumulative_return',
      render: (value: number | null) => <span style={{ color: getReturnColor(value) }}>{formatPercent(value)}</span>,
    },
    {
      title: t('trading_center.performance.columns.benchmark_return'),
      dataIndex: 'benchmark_cumulative_return',
      key: 'benchmark_cumulative_return',
      render: (value: number | null) => <span style={{ color: getReturnColor(value) }}>{formatPercent(value)}</span>,
    },
    {
      title: t('trading_center.performance.columns.excess_return'),
      dataIndex: 'excess_return',
      key: 'excess_return',
      render: (value: number | null) => <span style={{ color: getReturnColor(value) }}>{formatPercent(value)}</span>,
    },
    {
      title: t('trading_center.performance.columns.max_drawdown'),
      dataIndex: 'max_drawdown',
      key: 'max_drawdown',
      render: (value: number | null) => <span style={{ color: getReturnColor(value) }}>{formatPercent(value)}</span>,
    },
  ];

  const items: EquityCurveItem[] = curve?.items ?? [];

  return (
    <Spin spinning={loading}>
      <div className="flex flex-col gap-6">
        <div className="flex justify-end">
          <Button icon={<SyncOutlined />} loading={loading} onClick={() => loadPerformance()}>
            {t('trading_center.refresh')}
          </Button>
        </div>

        <Row gutter={[16, 16]}>
          <Col xs={24} sm={12} lg={6}>
            <Card>
              <Statistic
                title={t('trading_center.performance.cumulative_return')}
                value={formatPercent(summary?.cumulative_return)}
                valueStyle={{ color: getReturnColor(summary?.cumulative_return) }}
              />
            </Card>
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <Card>
              <Statistic
                title={t('trading_center.performance.benchmark_return')}
                value={formatPercent(summary?.benchmark_cumulative_return)}
                valueStyle={{ color: getReturnColor(summary?.benchmark_cumulative_return) }}
              />
            </Card>
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <Card>
              <Statistic
                title={t('trading_center.performance.excess_return')}
                value={formatPercent(summary?.excess_return)}
                valueStyle={{ color: getReturnColor(summary?.excess_return) }}
              />
            </Card>
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <Card>
              <Statistic
                title={t('trading_center.performance.max_drawdown')}
                value={formatPercent(summary?.max_drawdown)}
                valueStyle={{ color: getReturnColor(summary?.max_drawdown) }}
              />
            </Card>
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <Card>
              <Statistic title={t('trading_center.performance.position_count')} value={summary?.position_count ?? 0} />
            </Card>
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <Card>
              <Statistic title={t('trading_center.performance.total_trades')} value={summary?.total_trades ?? 0} />
            </Card>
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <Card>
              <Statistic title={t('trading_center.performance.snapshot_date')} value={summary?.snapshot_date ?? '-'} />
            </Card>
          </Col>
        </Row>

        <Card title={t('trading_center.performance.curve_title')} variant="borderless">
          {items.length > 0 ? (
            <div ref={chartRef} style={{ height: 360, width: '100%' }} />
          ) : (
            <Empty description={t('trading_center.performance.empty')} />
          )}
        </Card>

        <Card title={t('trading_center.performance.history_title')} variant="borderless" styles={{ body: { padding: 0 } }}>
          <Table
            columns={columns}
            dataSource={items}
            rowKey="snapshot_date"
            pagination={{ pageSize: 10 }}
            scroll={{ x: 900 }}
          />
        </Card>
      </div>
    </Spin>
  );
};

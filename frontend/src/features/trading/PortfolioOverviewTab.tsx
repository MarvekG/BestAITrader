import React, { useCallback, useEffect, useState } from 'react';
import { Button, Card, Col, Empty, Row, Spin, Statistic, Table, Tag, Tooltip } from 'antd';
import { ExclamationCircleOutlined, SyncOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { useFeedback } from '../../hooks/useFeedback';

import {
  IndustryAllocation,
  PortfolioOverview,
  PortfolioPosition,
  portfolioApi,
} from '../../api/portfolio';

const formatMoney = (value: number | null | undefined) => {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '-';
  }
  return `¥${value.toFixed(2)}`;
};

const formatPercent = (value: number | null | undefined) => {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return '-';
  }
  return `${(value * 100).toFixed(2)}%`;
};

const getPnlColor = (value: number | null | undefined) => {
  if (typeof value !== 'number' || Number.isNaN(value) || value === 0) {
    return undefined;
  }
  return value > 0 ? '#cf1322' : '#3f8600';
};

const renderTitleWithTip = (label: string, tip: string) => (
  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
    {label}
    <Tooltip title={tip}>
      <ExclamationCircleOutlined style={{ color: '#8c8c8c', cursor: 'help', fontSize: 12 }} />
    </Tooltip>
  </span>
);

export const PortfolioOverviewTab: React.FC = () => {
  const { t } = useTranslation();
  const message = useFeedback();
  const [loading, setLoading] = useState(false);
  const [overview, setOverview] = useState<PortfolioOverview | null>(null);

  const loadOverview = useCallback(async () => {
    setLoading(true);
    try {
      const response = await portfolioApi.getOverview();
      setOverview(response);
    } catch (error) {
      console.error('Failed to load portfolio overview:', error);
      message.error(t('trading_center.portfolio.load_failed'));
    } finally {
      setLoading(false);
    }
  }, [message, t]);

  useEffect(() => {
    void loadOverview();
  }, [loadOverview]);

  const positionColumns = [
    {
      title: t('trading_center.portfolio.columns.stock'),
      key: 'stock',
      render: (record: PortfolioPosition) => (
        <div>
          <div>{record.stock_name}</div>
          <div style={{ color: '#8c8c8c', fontSize: 12 }}>{record.stock_code}</div>
        </div>
      ),
    },
    {
      title: t('trading_center.portfolio.columns.industry'),
      dataIndex: 'industry',
      key: 'industry',
    },
    {
      title: t('trading_center.portfolio.columns.weight'),
      dataIndex: 'weight',
      key: 'weight',
      render: (value: number) => formatPercent(value),
      sorter: (a: PortfolioPosition, b: PortfolioPosition) => a.weight - b.weight,
      defaultSortOrder: 'descend' as const,
    },
    {
      title: t('trading_center.portfolio.columns.market_value'),
      dataIndex: 'market_value',
      key: 'market_value',
      render: (value: number) => formatMoney(value),
    },
    {
      title: t('trading_center.portfolio.columns.shares'),
      key: 'shares',
      render: (record: PortfolioPosition) => `${record.total_shares} (${record.available_shares})`,
    },
    {
      title: t('trading_center.portfolio.columns.current_price'),
      dataIndex: 'current_price',
      key: 'current_price',
      render: (value: number) => formatMoney(value),
    },
    {
      title: t('trading_center.portfolio.columns.unrealized_pnl'),
      key: 'unrealized_pnl',
      render: (record: PortfolioPosition) => (
        <span style={{ color: getPnlColor(record.unrealized_pnl) }}>
          {formatMoney(record.unrealized_pnl)} / {formatPercent(record.unrealized_pnl_pct)}
        </span>
      ),
    },
  ];

  const industryColumns = [
    {
      title: t('trading_center.portfolio.columns.industry'),
      dataIndex: 'industry',
      key: 'industry',
    },
    {
      title: t('trading_center.portfolio.columns.weight'),
      dataIndex: 'weight',
      key: 'weight',
      render: (value: number) => formatPercent(value),
      sorter: (a: IndustryAllocation, b: IndustryAllocation) => a.weight - b.weight,
      defaultSortOrder: 'descend' as const,
    },
    {
      title: t('trading_center.portfolio.columns.market_value'),
      dataIndex: 'market_value',
      key: 'market_value',
      render: (value: number) => formatMoney(value),
    },
    {
      title: t('trading_center.portfolio.columns.position_count'),
      dataIndex: 'position_count',
      key: 'position_count',
    },
    {
      title: t('trading_center.portfolio.columns.stocks'),
      dataIndex: 'stock_codes',
      key: 'stock_codes',
      render: (stockCodes: string[]) => (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
          {stockCodes.map((stockCode) => <Tag key={stockCode}>{stockCode}</Tag>)}
        </div>
      ),
    },
  ];

  const rankingColumns = [
    {
      title: t('trading_center.portfolio.columns.stock'),
      key: 'stock',
      render: (record: PortfolioPosition) => `${record.stock_name} (${record.stock_code})`,
    },
    {
      title: t('trading_center.portfolio.columns.unrealized_pnl'),
      key: 'unrealized_pnl',
      render: (record: PortfolioPosition) => (
        <span style={{ color: getPnlColor(record.unrealized_pnl) }}>
          {formatMoney(record.unrealized_pnl)} / {formatPercent(record.unrealized_pnl_pct)}
        </span>
      ),
    },
    {
      title: t('trading_center.portfolio.columns.weight'),
      dataIndex: 'weight',
      key: 'weight',
      render: (value: number) => formatPercent(value),
    },
  ];

  const summary = overview?.summary;
  const riskMetrics = overview?.risk_metrics;
  const positions = overview?.positions ?? [];
  const industryAllocations = overview?.industry_allocations ?? [];

  return (
    <Spin spinning={loading}>
      <div className="flex flex-col gap-6">
        <div className="flex justify-end">
          <Button icon={<SyncOutlined />} loading={loading} onClick={() => loadOverview()}>
            {t('trading_center.refresh')}
          </Button>
        </div>

        <Row gutter={[16, 16]}>
          <Col xs={24} sm={12} lg={6}>
            <Card>
              <Statistic
                title={renderTitleWithTip(
                  t('trading_center.portfolio.total_assets'),
                  t('trading_center.portfolio.total_assets_tip'),
                )}
                value={formatMoney(summary?.total_assets)}
              />
            </Card>
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <Card>
              <Statistic title={t('trading_center.portfolio.cash_ratio')} value={formatPercent(summary?.cash_ratio)} />
            </Card>
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <Card>
              <Statistic title={t('trading_center.portfolio.position_ratio')} value={formatPercent(summary?.position_ratio)} />
            </Card>
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <Card>
              <Statistic title={t('trading_center.portfolio.position_count')} value={summary?.position_count ?? 0} />
            </Card>
          </Col>
        </Row>

        <Row gutter={[16, 16]}>
          <Col xs={24} sm={12} lg={6}>
            <Card>
              <Statistic
                title={t('trading_center.portfolio.risk_metrics.top_single_position_pct')}
                value={formatPercent(riskMetrics?.top_single_position_pct)}
              />
            </Card>
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <Card>
              <Statistic
                title={t('trading_center.portfolio.risk_metrics.top_industry_position_pct')}
                value={formatPercent(riskMetrics?.top_industry_position_pct)}
              />
            </Card>
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <Card>
              <Statistic
                title={t('trading_center.portfolio.risk_metrics.stop_loss_coverage_pct')}
                value={formatPercent(riskMetrics?.stop_loss_coverage_pct)}
              />
            </Card>
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <Card>
              <Statistic
                title={t('trading_center.portfolio.risk_metrics.estimated_volatility_20d')}
                value={formatPercent(riskMetrics?.estimated_volatility_20d)}
              />
            </Card>
          </Col>
        </Row>

        <Card title={t('trading_center.portfolio.positions_title')} variant="borderless" styles={{ body: { padding: 0 } }}>
          {positions.length > 0 ? (
            <Table
              columns={positionColumns}
              dataSource={positions}
              rowKey="stock_code"
              pagination={{ pageSize: 10 }}
              scroll={{ x: 900 }}
            />
          ) : (
            <Empty description={t('trading_center.portfolio.empty')} />
          )}
        </Card>

        <Card title={t('trading_center.portfolio.industry_title')} variant="borderless" styles={{ body: { padding: 0 } }}>
          {industryAllocations.length > 0 ? (
            <Table
              columns={industryColumns}
              dataSource={industryAllocations}
              rowKey="industry"
              pagination={false}
              scroll={{ x: 760 }}
            />
          ) : (
            <Empty description={t('trading_center.portfolio.empty')} />
          )}
        </Card>

        <Row gutter={[16, 16]}>
          <Col xs={24} lg={12}>
            <Card title={t('trading_center.portfolio.top_gainers_title')} variant="borderless" styles={{ body: { padding: 0 } }}>
              <Table
                columns={rankingColumns}
                dataSource={overview?.top_gainers ?? []}
                rowKey="stock_code"
                pagination={false}
                scroll={{ x: 560 }}
                locale={{ emptyText: t('trading_center.portfolio.empty') }}
              />
            </Card>
          </Col>
          <Col xs={24} lg={12}>
            <Card title={t('trading_center.portfolio.top_losers_title')} variant="borderless" styles={{ body: { padding: 0 } }}>
              <Table
                columns={rankingColumns}
                dataSource={overview?.top_losers ?? []}
                rowKey="stock_code"
                pagination={false}
                scroll={{ x: 560 }}
                locale={{ emptyText: t('trading_center.portfolio.empty') }}
              />
            </Card>
          </Col>
        </Row>
      </div>
    </Spin>
  );
};

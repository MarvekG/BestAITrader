import React, { useCallback, useEffect, useState } from 'react';
import {
  App as AntdApp,
  AutoComplete,
  Button,
  Card,
  Col,
  Form,
  Input,
  Modal,
  Row,
  Select,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
  TimePicker,
  Typography,
} from 'antd';
import {
  DatabaseOutlined,
  DeleteOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
  SettingOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import dayjs, { Dayjs } from 'dayjs';
import { warehouseApi, StockInfo } from '../api/warehouse';
import { marketApi } from '../api/market';
import { useSessionStore } from '../store/useSessionStore';
import { debateApi } from '../api/debate';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { formatErrorMessage } from '../utils/errorUtils';
import { DebateManagementPanel, StockResearchAnalysisPanel } from './warehouse/AnalysisPanels';
import { StockDataPage } from './StockDataPage';

type ApiError = {
  response?: {
    data?: {
      detail?: unknown;
    };
  };
};

type StockSearchItem = {
  'stock_basic.stock_code': string;
  'stock_basic.name': string;
};

type AutoAnalysisFrequency = 'daily' | 'weekly' | 'monthly';

type AutoConfigValues = {
  auto_analysis_enabled?: boolean;
  auto_analysis_frequency: AutoAnalysisFrequency;
  auto_analysis_time?: Dayjs;
  auto_analysis_trading_frequency: string;
  auto_analysis_trading_strategy: string;
  auto_analysis_run_immediately?: boolean;
};

const { Text } = Typography;

const getApiErrorDetail = (error: unknown) => (error as ApiError)?.response?.data?.detail;

export const StockWarehousePage: React.FC = () => {
  const { t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const renderTabLabel = (label: string) => (
    <span style={{ fontSize: 14, fontWeight: 600 }}>{label}</span>
  );
  const [stocks, setStocks] = useState<StockInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [isAddModalOpen, setIsAddModalOpen] = useState(false);
  const [newStockCode, setNewStockCode] = useState('');
  const [stockSearchOptions, setStockSearchOptions] = useState<Array<{ value: string; label: string }>>([]);

  // AI Analysis Modal State
  const [isAiAnalysisModalOpen, setIsAiAnalysisModalOpen] = useState(false);
  const [selectedStockForAnalysis, setSelectedStockForAnalysis] = useState<StockInfo | null>(null);
  const [tradingFrequency, setTradingFrequency] = useState(t('warehouse.freq_position_trading'));
  const [tradingStrategy, setTradingStrategy] = useState(t('warehouse.strategy_value'));
  const [isBatchAnalysis, setIsBatchAnalysis] = useState(false);
  const [isAutoConfigModalOpen, setIsAutoConfigModalOpen] = useState(false);
  const [selectedStockForAutoConfig, setSelectedStockForAutoConfig] = useState<StockInfo | null>(null);
  const [autoConfigForm] = Form.useForm();

  // Filter State
  const [filterCode, setFilterCode] = useState('');
  const [filterName, setFilterName] = useState('');

  // Selection State
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);


  const { message, modal } = AntdApp.useApp();
  const { createSession, setActiveSession } = useSessionStore();
  const navigate = useNavigate();
  const activeTab = searchParams.get('tab') || 'warehouse';

  const handleTabChange = (key: string) => {
    const nextSearchParams = new URLSearchParams(searchParams);
    if (key === 'warehouse') {
      nextSearchParams.delete('tab');
    } else {
      nextSearchParams.set('tab', key);
    }
    setSearchParams(nextSearchParams);
  };

  const handleCopyText = async (value?: string | null) => {
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      message.success(t('common.copy_success'));
    } catch (error) {
      message.error(error instanceof Error ? error.message : t('common.error'));
    }
  };

  const renderCopyableText = (value?: string | null) => {
    const displayValue = value || '-';
    return (
      <Text
        title={displayValue}
        role="button"
        tabIndex={0}
        style={{ cursor: value ? 'pointer' : 'default' }}
        onClick={() => void handleCopyText(value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            void handleCopyText(value);
          }
        }}
      >
        {displayValue}
      </Text>
    );
  };

  const fetchStocks = useCallback(async () => {
    setLoading(true);
    try {
      const data = await warehouseApi.list();
      setStocks(data);
    } catch (error: unknown) {
      const detail = getApiErrorDetail(error);
      const errorMessage = formatErrorMessage(detail) || t('common.error');
      message.error(errorMessage);
    } finally {
      setLoading(false);
    }
  }, [message, t]);

  useEffect(() => {
    if (activeTab !== 'warehouse') return;
    fetchStocks();
  }, [activeTab, fetchStocks]);

  const handleSearchStock = async (value: string) => {
    if (!value) {
      setStockSearchOptions([]);
      return;
    }
    try {
      const res = await marketApi.getDbStocks({ query: value, limit: 10 });
      setStockSearchOptions(
        (res.items as StockSearchItem[]).map((item) => ({
          value: item['stock_basic.stock_code'],
          label: `${item['stock_basic.stock_code']} - ${item['stock_basic.name']}`,
        })),
      );
    } catch (error) {
      console.error('Failed to search stocks:', error);
    }
  };

  const handleAddStock = async () => {
    if (!newStockCode) return;
    try {
      await warehouseApi.add({ stock_code: newStockCode });
      message.success(t('common.success'));
      setIsAddModalOpen(false);
      setNewStockCode('');
      setStockSearchOptions([]);
      fetchStocks();
    } catch (error: unknown) {
      // Extract error message from backend response
      const detail = getApiErrorDetail(error);
      const errorMessage = formatErrorMessage(detail) || t('common.error');
      message.error(errorMessage);
    }
  };

  const handleDelete = async (code: string) => {
    modal.confirm({
      title: t('warehouse.remove_confirm_title'),
      content: t('warehouse.remove_confirm_content', { stock: code }),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
      onOk: async () => {
        try {
          await warehouseApi.delete(code);
          message.success(t('common.success'));
          fetchStocks();
          // 如果删除的是当前选中的项，需要从选中列表中移除
          setSelectedRowKeys(prev => prev.filter(key => key !== code));
        } catch (error: unknown) {
          const detail = getApiErrorDetail(error);
          const errorMessage = formatErrorMessage(detail) || t('common.error');
          message.error(errorMessage);
        }
      }
    });
  };

  const handleSyncData = async (code: string) => {
    try {
      setLoading(true);
      message.loading({ content: t('common.syncing'), key: 'sync_data' });
      await marketApi.syncDbData(code);
      message.success({ content: t('common.sync_success'), key: 'sync_data' });
      fetchStocks();
    } catch (error: unknown) {
      const detail = getApiErrorDetail(error);
      const errorMessage = formatErrorMessage(detail) || t('common.sync_failed');
      message.error({ content: errorMessage, key: 'sync_data' });
    } finally {
      setLoading(false);
    }
  };

  const handleBatchSyncData = async () => {
    // 仅处理选中的股票
    const targets = filteredStocks.filter(s => selectedRowKeys.includes(s.stock_code));

    if (targets.length === 0) {
      message.warning(t('warehouse.please_select_stock'));
      return;
    }

    modal.confirm({
      title: t('warehouse.batch_sync_data_confirm_title'),
      content: t('warehouse.batch_sync_data_confirm_content', {
        count: targets.length
      }),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
      onOk: async () => {
        setLoading(true);
        let successCount = 0;
        let failCount = 0;

        message.loading({ content: t('warehouse.batch_sync_progress', { current: 0, total: targets.length }), key: 'batch_sync' });

        for (let i = 0; i < targets.length; i++) {
          const stock = targets[i];
          message.loading({ content: t('warehouse.batch_sync_progress_detail', { current: i + 1, total: targets.length, stock_name: stock.stock_name }), key: 'batch_sync' });
          try {
            await marketApi.syncDbData(stock.stock_code);
            successCount++;
          } catch (error) {
            failCount++;
            console.error(`Failed to sync stock ${stock.stock_code}:`, error);
          }
        }

        setLoading(false);
        message.success({
          content: t('warehouse.batch_sync_success', {
            success: successCount,
            fail: failCount
          }),
          key: 'batch_sync'
        });
        setSelectedRowKeys([]);
        fetchStocks();
      }
    });
  };

  const handleBatchDelete = async () => {
    if (selectedRowKeys.length === 0) return;

    modal.confirm({
      title: t('warehouse.batch_remove_confirm_title'),
      content: t('warehouse.batch_remove_confirm_content', {
        count: selectedRowKeys.length
      }),
      okText: t('common.confirm'),
      cancelText: t('common.cancel'),
      okButtonProps: { danger: true },
      onOk: async () => {
        setLoading(true);
        let successCount = 0;
        let failCount = 0;

        for (const code of selectedRowKeys) {
          try {
            await warehouseApi.delete(code as string);
            successCount++;
          } catch (error) {
            failCount++;
            console.error(`Failed to delete stock ${code}:`, error);
          }
        }

        setLoading(false);
        if (successCount > 0) {
          message.success(t('warehouse.batch_delete_success', {
            count: successCount
          }));
        }
        if (failCount > 0) {
          message.error(t('warehouse.batch_delete_fail', {
            count: failCount
          }));
        }

        setSelectedRowKeys([]);
        fetchStocks();
      }
    });
  };

  const handleInitShanghai50 = async () => {
    try {
      await warehouseApi.initShanghai50();
      message.success(t('common.success'));
      fetchStocks();
    } catch (error: unknown) {
      const detail = getApiErrorDetail(error);
      const errorMessage = formatErrorMessage(detail) || t('common.error');
      message.error(errorMessage);
    }
  };

  const handleBatchProcess = async () => {
    // 仅处理选中的股票
    const targets = filteredStocks.filter(s => selectedRowKeys.includes(s.stock_code));

    if (targets.length === 0) {
      message.warning(t('warehouse.please_select_stock'));
      return;
    }

    // 设置为批量模式并打开配置弹窗
    setIsBatchAnalysis(true);
    setIsAiAnalysisModalOpen(true);
  };

  const handleStartTrade = async (record: StockInfo) => {
    // 强制先打开配置模态框，因为交易偏好是必填的 | Force open config modal as preferences are mandatory
    setIsBatchAnalysis(false);
    setSelectedStockForAnalysis(record);
    setIsAiAnalysisModalOpen(true);
  };

  const handleStartAiAnalysis = (record: StockInfo) => {
    setIsBatchAnalysis(false);
    setSelectedStockForAnalysis(record);
    setIsAiAnalysisModalOpen(true);
  };

  const autoAnalysisFrequencyOptions = [
    { value: 'daily', label: t('warehouse.auto_analysis_daily') },
    { value: 'weekly', label: t('warehouse.auto_analysis_weekly') },
    { value: 'monthly', label: t('warehouse.auto_analysis_monthly') },
  ] as const;

  const parseAutoAnalysisTime = (value?: string) => dayjs(`2000-01-01T${value || '09:35'}:00`);

  // A-share trading hours: 09:30-11:30, 13:00-15:00
  const disabledTradingTime = () => ({
    disabledHours: () => [0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 17, 18, 19, 20, 21, 22, 23],
    disabledMinutes: (selectedHour: number) => {
      if (selectedHour === 9) return Array.from({ length: 30 }, (_, i) => i);
      if (selectedHour === 11) return Array.from({ length: 30 }, (_, i) => i + 31);
      if (selectedHour === 15) return Array.from({ length: 59 }, (_, i) => i + 1);
      return [];
    },
  });

  const handleOpenAutoConfig = (record: StockInfo) => {
    setSelectedStockForAutoConfig(record);
    autoConfigForm.setFieldsValue({
      auto_analysis_enabled: Boolean(record.auto_analysis_enabled),
      auto_analysis_frequency: record.auto_analysis_frequency || 'daily',
      auto_analysis_time: parseAutoAnalysisTime(record.auto_analysis_time),
      auto_analysis_trading_frequency: record.auto_analysis_trading_frequency || t('warehouse.freq_position_trading'),
      auto_analysis_trading_strategy: record.auto_analysis_trading_strategy || t('warehouse.strategy_value'),
      auto_analysis_run_immediately: Boolean(record.auto_analysis_run_immediately),
    });
    setIsAutoConfigModalOpen(true);
  };

  const handleSaveAutoConfig = async () => {
    if (!selectedStockForAutoConfig) return;
    try {
      const values = await autoConfigForm.validateFields() as AutoConfigValues;
      await warehouseApi.update(selectedStockForAutoConfig.stock_code, {
        auto_analysis_enabled: Boolean(values.auto_analysis_enabled),
        auto_analysis_frequency: values.auto_analysis_frequency,
        auto_analysis_time: values.auto_analysis_time?.format('HH:mm') || '09:35',
        auto_analysis_trading_frequency: values.auto_analysis_trading_frequency,
        auto_analysis_trading_strategy: values.auto_analysis_trading_strategy,
        auto_analysis_run_immediately: Boolean(values.auto_analysis_run_immediately),
      });
      message.success(t('common.success'));
      setIsAutoConfigModalOpen(false);
      setSelectedStockForAutoConfig(null);
      fetchStocks();
    } catch (error: unknown) {
      const detail = getApiErrorDetail(error);
      const errorMessage = formatErrorMessage(detail) || t('common.error');
      message.error(errorMessage);
    }
  };

  const handleConfirmAiAnalysis = async () => {
    try {
      setIsAiAnalysisModalOpen(false);
      setLoading(true);

      if (isBatchAnalysis) {
        // 批量模式
        const targets = filteredStocks.filter(s => selectedRowKeys.includes(s.stock_code));
        let successCount = 0;
        let failCount = 0;

        message.loading({ content: t('warehouse.batch_process_progress', { current: 0, total: targets.length }), key: 'batch_process' });

        for (let i = 0; i < targets.length; i++) {
          const stock = targets[i];
          message.loading({ content: t('warehouse.batch_process_progress_detail', { current: i + 1, total: targets.length, stock_name: stock.stock_name }), key: 'batch_process' });
          try {
            const session = await createSession(
              stock.stock_code,
              stock.stock_name,
              tradingFrequency,
              tradingStrategy
            );
            await debateApi.run({
              session_id: session.session_id,
              stock_code: stock.stock_code,
              simplified: false,
              trading_frequency: tradingFrequency,
              trading_strategy: tradingStrategy,
            });
            successCount++;
          } catch (error) {
            failCount++;
            console.error(`Failed to batch process stock ${stock.stock_code}:`, error);
          }
        }

        message.success({
          content: t('warehouse.batch_process_success', {
            success: successCount,
            fail: failCount
          }),
          key: 'batch_process'
        });
        setSelectedRowKeys([]);
        fetchStocks();
      } else {
        // 单股模式
        if (!selectedStockForAnalysis) return;
        message.loading({ content: t('warehouse.starting_ai_analysis'), key: 'ai_analysis' });
        const session = await createSession(
          selectedStockForAnalysis.stock_code,
          selectedStockForAnalysis.stock_name,
          tradingFrequency,
          tradingStrategy
        );
        await debateApi.run({
          session_id: session.session_id,
          stock_code: selectedStockForAnalysis.stock_code,
          simplified: false,
          trading_frequency: tradingFrequency,
          trading_strategy: tradingStrategy,
        });
        setActiveSession(session);
        message.success({ content: t('common.success'), key: 'ai_analysis' });
        navigate(`/dashboard?session_id=${encodeURIComponent(session.session_id)}`);
      }
    } catch (error: unknown) {
      const detail = getApiErrorDetail(error);
      const errorMessage = formatErrorMessage(detail) || t('common.error');
      const msgKey = isBatchAnalysis ? 'batch_process' : 'ai_analysis';
      message.error({ content: errorMessage, key: msgKey });
    } finally {
      setLoading(false);
      setSelectedStockForAnalysis(null);
      setIsBatchAnalysis(false);
    }
  };

  // Clear all filters
  const handleClearFilters = () => {
    setFilterCode('');
    setFilterName('');
  };

  // Filter stocks based on code and name
  const filteredStocks = stocks.filter(stock => {
    const codeMatch = !filterCode || stock.stock_code.toLowerCase().includes(filterCode.toLowerCase());
    const nameMatch = !filterName || stock.stock_name.toLowerCase().includes(filterName.toLowerCase());
    return codeMatch && nameMatch;
  });

  const columns = [
    { title: t('stock_basic.stock_code'), dataIndex: 'stock_code', width: 100, render: renderCopyableText },
    { title: t('stock_basic.name'), dataIndex: 'stock_name', width: 120, render: renderCopyableText },
    { title: t('stock_basic.industry'), dataIndex: 'industry', width: 100},
    { title: t('stock_basic.market'), dataIndex: 'market', render: (v: string) => <Tag color="blue">{v}</Tag> },
    {
      title: t('warehouse.auto_analysis'),
      key: 'auto_analysis',
      width: 180,
      render: (_value: unknown, record: StockInfo) => (
        <Space size={6}>
          <Tag color={record.auto_analysis_enabled ? 'green' : 'default'}>
            {record.auto_analysis_enabled
              ? t('warehouse.auto_analysis_enabled')
              : t('warehouse.auto_analysis_disabled')}
          </Tag>
          <Button
            size="small"
            icon={<SettingOutlined />}
            onClick={() => handleOpenAutoConfig(record)}
          >
            {t('warehouse.config')}
          </Button>
        </Space>
      )
    },
    {
      title: t('common.action'),
      key: 'actions',
      render: (_value: unknown, record: StockInfo) => (
        <Space>
          <Button
            type="primary"
            size="small"
            icon={<PlayCircleOutlined />}
            onClick={() => handleStartTrade(record)}
          >
            {t('warehouse.trade')}
          </Button>
          <Button
            type="primary"
            size="small"
            style={{ backgroundColor: '#ff4d4f', borderColor: '#ff4d4f' }}
            onClick={() => navigate(`/trading?stock_code=${record.stock_code}`)}
          >
            {t('warehouse.place_order')}
          </Button>
          <Button
            size="small"
            onClick={() => navigate(`/warehouse?tab=stock-data&stock_code=${encodeURIComponent(record.stock_code)}`)}
          >
            {t('warehouse.view_data')}
          </Button>
          <Button
            size="small"
            icon={<SyncOutlined />}
            onClick={() => handleSyncData(record.stock_code)}
          >
            {t('warehouse.sync_data')}
          </Button>
          <Button
            size="small"
            icon={<PlayCircleOutlined />}
            onClick={() => handleStartAiAnalysis(record)}
          >
            {t('warehouse.start_ai_analysis')}
          </Button>
          <Button
            danger
            size="small"
            icon={<DeleteOutlined />}
            onClick={() => handleDelete(record.stock_code)}
          />
        </Space>
      )
    }
  ];

  const warehouseTab = (
    <>
      <Row justify="end" align="middle" style={{ marginBottom: 24 }}>
        <Col>
          <Space>
            {selectedRowKeys.length > 0 && (
              <Button
                danger
                icon={<DeleteOutlined />}
                onClick={handleBatchDelete}
              >
                {t('warehouse.batch_delete')} ({selectedRowKeys.length})
              </Button>
            )}
            <Button
              icon={<SyncOutlined />}
              onClick={handleBatchSyncData}
              type={selectedRowKeys.length > 0 ? "default" : "dashed"}
              disabled={selectedRowKeys.length === 0}
            >
              {t('warehouse.batch_sync_data')}
              {selectedRowKeys.length > 0 ? ` (${selectedRowKeys.length})` : ''}
            </Button>
            <Button
              icon={<RobotOutlined />}
              onClick={handleBatchProcess}
              type={selectedRowKeys.length > 0 ? "primary" : "default"}
            >
              {t('warehouse.batch_process')}
              {selectedRowKeys.length > 0 ? ` (${selectedRowKeys.length})` : ''}
            </Button>
            <Button icon={<ReloadOutlined />} onClick={fetchStocks}>{t('warehouse.refresh')}</Button>

            <Button icon={<DatabaseOutlined />} onClick={handleInitShanghai50}>{t('warehouse.init_sh50')}</Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={() => setIsAddModalOpen(true)}>
              {t('warehouse.add_stock')}
            </Button>
          </Space>
        </Col>
      </Row>

      {/* Filter Section */}
      <Card style={{ marginBottom: 16 }}>
        <Space size="middle">
          <Input.Search
            placeholder={t('warehouse.filter_code')}
            value={filterCode}
            onChange={(e) => setFilterCode(e.target.value)}
            onSearch={(value) => setFilterCode(value)}
            style={{ width: 200 }}
            allowClear
          />
          <Input.Search
            placeholder={t('warehouse.filter_name')}
            value={filterName}
            onChange={(e) => setFilterName(e.target.value)}
            onSearch={(value) => setFilterName(value)}
            style={{ width: 200 }}
            allowClear
          />
          <Button onClick={handleClearFilters}>{t('warehouse.clear_filters')}</Button>
          <span style={{ color: '#888' }}>
            {t('warehouse.showing_count', { current: filteredStocks.length, total: stocks.length })}
          </span>
        </Space>
      </Card>

      <Card styles={{ body: { padding: 0 } }}>
        <Table
          rowSelection={{
            selectedRowKeys,
            onChange: (keys) => setSelectedRowKeys(keys)
          }}
          columns={columns}
          dataSource={filteredStocks}
          rowKey="stock_code"
          loading={loading}
          pagination={{ pageSize: 10 }}
        />
      </Card>
    </>
  );

  return (
    <div style={{ padding: 24 }}>
      <Tabs
        activeKey={activeTab}
        onChange={handleTabChange}
        items={[
          { key: 'warehouse', label: renderTabLabel(t('warehouse.title')), children: warehouseTab },
          {
            key: 'debate-management',
            label: renderTabLabel(t('session.analysis_sessions_tab')),
            children: <DebateManagementPanel isActive={activeTab === 'debate-management'} />,
          },
          {
            key: 'stock-research-analysis',
            label: renderTabLabel(t('session.stock_analysis_tab')),
            children: <StockResearchAnalysisPanel isActive={activeTab === 'stock-research-analysis'} />,
          },
          { key: 'stock-data', label: renderTabLabel(t('layout.menu.stock_data')), children: <StockDataPage /> },
        ]}
      />

      <Modal
        title={t('warehouse.add_stock_title')}
        open={isAddModalOpen}
        width={360}
        onOk={handleAddStock}
        onCancel={() => {
          setIsAddModalOpen(false);
          setNewStockCode('');
          setStockSearchOptions([]);
        }}
        okText={t('common.confirm')}
        cancelText={t('common.cancel')}
      >
        <AutoComplete
          options={stockSearchOptions}
          onSearch={handleSearchStock}
          onSelect={(value) => {
            setNewStockCode(value);
          }}
          value={newStockCode}
          onChange={(value) => setNewStockCode(value)}
          allowClear
          style={{ width: '100%' }}
        >
          <Input
            placeholder={t('common.filter_by_stock_code_or_name')}
            onPressEnter={handleAddStock}
          />
        </AutoComplete>
      </Modal>

      <Modal
        title={t('warehouse.analysis_settings_title')}
        open={isAiAnalysisModalOpen}
        onOk={handleConfirmAiAnalysis}
        onCancel={() => setIsAiAnalysisModalOpen(false)}
        okText={t('common.confirm')}
        cancelText={t('common.cancel')}
      >
        <Form layout="vertical">
          <Form.Item label={t('warehouse.trading_frequency')}>
            <Select value={tradingFrequency} onChange={setTradingFrequency}>
              <Select.Option value={t('warehouse.freq_day_trading')}>{t('warehouse.freq_day_trading')}</Select.Option>
              <Select.Option value={t('warehouse.freq_swing_trading')}>{t('warehouse.freq_swing_trading')}</Select.Option>
              <Select.Option value={t('warehouse.freq_position_trading')}>{t('warehouse.freq_position_trading')}</Select.Option>
            </Select>
            <div style={{ marginTop: 8, fontSize: '12px', color: '#888' }}>
              {tradingFrequency === t('warehouse.freq_day_trading') && t('warehouse.freq_day_trading_desc')}
              {tradingFrequency === t('warehouse.freq_swing_trading') && t('warehouse.freq_swing_trading_desc')}
              {tradingFrequency === t('warehouse.freq_position_trading') && t('warehouse.freq_position_trading_desc')}
            </div>
          </Form.Item>
          <Form.Item label={t('warehouse.trading_strategy')}>
            <Select value={tradingStrategy} onChange={setTradingStrategy}>
              <Select.Option value={t('warehouse.strategy_value')}>{t('warehouse.strategy_value')}</Select.Option>
              <Select.Option value={t('warehouse.strategy_growth')}>{t('warehouse.strategy_growth')}</Select.Option>
              <Select.Option value={t('warehouse.strategy_trend')}>{t('warehouse.strategy_trend')}</Select.Option>
            </Select>
            <div style={{ marginTop: 8, fontSize: '12px', color: '#888' }}>
              {tradingStrategy === t('warehouse.strategy_value') && t('warehouse.strategy_value_desc')}
              {tradingStrategy === t('warehouse.strategy_growth') && t('warehouse.strategy_growth_desc')}
              {tradingStrategy === t('warehouse.strategy_trend') && t('warehouse.strategy_trend_desc')}
            </div>
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={t('warehouse.auto_analysis_config_title')}
        open={isAutoConfigModalOpen}
        onOk={handleSaveAutoConfig}
        onCancel={() => {
          setIsAutoConfigModalOpen(false);
          setSelectedStockForAutoConfig(null);
        }}
        okText={t('common.confirm')}
        cancelText={t('common.cancel')}
      >
        <Form form={autoConfigForm} layout="vertical">
          <Form.Item
            name="auto_analysis_enabled"
            label={t('warehouse.auto_analysis_enabled_label')}
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>
          <Form.Item
            name="auto_analysis_run_immediately"
            label={t('warehouse.auto_analysis_run_immediately')}
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>
          <Form.Item
            name="auto_analysis_frequency"
            label={t('warehouse.auto_analysis_frequency')}
            rules={[{ required: true }]}
          >
            <Select options={[...autoAnalysisFrequencyOptions]} />
          </Form.Item>
          <Form.Item
            name="auto_analysis_time"
            label={t('warehouse.auto_analysis_time')}
            rules={[{ required: true }]}
          >
            <TimePicker format="HH:mm" minuteStep={5} style={{ width: '100%' }} disabledTime={disabledTradingTime} />
          </Form.Item>
          <Form.Item
            name="auto_analysis_trading_frequency"
            label={t('warehouse.trading_frequency')}
            rules={[{ required: true }]}
          >
            <Select>
              <Select.Option value={t('warehouse.freq_day_trading')}>{t('warehouse.freq_day_trading')}</Select.Option>
              <Select.Option value={t('warehouse.freq_swing_trading')}>{t('warehouse.freq_swing_trading')}</Select.Option>
              <Select.Option value={t('warehouse.freq_position_trading')}>{t('warehouse.freq_position_trading')}</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item
            name="auto_analysis_trading_strategy"
            label={t('warehouse.trading_strategy')}
            rules={[{ required: true }]}
          >
            <Select>
              <Select.Option value={t('warehouse.strategy_value')}>{t('warehouse.strategy_value')}</Select.Option>
              <Select.Option value={t('warehouse.strategy_growth')}>{t('warehouse.strategy_growth')}</Select.Option>
              <Select.Option value={t('warehouse.strategy_trend')}>{t('warehouse.strategy_trend')}</Select.Option>
            </Select>
          </Form.Item>
        </Form>
      </Modal>

    </div>
  );
};

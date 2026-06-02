import React, { useState, useEffect } from 'react';
import { useLocation } from 'react-router-dom';
import {
    Card,
    Table,
    Button,
    message,
    Tag,
    Space,
    Modal,
    Form,
    Input,
    InputNumber,
    Radio,
    Select,
    Spin,
    Switch,
    Descriptions,
    Tooltip,
    theme,
    Tabs,
} from 'antd';
import { SyncOutlined, PlusOutlined, DeleteOutlined, InfoCircleOutlined, HistoryOutlined, QuestionCircleOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { accountsApi, AccountAssets, Position } from '../api/accounts';
import { tradingApi, OrderHistory, OrderRequest } from '../api/trading';
import { riskControlApi, RiskControlConfigUpdate, RiskControlHit, RiskControlPolicy } from '../api/riskControl';
import { marketApi } from '../api/market';
import { WebSocketMessage } from '../services/websocket';
import { useWebSocketSubscription } from '../hooks/useWebSocketSubscription';
import { getApiErrorMessage } from '../utils/errorUtils';
import { PerformanceTab } from '../features/trading/PerformanceTab';
import { PortfolioOverviewTab } from '../features/trading/PortfolioOverviewTab';

interface OrderFormValues {
    action: 'buy' | 'sell';
    stock_code: string;
    shares: number;
    stop_loss_pct?: number;
}

export const SimulatedTradingPage: React.FC = () => {
    const { t } = useTranslation();
    const location = useLocation();
    const {
        token: {
            colorBgContainer,
            colorFillQuaternary,
            colorPrimary,
            colorText,
            colorTextSecondary,
            colorBorder,
        },
    } = theme.useToken();
    const [loading, setLoading] = useState(false);

    // State
    const [account, setAccount] = useState<AccountAssets | null>(null);
    const [positions, setPositions] = useState<Position[]>([]);
    const [orders, setOrders] = useState<OrderHistory[]>([]);
    const [filterStockCode, setFilterStockCode] = useState<string | null>(null);

    // Modals
    const [isOrderModalVisible, setIsOrderModalVisible] = useState(false);
    const [isRiskControlModalVisible, setIsRiskControlModalVisible] = useState(false);
    const [orderForm] = Form.useForm();
    const [riskControlForm] = Form.useForm();
    const [submittingOrder, setSubmittingOrder] = useState(false);
    const [savingRiskControl, setSavingRiskControl] = useState(false);
    const orderAction = Form.useWatch('action', orderForm);
    const riskPolicyOptions = [
        { value: 'block', label: t('trading_center.risk_control.policies.block') },
        { value: 'off', label: t('trading_center.risk_control.policies.off') },
    ];

    // Data Loading
    const loadData = async (showLoading = true) => {
        if (showLoading) setLoading(true);
        try {
            const [accRes, posRes, ordRes] = await Promise.all([
                accountsApi.getMyAssets(),
                accountsApi.getMyPositions(),
                tradingApi.getMyOrders()
            ]);
            setAccount(accRes);
            setPositions(posRes);
            setOrders(ordRes);
        } catch (error) {
            console.error('Failed to fetch trading data:', error);
            message.error('加载交易数据失败');
        } finally {
            if (showLoading) setLoading(false);
        }
    };

    const loadRiskControlConfig = async () => {
        try {
            const config = await riskControlApi.getConfig();
            riskControlForm.setFieldsValue({
                enabled: config.enabled,
                max_single_position_pct: Number((config.max_single_position_pct * 100).toFixed(2)),
                max_industry_position_pct: Number((config.max_industry_position_pct * 100).toFixed(2)),
                min_cash_pct: Number((config.min_cash_pct * 100).toFixed(2)),
                require_stop_loss: config.require_stop_loss,
                stop_loss_warning_pct: Number((config.stop_loss_warning_pct * 100).toFixed(2)),
                rule_policies: config.rule_policies,
            });
        } catch (error) {
            console.error('Failed to load risk control config:', error);
            message.error(t('trading_center.risk_control.load_failed'));
        }
    };

    useEffect(() => {
        loadData();
    }, []);

    // Handle initial stock_code from URL
    useEffect(() => {
        const params = new URLSearchParams(location.search);
        const stockCode = params.get('stock_code');
        if (stockCode) {
            orderForm.setFieldsValue({
                action: 'buy',
                stock_code: stockCode,
                shares: 100,
                stop_loss_pct: undefined,
            });
            setIsOrderModalVisible(true);

            // Clean up URL to avoid re-opening on manual refresh if desired, 
            // but usually keeping it is fine for back navigation.
        }
    }, [location.search, orderForm]);

    useWebSocketSubscription('position_update', (msg: WebSocketMessage) => {
        console.log('Position update received:', msg);
        loadData(false);
    });
    useWebSocketSubscription('order_status', (msg: WebSocketMessage) => {
        console.log('Order update received:', msg);
        loadData(false);
    });
    useWebSocketSubscription('trade_executed', (msg: WebSocketMessage) => {
        console.log('Trade executed:', msg);
        loadData(false);
    });

    // Order Modal Handler
    const resolveStopLossPrice = async (stockCode: string, stopLossPct?: number) => {
        if (!stopLossPct || stopLossPct <= 0) {
            return undefined;
        }

        const response = await marketApi.getRealtimeMarket({ stock_code: stockCode, limit: 1 });
        const latestPrice = Number(response.items?.[0]?.current_price);

        if (!Number.isFinite(latestPrice) || latestPrice <= 0) {
            throw new Error('无法获取最新价，不能计算止损价');
        }

        return Number((latestPrice * (1 - stopLossPct / 100)).toFixed(2));
    };

    const handlePlaceOrder = async (values: OrderFormValues) => {
        setSubmittingOrder(true);
        try {
            const stopLoss = values.action === 'buy'
                ? await resolveStopLossPrice(values.stock_code, values.stop_loss_pct)
                : undefined;
            const orderPayload: OrderRequest = {
                // session_id is deliberately omitted for global manual orders
                stock_code: values.stock_code,
                stock_name: '', // Optional/Omitted: Let backend fill or use symbol
                action: values.action,
                order_type: 'market',
                price: 0, // Market order doesn't need price
                shares: values.shares,
                stop_loss: stopLoss,
            };
            const riskResult = await riskControlApi.evaluateOrder(orderPayload);

            if (riskResult.blocks.length > 0) {
                message.error(formatRiskControlMessages(riskResult.blocks));
                return;
            }

            await submitOrder(orderPayload);
        } catch (error) {
            const detail = getApiErrorMessage(error, '未知错误');
            message.error(`订单提交失败: ${detail}`);
        } finally {
            setSubmittingOrder(false);
        }
    };

    const submitOrder = async (orderPayload: Parameters<typeof tradingApi.placeOrder>[0]) => {
        await tradingApi.placeOrder(orderPayload);
        message.success('已提交订单');
        setIsOrderModalVisible(false);
        orderForm.resetFields();
        loadData(false);
    };

    const translateRiskControlParam = (value: string) => {
        const valueKey = `trading_center.risk_control.values.${value}`;
        return t(valueKey, { defaultValue: value });
    };

    const formatRiskControlMessages = (items: RiskControlHit[]) => (
        items.map((item) => t(item.message_key, {
            ...item.params,
            current: translateRiskControlParam(item.params.current),
            limit: translateRiskControlParam(item.params.limit),
        })).join('\n')
    );

    const renderRiskControlLabel = (labelKey: string, tipKey: string) => (
        <span>
            {t(labelKey)}
            <Tooltip title={t(tipKey)}>
                <QuestionCircleOutlined style={{ marginLeft: 4, cursor: 'help', fontSize: '12px' }} />
            </Tooltip>
        </span>
    );

    const renderRiskControlRule = (
        rule: string,
        controlItem: React.ReactNode,
        labelKey: string,
        tipKey: string,
        valuePropName?: 'checked'
    ) => (
        <div
            style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
                gap: 16,
                padding: 16,
                border: `1px solid ${colorBorder}`,
                borderRadius: 8,
                background: colorFillQuaternary,
                marginBottom: 12,
            }}
        >
            <Form.Item
                label={renderRiskControlLabel(labelKey, tipKey)}
                name={rule}
                valuePropName={valuePropName}
                style={{ marginBottom: 0 }}
            >
                {controlItem}
            </Form.Item>
            <Form.Item
                label={t('trading_center.risk_control.policy')}
                name={['rule_policies', rule]}
                tooltip={t('trading_center.risk_control.policy_tip')}
                style={{ marginBottom: 0 }}
            >
                <Select options={riskPolicyOptions} />
            </Form.Item>
        </div>
    );

    const handleOpenRiskControl = async () => {
        setIsRiskControlModalVisible(true);
        await loadRiskControlConfig();
    };

    const handleSaveRiskControl = async (
        values: Record<string, number | boolean | Record<string, RiskControlPolicy>>
    ) => {
        setSavingRiskControl(true);
        try {
            const payload: RiskControlConfigUpdate = {
                enabled: Boolean(values.enabled),
                max_single_position_pct: Number(values.max_single_position_pct || 0) / 100,
                max_industry_position_pct: Number(values.max_industry_position_pct || 0) / 100,
                min_cash_pct: Number(values.min_cash_pct || 0) / 100,
                require_stop_loss: Boolean(values.require_stop_loss),
                stop_loss_warning_pct: Number(values.stop_loss_warning_pct || 0) / 100,
                rule_policies: values.rule_policies as Record<string, RiskControlPolicy>,
            };
            await riskControlApi.updateConfig(payload);
            message.success(t('trading_center.risk_control.save_success'));
            setIsRiskControlModalVisible(false);
        } catch (error) {
            console.error('Failed to save risk control config:', error);
            message.error(t('trading_center.risk_control.save_failed'));
        } finally {
            setSavingRiskControl(false);
        }
    };

    const handleFastSell = (record: Position) => {
        const executableShares = Math.max(
            0,
            Math.min(record.current_shares || 0, record.available_shares || 0)
        );
        orderForm.setFieldsValue({
            action: 'sell',
            stock_code: record.stock_code,
            shares: executableShares,
            stop_loss_pct: undefined,
        });
        setIsOrderModalVisible(true);
    };

    const handleFastBuy = (record: Position) => {
        orderForm.setFieldsValue({
            action: 'buy',
            stock_code: record.stock_code,
            shares: 100,
            stop_loss_pct: undefined,
        });
        setIsOrderModalVisible(true);
    };

    // Reset Account Handler
    const handleResetAccount = () => {
        Modal.confirm({
            title: t('trading_center.modals.reset_account.title'),
            icon: <InfoCircleOutlined style={{ color: '#faad14' }} />,
            content: t('trading_center.modals.reset_account.content'),
            okText: t('trading_center.modals.reset_account.confirm'),
            cancelText: t('trading_center.modals.reset_account.cancel'),
            onOk: async () => {
                try {
                    await accountsApi.resetAccount();
                    message.success('账户已重置');
                    loadData();
                } catch {
                    message.error('重置账户失败');
                }
            }
        });
    };

    // Columns Configuration
    const positionColumns = [
        {
            title: t('trading_center.positions.symbol'),
            dataIndex: 'stock_code',
            key: 'stock_code',
            render: (text: string) => <a style={{ color: '#1890ff' }}>{text}</a>,
        },
        {
            title: t('trading_center.positions.name'),
            dataIndex: 'stock_name',
            key: 'stock_name',
        },
        {
            title: t('trading_center.positions.quantity'),
            key: 'quantity',
            render: (record: Position) => (
                <span>
                    {record.current_shares} <span style={{ color: '#8c8c8c' }}>({t('trading_center.positions.available')} {record.available_shares})</span>
                </span>
            )
        },
        {
            title: t('trading_center.positions.avg_price'),
            dataIndex: 'avg_cost',
            key: 'avg_cost',
            render: (val: number) => {
                const num = Number(val);
                return isNaN(num) ? '-' : `¥${num.toFixed(2)}`;
            }
        },
        {
            title: t('trading_center.positions.latest_price'),
            dataIndex: 'current_price',
            key: 'current_price',
            render: (val: number) => {
                const num = Number(val);
                return isNaN(num) ? '-' : `¥${num.toFixed(2)}`;
            }
        },
        {
            title: t('trading_center.positions.stop_loss'),
            dataIndex: 'stop_loss',
            key: 'stop_loss',
            render: (val: number | null | undefined) => {
                const num = Number(val);
                return isNaN(num) || num <= 0 ? '-' : `¥${num.toFixed(2)}`;
            }
        },
        {
            title: t('trading_center.positions.market_value'),
            dataIndex: 'market_value',
            key: 'market_value',
            render: (val: number) => {
                const num = Number(val);
                return isNaN(num) ? '-' : `¥${num.toFixed(2)}`;
            }
        },
        {
            title: t('trading_center.positions.floating_pnl'),
            dataIndex: 'unrealized_pnl',
            key: 'unrealized_pnl',
            render: (val: number) => {
                const num = Number(val);
                if (isNaN(num)) return '-';
                return (
                    <span style={{ color: num > 0 ? '#cf1322' : num < 0 ? '#3f8600' : 'inherit' }}>
                        {num > 0 ? '+' : ''}{num.toFixed(2)}
                    </span>
                );
            }
        },
        {
            title: t('trading_center.positions.actions'),
            key: 'action',
            render: (record: Position) => (
                <Space size="middle">
                    <a style={{ color: '#faad14' }} onClick={() => setFilterStockCode(record.stock_code)}>{t('trading_center.positions.details')}</a>
                    <a style={{ color: '#1890ff' }} onClick={() => handleFastBuy(record)}>{t('trading_center.positions.buy')}</a>
                    <a style={{ color: '#cf1322' }} onClick={() => handleFastSell(record)}>{t('trading_center.positions.sell')}</a>
                </Space>
            )
        }
    ];

    const orderColumns = [
        {
            title: t('trading_center.orders.time'),
            dataIndex: 'created_at',
            key: 'created_at',
            render: (text: string) => new Date(text).toLocaleString(),
        },
        {
            title: t('trading_center.orders.direction'),
            dataIndex: 'action',
            key: 'action',
            render: (action: string) => (
                <Tag color={action === 'buy' ? 'processing' : 'error'}>
                    {action === 'buy' ? t('trading_center.orders.buy') : t('trading_center.orders.sell')}
                </Tag>
            )
        },
        {
            title: t('trading_center.orders.symbol'),
            dataIndex: 'stock_code',
            key: 'stock_code',
            render: (text: string) => <a style={{ color: '#1890ff' }}>{text}</a>,
        },
        {
            title: t('trading_center.orders.name'),
            dataIndex: 'stock_name',
            key: 'stock_name',
        },
        {
            title: t('trading_center.orders.source'),
            dataIndex: 'source',
            key: 'source',
            width: 120,
            // 直接原样显示 source 字段，无值时降级用 session_id 前8位
            // Show source field as-is; fallback to first 8 chars of session_id
            render: (source: string | null | undefined, record: OrderHistory) =>
                source || (record.session_id ? record.session_id.substring(0, 8) : '-')
        },
        {
            title: t('trading_center.orders.price'),
            dataIndex: 'avg_fill_price',
            key: 'avg_fill_price',
            render: (val: number | null) => {
                const num = Number(val);
                return !isNaN(num) && num !== 0 ? num.toFixed(2) : '-';
            }
        },
        {
            title: t('trading_center.orders.quantity'),
            key: 'shares',
            render: (_: unknown, record: OrderHistory) => `${record.filled_shares} / ${record.shares}`
        },
        {
            title: t('trading_center.orders.realized_pnl'),
            dataIndex: 'realized_pnl',
            key: 'realized_pnl',
            render: (val: number | undefined, record: OrderHistory) => {
                if (record.action === 'sell' && record.status === 'filled') {
                    const pnl = Number(val);
                    if (isNaN(pnl)) return '-';
                    const color = pnl > 0 ? '#cf1322' : pnl < 0 ? '#389e0d' : 'inherit';
                    const prefix = pnl > 0 ? '+' : '';
                    return <span style={{ color, fontWeight: 'bold' }}>{prefix}{pnl.toFixed(2)}</span>;
                }
                return '-';
            }
        },
        {
            title: t('trading_center.orders.status'),
            dataIndex: 'status',
            key: 'status',
            render: (status: string, record: OrderHistory) => {
                let color = 'default';
                let label = status;
                if (status === 'filled') { color = 'success'; label = t('trading_center.orders.status_filled'); }
                if (status === 'pending') { color = 'warning'; label = t('trading_center.orders.status_pending'); }
                if (status === 'cancelled') { color = 'default'; label = t('trading_center.orders.status_cancelled'); }
                if (status === 'rejected') { color = 'error'; label = t('trading_center.orders.status_rejected'); }

                if (status === 'rejected' && record.remark) {
                    // Normalize the reason to serve as i18n key, fallback to english remark if not mapped
                    const reasonKey = record.remark.replace(/ /g, '_');
                    const translatedReason = t(`trading_center.orders.reasons.${reasonKey}`, record.remark) as string;
                    return (
                        <Tooltip title={translatedReason}>
                            <Tag color={color} style={{ cursor: 'pointer' }}>{label}</Tag>
                        </Tooltip>
                    );
                }

                return <Tag color={color}>{label}</Tag>;
            }
        },
        {
            title: t('trading_center.orders.remark'),
            dataIndex: 'remark',
            key: 'remark',
            render: (remark: string | null | undefined, record: OrderHistory) => {
                if (record.status === 'rejected' && remark) {
                    const reasonKey = remark.replace(/ /g, '_');
                    return <span style={{ color: '#cf1322', fontSize: '12px' }}>{t(`trading_center.orders.reasons.${reasonKey}`, remark) as string}</span>;
                }
                return remark || '-';
            }
        },
    ];

    return (
        <Spin spinning={loading}>
            {/* Header Area */}
            <div className="flex justify-between items-center mb-6">
                <div className="flex items-center space-x-3">
                    <HistoryOutlined style={{ fontSize: '24px', color: colorPrimary }} />
                    <h1 className="text-2xl font-bold text-white m-0" style={{ color: colorText }}>
                        {t('trading_center.title')}
                    </h1>
                </div>
                <Space>
                    <Button onClick={handleOpenRiskControl}>
                        {t('trading_center.risk_control.settings')}
                    </Button>
                    <Button type="primary" icon={<PlusOutlined />} onClick={() => {
                        orderForm.resetFields();
                        orderForm.setFieldsValue({ action: 'buy', shares: 100, stop_loss_pct: undefined });
                        setIsOrderModalVisible(true);
                    }}>
                        {t('trading_center.place_order')}
                    </Button>
                    <Button danger icon={<DeleteOutlined />} onClick={handleResetAccount}>
                        {t('trading_center.reset_account')}
                    </Button>
                </Space>
            </div>

            <Tabs
                defaultActiveKey="trading"
                items={[
                    {
                        key: 'trading',
                        label: t('trading_center.tabs.trading'),
                        children: (
                            <>
                                <div className="flex justify-end" style={{ marginTop: 16, marginBottom: 16 }}>
                                    <Button type="text" style={{ color: colorPrimary }} icon={<SyncOutlined />} onClick={() => loadData()}>
                                        {t('trading_center.refresh')}
                                    </Button>
                                </div>

                                {/* Risk Warning Alert */}
                                <div style={{ backgroundColor: '#fffbe6', border: '1px solid #ffe58f', padding: '16px 24px', borderRadius: '8px', marginBottom: '24px' }}>
                                    <div style={{ display: 'flex', alignItems: 'flex-start' }}>
                                        <InfoCircleOutlined style={{ color: '#faad14', fontSize: '20px', marginRight: '16px', marginTop: '2px' }} />
                                        <div>
                                            <h4 style={{ color: '#d46b08', margin: '0 0 8px 0', fontSize: '16px', fontWeight: 'bold' }}>{t('trading_center.risk_warning.title')}</h4>
                                            <ul style={{ color: '#d46b08', margin: 0, paddingLeft: '20px', fontSize: '14px', lineHeight: '1.6' }}>
                                                <li><b>{t('trading_center.risk_warning.p1_title')}</b> {t('trading_center.risk_warning.p1_desc')}</li>
                                                <li><b>{t('trading_center.risk_warning.p2_title')}</b> {t('trading_center.risk_warning.p2_desc')}</li>
                                                <li><b>{t('trading_center.risk_warning.p3_title')}</b> {t('trading_center.risk_warning.p3_desc')}</li>
                                                <li><b>{t('trading_center.risk_warning.p4_title')}</b> {t('trading_center.risk_warning.p4_desc')}</li>
                                            </ul>
                                        </div>
                                    </div>
                                </div>

                                {/* Main Content Layout */}
                                <div className="grid grid-cols-12 gap-6">
                                    <div className="col-span-12 lg:col-span-4">
                                        <Card title={
                                            <div style={{ color: colorText, fontSize: 16, fontWeight: 'bold' }}>
                                                {t('trading_center.account_info.title')}
                                            </div>
                                        } variant="borderless" style={{ background: colorBgContainer, height: '100%' }}>
                                            <div className="mb-6">
                                                <Descriptions
                                                    bordered
                                                    column={3}
                                                    size="small"
                                                    layout="horizontal"
                                                    styles={{
                                                        label: { background: colorFillQuaternary, color: colorTextSecondary, padding: '4px 8px' },
                                                        content: { background: colorBgContainer, color: colorText, padding: '4px 8px' }
                                                    }}
                                                >
                                                    <Descriptions.Item label={
                                                        <span>
                                                            {t('trading_center.account_info.starting_capital')}
                                                            <Tooltip title={t('trading_center.account_info.starting_capital_tip')}>
                                                                <QuestionCircleOutlined style={{ marginLeft: 4, cursor: 'help', fontSize: '12px' }} />
                                                            </Tooltip>
                                                        </span>
                                                    }>
                                                        ¥{account?.starting_capital ? Number(account.starting_capital).toFixed(2) : '0.00'}
                                                    </Descriptions.Item>
                                                    <Descriptions.Item label={
                                                        <span>
                                                            {t('trading_center.account_info.total_assets')}
                                                            <Tooltip title={t('trading_center.account_info.total_assets_tip')}>
                                                                <QuestionCircleOutlined style={{ marginLeft: 4, cursor: 'help', fontSize: '12px' }} />
                                                            </Tooltip>
                                                        </span>
                                                    }>
                                                        <span style={{ color: colorText, fontWeight: 'bold' }}>
                                                            ¥{account?.total_assets ? Number(account.total_assets).toFixed(2) : '0.00'}
                                                        </span>
                                                    </Descriptions.Item>
                                                    <Descriptions.Item label={
                                                        <span>
                                                            {t('trading_center.account_info.available_cash')}
                                                            <Tooltip title={t('trading_center.account_info.available_cash_tip')}>
                                                                <QuestionCircleOutlined style={{ marginLeft: 4, cursor: 'help', fontSize: '12px' }} />
                                                            </Tooltip>
                                                        </span>
                                                    }>
                                                        ¥{account?.cash_balance ? Number(account.cash_balance).toFixed(2) : '0.00'}
                                                    </Descriptions.Item>
                                                    <Descriptions.Item label={
                                                        <span>
                                                            {t('trading_center.account_info.frozen_cash')}
                                                            <Tooltip title={t('trading_center.account_info.frozen_cash_tip')}>
                                                                <QuestionCircleOutlined style={{ marginLeft: 4, cursor: 'help', fontSize: '12px' }} />
                                                            </Tooltip>
                                                        </span>
                                                    }>
                                                        ¥{account?.frozen_cash ? Number(account.frozen_cash).toFixed(2) : '0.00'}
                                                    </Descriptions.Item>
                                                    <Descriptions.Item label={
                                                        <span>
                                                            {t('trading_center.account_info.market_value')}
                                                            <Tooltip title={t('trading_center.account_info.market_value_tip')}>
                                                                <QuestionCircleOutlined style={{ marginLeft: 4, cursor: 'help', fontSize: '12px' }} />
                                                            </Tooltip>
                                                        </span>
                                                    }>
                                                        ¥{account?.market_value ? Number(account.market_value).toFixed(2) : '0.00'}
                                                    </Descriptions.Item>
                                                    <Descriptions.Item label={
                                                        <span>
                                                            {t('trading_center.account_info.floating_pnl')}
                                                            <Tooltip title={t('trading_center.account_info.floating_pnl_tip')}>
                                                                <QuestionCircleOutlined style={{ marginLeft: 4, cursor: 'help', fontSize: '12px' }} />
                                                            </Tooltip>
                                                        </span>
                                                    }>
                                                        <span style={{ color: (account?.floating_pnl ?? 0) >= 0 ? '#cf1322' : '#3f8600', fontWeight: 'bold' }}>
                                                            {(account?.floating_pnl ?? 0) > 0 ? '+' : ''}{account?.floating_pnl ? Number(account.floating_pnl).toFixed(2) : '0.00'}
                                                        </span>
                                                    </Descriptions.Item>
                                                    <Descriptions.Item label={
                                                        <span>
                                                            {t('trading_center.account_info.realized_pnl')}
                                                            <Tooltip title={t('trading_center.account_info.realized_pnl_tip')}>
                                                                <QuestionCircleOutlined style={{ marginLeft: 4, cursor: 'help', fontSize: '12px' }} />
                                                            </Tooltip>
                                                        </span>
                                                    }>
                                                        <span style={{ color: (account?.total_profit_loss ?? 0) >= 0 ? '#cf1322' : '#3f8600', fontWeight: 'bold' }}>
                                                            {(account?.total_profit_loss ?? 0) > 0 ? '+' : ''}{account?.total_profit_loss ? Number(account.total_profit_loss).toFixed(2) : '0.00'}
                                                        </span>
                                                    </Descriptions.Item>
                                                    <Descriptions.Item label={
                                                        <span>
                                                            {t('trading_center.account_info.profit_loss_pct')}
                                                            <Tooltip title={t('trading_center.account_info.profit_loss_pct_tip')}>
                                                                <QuestionCircleOutlined style={{ marginLeft: 4, cursor: 'help', fontSize: '12px' }} />
                                                            </Tooltip>
                                                        </span>
                                                    }>
                                                        <span style={{ color: (account?.profit_loss_pct ?? 0) >= 0 ? '#cf1322' : '#3f8600', fontWeight: 'bold' }}>
                                                            {(account?.profit_loss_pct ?? 0) > 0 ? '+' : ''}{account?.profit_loss_pct ? Number(account.profit_loss_pct).toFixed(2) : '0.00'}%
                                                        </span>
                                                    </Descriptions.Item>
                                                    <Descriptions.Item label={
                                                        <span>
                                                            {t('trading_center.account_info.total_trades')}
                                                            <Tooltip title={t('trading_center.account_info.total_trades_tip')}>
                                                                <QuestionCircleOutlined style={{ marginLeft: 4, cursor: 'help', fontSize: '12px' }} />
                                                            </Tooltip>
                                                        </span>
                                                    }>
                                                        {account?.total_trades || 0}
                                                    </Descriptions.Item>
                                                    <Descriptions.Item label={
                                                        <span>
                                                            {t('trading_center.account_info.win_rate')}
                                                            <Tooltip title={t('trading_center.account_info.win_rate_tip')}>
                                                                <QuestionCircleOutlined style={{ marginLeft: 4, cursor: 'help', fontSize: '12px' }} />
                                                            </Tooltip>
                                                        </span>
                                                    }>
                                                        {account?.win_rate ? Number(account.win_rate).toFixed(2) : '0.00'}%
                                                    </Descriptions.Item>
                                                </Descriptions>
                                            </div>

                                            <div className="mt-8 text-center text-xs text-gray-500" style={{ color: colorTextSecondary }}>
                                                {t('trading_center.account_info.updated_at')}: {account?.updated_at ? new Date(account.updated_at).toLocaleString() : '-'}
                                            </div>
                                        </Card>
                                    </div>

                                    {/* Right Side: Positions and Orders */}
                                    <div className="col-span-12 lg:col-span-8 flex flex-col gap-6">
                                        {/* Positions Card */}
                                        <Card title={
                                            <div style={{ color: colorText, fontSize: 16, fontWeight: 'bold' }}>
                                                {t('trading_center.positions.title')} ({positions.length})
                                            </div>
                                        } variant="borderless" style={{ background: colorBgContainer }} styles={{ body: { padding: 0 } }}>
                                            <Table
                                                columns={positionColumns}
                                                dataSource={positions}
                                                rowKey="id"
                                                pagination={{ pageSize: 20 }}
                                                className="bg-transparent"
                                            />
                                        </Card>

                                        {/* Orders Card */}
                                        <Card title={
                                            <div className="flex items-center space-x-4">
                                                <span style={{ color: colorText, fontSize: 16, fontWeight: 'bold' }}>
                                                    {t('trading_center.orders.title')} ({orders.length})
                                                </span>
                                                {filterStockCode && (
                                                    <Tag
                                                        closable
                                                        onClose={() => setFilterStockCode(null)}
                                                        color="blue"
                                                    >
                                                        {filterStockCode}
                                                    </Tag>
                                                )}
                                            </div>
                                        } variant="borderless" style={{ background: colorBgContainer, flex: 1 }} styles={{ body: { padding: 0 } }}>
                                            <Table
                                                columns={orderColumns}
                                                dataSource={filterStockCode ? orders.filter(o => o.stock_code === filterStockCode) : orders}
                                                rowKey="id"
                                                pagination={{ pageSize: 5 }}
                                                className="bg-transparent"
                                            />
                                        </Card>
                                    </div>
                                </div >
                            </>
                        ),
                    },
                    {
                        key: 'portfolio',
                        label: t('trading_center.tabs.portfolio'),
                        children: <PortfolioOverviewTab />,
                    },
                    {
                        key: 'performance',
                        label: t('trading_center.tabs.performance'),
                        children: <PerformanceTab />,
                    },
                ]}
            />

            {/* Place Order Modal */}
            < Modal
                title={t('trading_center.modals.place_order.title')}
                open={isOrderModalVisible}
                onCancel={() => setIsOrderModalVisible(false)}
                footer={null}
                width={480}
            >
                <Form
                    form={orderForm}
                    layout="horizontal"
                    labelCol={{ span: 6 }}
                    wrapperCol={{ span: 16 }}
                    onFinish={handlePlaceOrder}
                    onValuesChange={(changedValues) => {
                        if (changedValues.action === 'sell') {
                            orderForm.setFieldValue('stop_loss_pct', undefined);
                        }
                    }}
                    style={{ marginTop: 24 }}
                >
                    <Form.Item label={t('trading_center.modals.place_order.direction')} name="action" rules={[{ required: true }]}>
                        <Radio.Group buttonStyle="solid">
                            <Radio.Button value="buy" style={{ color: '#1890ff', borderColor: '#1890ff' }}>{t('trading_center.modals.place_order.buy')}</Radio.Button>
                            <Radio.Button value="sell" style={{ color: '#cf1322', borderColor: '#cf1322' }}>{t('trading_center.modals.place_order.sell')}</Radio.Button>
                        </Radio.Group>
                    </Form.Item>

                    <Form.Item label={t('trading_center.modals.place_order.symbol')} name="stock_code" rules={[{ required: true }]}>
                        <Input placeholder={t('trading_center.modals.place_order.symbol_placeholder')} />
                    </Form.Item>

                    <Form.Item
                        label={t('trading_center.modals.place_order.quantity')}
                        name="shares"
                        rules={[{ required: true, type: 'number', min: 100, message: '必须为至少100的整数' }]}
                    >
                        <InputNumber min={100} step={100} style={{ width: '100%' }} />
                    </Form.Item>

                    {orderAction === 'buy' && (
                        <Form.Item
                            label={t('trading_center.modals.place_order.stop_loss_pct')}
                            name="stop_loss_pct"
                            rules={[
                                {
                                    required: true,
                                    message: t('trading_center.modals.place_order.stop_loss_pct_required'),
                                },
                                {
                                    type: 'number',
                                    min: 0.01,
                                    max: 99.99,
                                    message: t('trading_center.modals.place_order.stop_loss_pct_invalid'),
                                },
                            ]}
                        >
                            <InputNumber
                                min={0.01}
                                max={99.99}
                                precision={2}
                                step={0.1}
                                placeholder={t('trading_center.modals.place_order.stop_loss_pct_placeholder')}
                                style={{ width: '100%' }}
                            />
                        </Form.Item>
                    )}

                    <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 32, gap: 12 }}>
                        <Button onClick={() => setIsOrderModalVisible(false)}>{t('trading_center.modals.place_order.cancel')}</Button>
                        <Button type="primary" htmlType="submit" loading={submittingOrder}>
                            {t('trading_center.modals.place_order.submit')}
                        </Button>
                    </div>
                </Form>
            </Modal >

            <Modal
                title={t('trading_center.risk_control.settings')}
                open={isRiskControlModalVisible}
                onCancel={() => setIsRiskControlModalVisible(false)}
                footer={null}
                width={520}
            >
                <Form
                    form={riskControlForm}
                    layout="vertical"
                    onFinish={handleSaveRiskControl}
                    initialValues={{
                        enabled: true,
                        max_single_position_pct: 20,
                        max_industry_position_pct: 35,
                        min_cash_pct: 10,
                        require_stop_loss: true,
                        stop_loss_warning_pct: 10,
                        rule_policies: {
                            require_stop_loss: 'block',
                            max_single_position_pct: 'block',
                            max_industry_position_pct: 'block',
                            min_cash_pct: 'block',
                            stop_loss_warning_pct: 'block',
                        },
                    }}
                >
                    <Form.Item
                        label={renderRiskControlLabel('trading_center.risk_control.enabled', 'trading_center.risk_control.enabled_tip')}
                        name="enabled"
                        valuePropName="checked"
                    >
                        <Switch />
                    </Form.Item>
                    {renderRiskControlRule(
                        'max_single_position_pct',
                        <InputNumber min={0} max={100} precision={2} addonAfter="%" style={{ width: '100%' }} />,
                        'trading_center.risk_control.max_single_position_pct',
                        'trading_center.risk_control.max_single_position_pct_tip'
                    )}
                    {renderRiskControlRule(
                        'max_industry_position_pct',
                        <InputNumber min={0} max={100} precision={2} addonAfter="%" style={{ width: '100%' }} />,
                        'trading_center.risk_control.max_industry_position_pct',
                        'trading_center.risk_control.max_industry_position_pct_tip'
                    )}
                    {renderRiskControlRule(
                        'min_cash_pct',
                        <InputNumber min={0} max={100} precision={2} addonAfter="%" style={{ width: '100%' }} />,
                        'trading_center.risk_control.min_cash_pct',
                        'trading_center.risk_control.min_cash_pct_tip'
                    )}
                    {renderRiskControlRule(
                        'require_stop_loss',
                        <Switch />,
                        'trading_center.risk_control.require_stop_loss',
                        'trading_center.risk_control.require_stop_loss_tip',
                        'checked'
                    )}
                    {renderRiskControlRule(
                        'stop_loss_warning_pct',
                        <InputNumber min={0} max={100} precision={2} addonAfter="%" style={{ width: '100%' }} />,
                        'trading_center.risk_control.stop_loss_warning_pct',
                        'trading_center.risk_control.stop_loss_warning_pct_tip'
                    )}
                    <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 12 }}>
                        <Button onClick={() => setIsRiskControlModalVisible(false)}>
                            {t('trading_center.modals.place_order.cancel')}
                        </Button>
                        <Button type="primary" htmlType="submit" loading={savingRiskControl}>
                            {t('trading_center.risk_control.save')}
                        </Button>
                    </div>
                </Form>
            </Modal>
        </Spin >
    );
};

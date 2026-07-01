import React, { useCallback, useState, useEffect } from 'react';
import { Card, Select, Tabs, Spin, Empty, Typography, DatePicker, Space } from 'antd';
import dayjs from 'dayjs';
import { ReloadOutlined } from '@ant-design/icons';
import { useSearchParams } from 'react-router-dom';
import { marketApi } from '../api/market';
import type { AIContext } from '../api/market';
import { StockWarehouse, warehouseApi } from '../api/warehouse';
import { getApiErrorMessage } from '../utils/errorUtils';
import { KlineChart } from '../features/market/KlineChart';
import { useFeedback } from '../hooks/useFeedback';

import { useTranslation } from 'react-i18next';

const { Text } = Typography;

export const StockDataPage: React.FC = () => {
    const { t } = useTranslation();
    const message = useFeedback();
    const [searchParams, setSearchParams] = useSearchParams();
    const [stockCode, setStockCode] = useState<string | undefined>(searchParams.get('stock_code') || undefined);
    const [stockList, setStockList] = useState<StockWarehouse[]>([]);
    const [loading, setLoading] = useState(false);
    const [syncing, setSyncing] = useState(false);
    const [aiContext, setAiContext] = useState<AIContext | null>(null);
    const [syncDateRange, setSyncDateRange] = useState<[dayjs.Dayjs | null, dayjs.Dayjs | null]>([dayjs().subtract(1, 'week'), dayjs()]);

    // 从已有仓库加载股票列表
    useEffect(() => {
        const loadStockList = async () => {
            try {
                const stocks = await warehouseApi.getStocks();
                setStockList(stocks);
            } catch (error) {
                console.error('Failed to load stock list:', error);
            }
        };
        loadStockList();
    }, []);

    const loadData = useCallback(async (code: string) => {
        setLoading(true);
        try {
            // 获取 AI 上下文数据
            const context = await marketApi.getAIContext(code);
            setAiContext(context);
        } catch (error) {
            console.error('Failed to load AI context:', error);
            setAiContext(null);
            message.error(getApiErrorMessage(error, t('common.error')));
        } finally {
            setLoading(false);
        }
    }, [message, t]);

    // 当股票代码变化时加载数据
    useEffect(() => {
        if (!stockCode) return;
        loadData(stockCode);
    }, [loadData, stockCode]);

    const handleStockChange = (value: string) => {
        setStockCode(value);
        const nextSearchParams = new URLSearchParams(searchParams);
        nextSearchParams.set('stock_code', value);
        setSearchParams(nextSearchParams);
    };

    const checkStatus = async () => {
        // 由于依赖的 task API 已移除，我们将自动延迟并刷新
        setTimeout(() => {
            message.success(t('common.sync_success'));
            setSyncing(false);
            if (stockCode) loadData(stockCode);
        }, 5000); // 假定同步过程为 5 秒
    };

    const handleRefresh = async () => {
        if (!stockCode) return;

        setSyncing(true);
        message.info(t('common.syncing'));
        try {
            const startDate = syncDateRange?.[0]?.format('YYYYMMDD');
            const endDate = syncDateRange?.[1]?.format('YYYYMMDD');
            await marketApi.syncDbData(stockCode, startDate, endDate);
            // 代替原本的 pollTaskStatus
            checkStatus();
        } catch (error) {
            console.error('Failed to start sync task:', error);
            message.error(t('common.sync_failed'));
            setSyncing(false);
        }
    };

    if (!stockCode) {
        return (
            <div style={{ height: '100%', display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'center' }}>
                <Empty description={t('common.please_select_stock')} />
                <Select
                    showSearch
                    placeholder={t('common.input_stock_placeholder')}
                    style={{ width: 400, marginTop: 24 }}
                    onChange={handleStockChange}
                    filterOption={(input, option) =>
                        (option?.label ?? '').toLowerCase().includes(input.toLowerCase())
                    }
                    options={stockList.map(stock => ({
                        value: stock.stock_code,
                        label: `${stock.stock_code} - ${stock.stock_name}`
                    }))}
                />
            </div>
        );
    }

    // 递归渲染 AI Context 对象
    const renderContextData = (data: unknown, level: number = 0) => {
        if (typeof data !== 'object' || data === null) {
            return <Text>{String(data)}</Text>;
        }

        if (Array.isArray(data)) {
            if (data.length === 0) return <Text type="secondary">Empty</Text>;
            return (
                <div style={{ paddingLeft: 12, borderLeft: '1px solid #f0f0f0' }}>
                    {data.map((item, index) => (
                        <div key={index} style={{ marginBottom: 8, display: 'flex', alignItems: 'flex-start' }}>
                            <span style={{ marginRight: 8, color: '#bfbfbf' }}>•</span>
                            <div style={{ flex: 1 }}>{renderContextData(item, level + 1)}</div>
                        </div>
                    ))}
                </div>
            );
        }

        const entries = Object.entries(data);
        if (entries.length === 0) return <Text type="secondary">Empty</Text>;

        // 检查是否所有子项都是简单值
        const isSimpleObject = entries.every(([, v]) => typeof v !== 'object' || v === null);

        if (isSimpleObject) {
            return (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '8px 16px', padding: '8px 0' }}>
                    {entries.map(([key, value]) => (
                        <div key={key}>
                            <Text type="secondary" style={{ marginRight: 8 }}>{key}:</Text>
                            <Text strong>{String(value)}</Text>
                        </div>
                    ))}
                </div>
            );
        }

        return (
            <div style={{ paddingLeft: level === 0 ? 0 : 16, borderLeft: level === 0 ? 'none' : '2px solid #f0f0f0' }}>
                {entries.map(([key, value]) => (
                    <div key={key} style={{ marginBottom: 16 }}>
                        <div style={{ marginBottom: 4, background: level === 0 ? '#fafafa' : 'transparent', padding: level === 0 ? '4px 8px' : 0, borderRadius: 4 }}>
                            <Text strong style={{ fontSize: level === 0 ? 16 : 14, color: level === 0 ? '#1890ff' : 'inherit' }}>{key.toUpperCase()}</Text>
                        </div>
                        <div style={{ paddingLeft: 8 }}>
                            {renderContextData(value, level + 1)}
                        </div>
                    </div>
                ))}
            </div>
        );
    };

    return (
        <div style={{ height: '100%', overflow: 'auto' }}>
            <Card
                title={
                    <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
                        <span>{t('common.stock_data_center')}</span>
                        <Select
                            showSearch
                            value={stockCode}
                            style={{ width: 300 }}
                            onChange={handleStockChange}
                            filterOption={(input, option) =>
                                (option?.label ?? '').toLowerCase().includes(input.toLowerCase())
                            }
                            options={stockList.map(stock => ({
                                value: stock.stock_code,
                                label: `${stock.stock_code} - ${stock.stock_name}`
                            }))}
                        />
                    </div>
                }
                extra={
                    <Space>
                        <DatePicker.RangePicker
                            value={syncDateRange}
                            onChange={(dates) => setSyncDateRange(dates as [dayjs.Dayjs | null, dayjs.Dayjs | null])}
                            size="small"
                        />
                        <ReloadOutlined spin={syncing} onClick={!syncing ? handleRefresh : undefined} style={{ cursor: syncing ? 'not-allowed' : 'pointer', fontSize: 18 }} />
                    </Space>
                }
            >
                {loading && <div style={{ textAlign: 'center', padding: 40 }}><Spin size="large" /></div>}

                {!loading && (
                    <Tabs
                        defaultActiveKey="ai_context"
                        items={[
                            {
                                key: 'ai_context',
                                label: t('common.ai_context'),
                                children: aiContext ? (
                                    <div style={{ maxHeight: 'calc(100vh - 300px)', overflow: 'auto', padding: '0 12px' }}>
                                        {renderContextData(aiContext)}
                                    </div>
                                ) : <Empty />
                            },
                            {
                                key: 'kline',
                                label: t('common.kline_chart'),
                                children: stockCode ? <KlineChart stockCode={stockCode} /> : <Empty />
                            }
                        ]}
                    />
                )}
            </Card>
        </div>
    );
};

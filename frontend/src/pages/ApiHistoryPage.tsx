import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Table, Card, Button, Tag, Space, Input, Select, Modal, Typography, App } from 'antd';
import { DeleteOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import { useTranslation } from 'react-i18next';
import { apiHistory, ApiHistoryRecord } from '../utils/apiHistory';
import dayjs from 'dayjs';

const { Text } = Typography;
const { TextArea } = Input;

const hasObjectContent = (value: unknown): value is Record<string, unknown> =>
    typeof value === 'object' && value !== null && Object.keys(value).length > 0;

export const ApiHistoryPage: React.FC = () => {
    const { t } = useTranslation();
    const { message } = App.useApp();
    const [records, setRecords] = useState<ApiHistoryRecord[]>(() => apiHistory.getRecords());
    const [searchKeyword, setSearchKeyword] = useState('');
    const [statusFilter, setStatusFilter] = useState<string>('all');

    // 加载记录 | Load records
    const loadRecords = useCallback(() => {
        setRecords(apiHistory.getRecords());
    }, []);

    // 应用筛选 | Apply filters
    const filteredRecords = useMemo(() => {
        let filtered = records;

        // 按状态筛选 | Filter by status
        if (statusFilter !== 'all') {
            filtered = filtered.filter(r => r.status === statusFilter);
        }

        // 按关键字筛选 | Filter by keyword
        if (searchKeyword) {
            const lowerKeyword = searchKeyword.toLowerCase();
            filtered = filtered.filter(r =>
                r.url.toLowerCase().includes(lowerKeyword) ||
                r.method.toLowerCase().includes(lowerKeyword)
            );
        }

        return filtered;
    }, [records, searchKeyword, statusFilter]);

    useEffect(() => {
        // 定期刷新以显示 WebSocket 更新 | Periodic refresh to show WebSocket updates
        const interval = setInterval(loadRecords, 2000);
        return () => clearInterval(interval);
    }, [loadRecords]);

    // 清空历史 | Clear history
    const handleClear = () => {
        Modal.confirm({
            title: t('api_history.clear_confirm'),
            okText: t('common.confirm'),
            cancelText: t('common.cancel'),
            onOk: () => {
                apiHistory.clearRecords();
                message.success(t('api_history.cleared'));
                loadRecords();
            }
        });
    };

    // 状态标签渲染 | Status tag render
    const renderStatus = (status: string) => {
        const statusConfig = {
            pending: { color: 'warning', text: t('api_history.pending') },
            completed: { color: 'success', text: t('api_history.completed') },
            failed: { color: 'error', text: t('api_history.failed') },
        };
        const config = statusConfig[status as keyof typeof statusConfig] || { color: 'default', text: status };
        return <Tag color={config.color}>{config.text}</Tag>;
    };

    // 表格列定义 | Table columns definition
    const columns = [
        {
            title: t('api_history.time'),
            dataIndex: 'requestTime',
            key: 'requestTime',
            width: 180,
            render: (time: number) => dayjs(time).format('YYYY-MM-DD HH:mm:ss'),
        },
        {
            title: t('api_history.method'),
            dataIndex: 'method',
            key: 'method',
            width: 100,
            render: (method: string) => {
                const colors: Record<string, string> = {
                    GET: 'blue',
                    POST: 'green',
                    PUT: 'orange',
                    DELETE: 'red',
                };
                return <Tag color={colors[method] || 'default'}>{method}</Tag>;
            },
        },
        {
            title: t('api_history.url'),
            dataIndex: 'url',
            key: 'url',
            ellipsis: true,
            render: (url: string) => <Text code>{url}</Text>,
        },
        {
            title: t('api_history.status'),
            dataIndex: 'status',
            key: 'status',
            width: 120,
            render: renderStatus,
        },
        {
            title: t('api_history.duration'),
            dataIndex: 'duration',
            key: 'duration',
            width: 100,
            render: (duration?: number) => {
                if (duration === undefined || duration === null) return '-';
                return `${duration}ms`;
            },
        },
    ];

    // 展开行内容 | Expanded row content
    const expandedRowRender = (record: ApiHistoryRecord) => {
        return (
            <div style={{ padding: '16px', backgroundColor: 'var(--app-bg-container)' }}>
                <Space direction="vertical" style={{ width: '100%' }} size="large">
                    {/* 请求信息 | Request information */}
                    <div>
                        <Text strong style={{ fontSize: 16, marginBottom: 8, display: 'block' }}>
                            {t('api_history.request')}
                        </Text>
                        <div style={{ marginLeft: 16 }}>
                            <Text type="secondary">URL: </Text>
                            <Text code>{record.url}</Text>
                            <br />
                            <Text type="secondary">Method: </Text>
                            <Tag color="blue">{record.method}</Tag>
                            <br />
                            {record.task_id && (
                                <>
                                    <Text type="secondary">Task ID: </Text>
                                    <Text code>{record.task_id}</Text>
                                    <br />
                                </>
                            )}
                            {hasObjectContent(record.params) && (
                                <>
                                    <Text type="secondary" style={{ marginTop: 8, display: 'block' }}>
                                        {t('api_history.params')}:
                                    </Text>
                                    <TextArea
                                        value={JSON.stringify(record.params, null, 2)}
                                        readOnly
                                        autoSize={{ minRows: 3, maxRows: 10 }}
                                        style={{ marginTop: 4, fontFamily: 'monospace', fontSize: 12 }}
                                    />
                                </>
                            )}
                        </div>
                    </div>

                    {/* 响应信息 | Response information */}
                    <div>
                        <Text strong style={{ fontSize: 16, marginBottom: 8, display: 'block' }}>
                            {t('api_history.response')}
                        </Text>
                        <div style={{ marginLeft: 16 }}>
                            <Text type="secondary">Status: </Text>
                            {renderStatus(record.status)}
                            <br />
                            {record.responseTime && (
                                <>
                                    <Text type="secondary">Response Time: </Text>
                                    <Text>{dayjs(record.responseTime).format('YYYY-MM-DD HH:mm:ss')}</Text>
                                    <br />
                                </>
                            )}
                            {record.duration !== undefined && (
                                <>
                                    <Text type="secondary">Duration: </Text>
                                    <Text>{record.duration}ms</Text>
                                    <br />
                                </>
                            )}
                            {record.error && (
                                <>
                                    <Text type="danger" style={{ marginTop: 8, display: 'block' }}>
                                        Error:
                                    </Text>
                                    <Text type="danger" code style={{ display: 'block', marginTop: 4 }}>
                                        {record.error}
                                    </Text>
                                </>
                            )}
                            {record.response !== undefined && record.response !== null && (
                                <>
                                    <Text type="secondary" style={{ marginTop: 8, display: 'block' }}>
                                        {t('api_history.response_data')}:
                                    </Text>
                                    <TextArea
                                        value={JSON.stringify(record.response, null, 2)}
                                        readOnly
                                        autoSize={{ minRows: 5, maxRows: 20 }}
                                        style={{ marginTop: 4, fontFamily: 'monospace', fontSize: 12 }}
                                    />
                                </>
                            )}
                            {record.status === 'pending' && !record.response && (
                                <Text type="warning" style={{ marginTop: 8, display: 'block' }}>
                                    {t('api_history.waiting_response')}
                                </Text>
                            )}
                        </div>
                    </div>
                </Space>
            </div>
        );
    };

    return (
        <Card
            title={t('api_history.title')}
            extra={
                <Space>
                    <Input
                        placeholder={t('api_history.search_placeholder')}
                        prefix={<SearchOutlined />}
                        value={searchKeyword}
                        onChange={(e) => setSearchKeyword(e.target.value)}
                        style={{ width: 200 }}
                        allowClear
                    />
                    <Select
                        value={statusFilter}
                        onChange={setStatusFilter}
                        style={{ width: 120 }}
                        options={[
                            { label: t('api_history.all_status'), value: 'all' },
                            { label: t('api_history.pending'), value: 'pending' },
                            { label: t('api_history.completed'), value: 'completed' },
                            { label: t('api_history.failed'), value: 'failed' },
                        ]}
                    />
                    <Button icon={<ReloadOutlined />} onClick={loadRecords}>
                        {t('api_history.refresh')}
                    </Button>
                    <Button danger icon={<DeleteOutlined />} onClick={handleClear}>
                        {t('api_history.clear')}
                    </Button>
                </Space>
            }
        >
            <Table
                columns={columns}
                dataSource={filteredRecords}
                rowKey="id"
                expandable={{
                    expandedRowRender,
                    rowExpandable: () => true,
                }}
                pagination={{
                    pageSize: 20,
                    showSizeChanger: true,
                    showTotal: (total) => t('api_history.total_records', { count: total }),
                }}
                locale={{
                    emptyText: t('api_history.empty'),
                }}
            />
        </Card>
    );
};

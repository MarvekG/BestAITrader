import React, { useCallback, useState, useEffect } from 'react';
import { Table, Tabs, Input, Button, Card, Space, Tag, Typography, DatePicker, App, Modal, Tooltip, Select, Radio, Checkbox, AutoComplete } from 'antd';
import { useTranslation } from 'react-i18next';
import { SyncOutlined, SearchOutlined, DatabaseOutlined, LineChartOutlined, DollarOutlined, ReadOutlined, TransactionOutlined, FireOutlined, FundViewOutlined, FundOutlined, DeleteOutlined, ExclamationCircleOutlined, UserOutlined, SettingOutlined, QuestionCircleOutlined } from '@ant-design/icons';
import { marketApi } from '../api/market';
import { TaskCompletedMessage, WebSocketMessage } from '../services/websocket';
import { useWebSocketSubscription } from '../hooks/useWebSocketSubscription';
import { getApiErrorMessage } from '../utils/errorUtils';
import dayjs from 'dayjs';
import ReactMarkdown from 'react-markdown';

const { Text } = Typography;

const formatNumber = (value: number | null | undefined, precision = 2) => {
    if (value == null) return '-';
    return Number(value).toLocaleString(undefined, {
        maximumFractionDigits: precision,
        minimumFractionDigits: 0,
    });
};

export const DataManagerPage: React.FC = () => {
    const { t } = useTranslation();
    const { message, notification } = App.useApp();
    const [loading, setLoading] = useState(false);
    const [syncing, setSyncing] = useState(false);
    const [dragonTigerSyncing, setDragonTigerSyncing] = useState(false);
    const [realtimeSyncing, setRealtimeSyncing] = useState(false);
    const [industrySyncing, setIndustrySyncing] = useState(false);
    const [interactiveQASyncing, setInteractiveQASyncing] = useState(false);
    const [moneyFlowSyncing, setMoneyFlowSyncing] = useState(false);
    const [shareholderSyncing, setShareholderSyncing] = useState(false);
    const [pledgeSyncing, setPledgeSyncing] = useState(false);
    const [pledgeSummarySyncing, setPledgeSummarySyncing] = useState(false);
    const [insiderSyncing, setInsiderSyncing] = useState(false);
    const [lockupSyncing, setLockupSyncing] = useState(false);
    const [marginSyncing, setMarginSyncing] = useState(false);
    const [limitUpSyncing, setLimitUpSyncing] = useState(false);
    const [limitDownSyncing, setLimitDownSyncing] = useState(false);
    const [zhabanSyncing, setZhabanSyncing] = useState(false);
    const [northboundSyncing, setNorthboundSyncing] = useState(false);
    const [sectorMoneyFlowSyncing, setSectorMoneyFlowSyncing] = useState(false);
    const [topHoldersSyncing, setTopHoldersSyncing] = useState(false);
    const [baseInfoSyncing, setBaseInfoSyncing] = useState(false);
    const baseInfoSyncTaskIdRef = React.useRef<string | null>(null);
    const [resumeSync, setResumeSync] = useState(false);

    // Bulk Sync State
    const [bulkSyncing, setBulkSyncing] = useState(false);
    const [isBulkSyncModalVisible, setIsBulkSyncModalVisible] = useState(false);
    const [selectedBulkTables, setSelectedBulkTables] = useState<string[]>([]);
    const [bulkSyncDateRange, setBulkSyncDateRange] = useState<[dayjs.Dayjs | null, dayjs.Dayjs | null]>([dayjs().subtract(1, 'week'), dayjs()]);
    const [bulkSyncStockCodes, setBulkSyncStockCodes] = useState<string>('');
    // 股票范围选项: warehouse（仓库）| all（全量 stock_basic）
    // Stock scope: warehouse (default) | all (all stocks from stock_basic)
    const [bulkSyncStockScope, setBulkSyncStockScope] = useState<string>('warehouse');

    const [limitUpDate, setLimitUpDate] = useState<dayjs.Dayjs | null>(null);
    const [limitDownDate, setLimitDownDate] = useState<dayjs.Dayjs | null>(null);
    const [zhabanDate, setZhabanDate] = useState<dayjs.Dayjs | null>(null);

    const [dragonTigerDateRange, setDragonTigerDateRange] = useState<[dayjs.Dayjs | null, dayjs.Dayjs | null]>([dayjs().subtract(1, 'day'), dayjs().subtract(1, 'day')]);
    const [syncDateRange, setSyncDateRange] = useState<[dayjs.Dayjs | null, dayjs.Dayjs | null]>([dayjs().subtract(1, 'week'), dayjs()]);
    const [basicSyncing, setBasicSyncing] = useState(false);
    const basicSyncTaskIdRef = React.useRef<string | null>(null);
    const [indicatorsSyncing, setIndicatorsSyncing] = useState(false);
    const [valuationSyncing, setValuationSyncing] = useState(false);
    const [blockTradeSyncing, setBlockTradeSyncing] = useState(false);
    const [blockTradeDateRange, setBlockTradeDateRange] = useState<[dayjs.Dayjs | null, dayjs.Dayjs | null]>([dayjs().subtract(3, 'day'), dayjs()]);

    // Data Source & Daily Sync State
    const [dailySyncing, setDailySyncing] = useState(false);
    const [dataSourceList, setDataSourceList] = useState<any>(null);
    const [currentDataSource, setCurrentDataSource] = useState<string>('');
    const [isDataSourceModalVisible, setIsDataSourceModalVisible] = useState(false);
    const [isDailySyncModalVisible, setIsDailySyncModalVisible] = useState(false);
    const [dailySyncDateRange, setDailySyncDateRange] = useState<[dayjs.Dayjs | null, dayjs.Dayjs | null]>([dayjs().subtract(3, 'month'), dayjs()]);
    const [dailySyncAdjust, setDailySyncAdjust] = useState<string>('None');

    // Clear Data State
    const [isClearDataModalVisible, setIsClearDataModalVisible] = useState(false);
    const [clearingTable, setClearingTable] = useState(false);
    const [dbTables, setDbTables] = useState<string[]>([]);
    const [selectedTableToClear, setSelectedTableToClear] = useState<string>('all');
    const [clearConfirmationText, setClearConfirmationText] = useState('');

    // Index Daily Sync State
    const [indexDailySyncing, setIndexDailySyncing] = useState(false);
    const [isIndexDailySyncModalVisible, setIsIndexDailySyncModalVisible] = useState(false);
    const [indexCode, setIndexCode] = useState<string>('');
    const [indexDailySyncDateRange, setIndexDailySyncDateRange] = useState<[dayjs.Dayjs | null, dayjs.Dayjs | null]>([dayjs().subtract(1, 'year'), dayjs()]);

    const [stockCode, setStockCode] = useState<string>('');
    const [searchOptions, setSearchOptions] = useState<{ value: string, label: string }[]>([]);
    const [activeTab, setActiveTab] = useState<string>('stocks');
    const [data, setData] = useState<{ total: number; items: any[] }>({ total: 0, items: [] });
    const [pagination, setPagination] = useState({ current: 1, pageSize: 20 });
    const { current: paginationCurrent, pageSize: paginationPageSize } = pagination;
    const [newsDetail, setNewsDetail] = useState<any>(null);
    const [isNewsModalVisible, setIsNewsModalVisible] = useState(false);
    const [modalTitle, setModalTitle] = useState<string>('');
    // Fetch data sources on mount
    useEffect(() => {
        fetchDataSources();
    }, []);

    const handleSearchStock = async (value: string) => {
        if (!value) {
            setSearchOptions([]);
            return;
        }
        try {
            const res = await marketApi.getDbStocks({ query: value, limit: 10 });
            setSearchOptions(res.items.map((item: any) => ({
                value: item['stock_basic.stock_code'],
                label: `${item['stock_basic.stock_code']} - ${item['stock_basic.name']}`
            })));
        } catch (error) {
            console.error('Failed to search stocks:', error);
        }
    };

    const fetchDataSources = async () => {
        try {
            const res = await marketApi.getDataSources();
            setDataSourceList(res);
            setCurrentDataSource(res.default_source);
        } catch (error) {
            console.error("Failed to fetch data sources", error);
        }
    };

    const handleSwitchDataSource = async (source: string) => {
        try {
            const res = await marketApi.setDefaultDataSource(source);
            message.success(res.message);
            setCurrentDataSource(res.default_source);
            setIsDataSourceModalVisible(false);
            // Refresh data from new source
            fetchData(activeTab, stockCode, pagination.current, pagination.pageSize);
        } catch (error) {
            message.error(getApiErrorMessage(error, 'Switch failed'));
        }
    };

    const fetchDbTables = async () => {
        try {
            const tables = await marketApi.getDbTables();
            setDbTables(tables);
        } catch (error) {
            console.error("Failed to fetch db tables", error);
        }
    };

    const handleDailySync = async () => {
        if (!stockCode) {
            message.warning("Please filter by a stock code first");
            return;
        }
        setDailySyncing(true);
        try {
            const start = dailySyncDateRange[0]?.format('YYYYMMDD');
            const end = dailySyncDateRange[1]?.format('YYYYMMDD');

            if (!start || !end) {
                message.error("Please select a valid date range");
                return;
            }

            const res = await marketApi.syncDailyDbData(stockCode, start, end, dailySyncAdjust);
            message.success(res.message);
            setIsDailySyncModalVisible(false);
        } catch (error) {
            message.error(getApiErrorMessage(error, 'Daily sync failed'));
        } finally {
            setDailySyncing(false);
        }
    };

    const handleIndexDailySync = async () => {
        if (!indexCode) {
            message.warning("Please enter an index code");
            return;
        }
        setIndexDailySyncing(true);
        try {
            const start = indexDailySyncDateRange[0]?.format('YYYYMMDD');
            const end = indexDailySyncDateRange[1]?.format('YYYYMMDD');

            if (!start || !end) {
                message.error("Please select a valid date range");
                return;
            }

            // Calls new API
            const res = await marketApi.syncIndexDaily(indexCode, start, end);
            message.success(res.message);
            setIsIndexDailySyncModalVisible(false);
        } catch (error) {
            message.error(getApiErrorMessage(error, 'Index daily sync failed'));
        } finally {
            setIndexDailySyncing(false);
        }
    };

    const handleClearData = async () => {
        if (clearConfirmationText.trim().toLowerCase() !== 'confirm' && clearConfirmationText.trim() !== '确认') {
            message.error(t('market.data_manager.confirm_error_msg'));
            return;
        }

        const executeClear = async () => {
            setClearingTable(true);
            try {
                const res = await marketApi.clearDbTable(selectedTableToClear, clearConfirmationText);
                message.success(res.message);
                setIsClearDataModalVisible(false);
                setClearConfirmationText('');
                // Refresh current view
                fetchData(activeTab, stockCode, pagination.current, pagination.pageSize);
            } catch (error) {
                message.error(getApiErrorMessage(error, 'Failed to clear table'));
            } finally {
                setClearingTable(false);
            }
        };

        if (selectedTableToClear === 'all') {
            Modal.confirm({
                title: t('market.data_manager.critical_warning'),
                icon: <ExclamationCircleOutlined style={{ color: 'red' }} />,
                content: t('market.data_manager.clear_all_confirmation_content'),
                okText: t('market.data_manager.delete_everything'),
                okType: 'danger',
                cancelText: t('common.cancel'),
                onOk: executeClear,
            });
        } else {
            executeClear();
        }
    };

    const openClearDataModal = async () => {
        // Fetch latest tables
        try {
            const tables = await marketApi.getDbTables();
            setDbTables(tables);
        } catch (e) {
            console.error(e);
        }

        // Auto-select table based on activeTab
        let defaultTable = 'all';
        const tabToTableMap: Record<string, string> = {
            'stocks': 'stock_basic',
            'kline': 'kline_data',
            'realtime': 'stock_realtime_market', // Based on backend
            'valuation': 'stock_valuation_history',
            'industry': 'industry_data', // Hypothesized
            'northbound': 'northbound_data',
            'dragontiger': 'dragon_tiger_data',
            'stock_interactive_qa': 'stock_interactive_qa',
            'stock_limit_up_pool': 'stock_limit_up_pool',
            'stock_limit_down_pool': 'stock_limit_down_pool',
            'stock_zhaban_pool': 'stock_zhaban_pool',
            'stock_money_flow': 'stock_money_flow',
            'stock_shareholder_count': 'stock_shareholder_count',
            'stock_pledge_risk': 'stock_pledge_risk',
            'stock_pledge_summary': 'stock_pledge_summary',
            'stock_insider_trading': 'stock_insider_trading',
            'stock_lockup_release': 'stock_lockup_release',
            'stock_margin_data': 'stock_margin_data',
            'index_daily': 'index_daily',
            'stock_indicators': 'stock_indicators',
            'stock_block_trade': 'stock_block_trade',
            'sector_money_flow': 'sector_money_flow',
            'stock_top_holders': 'stock_top_holders',
        };

        if (tabToTableMap[activeTab]) {
            defaultTable = tabToTableMap[activeTab];
        } else {
            // Heuristic fallback for display-only columns.
            // but hardcoded map is safer for now.
        }

        setSelectedTableToClear(defaultTable);
        setClearConfirmationText('');
        setIsClearDataModalVisible(true);
    };

    const fetchData = useCallback(async (tab: string, code: string, page: number, size: number) => {
        setLoading(true);
        // Clear previous data to avoid Antd Table pagination mismatch warnings during loading
        setData(prev => ({ ...prev, items: [] }));
        try {
            const skip = (page - 1) * size;
            let res;
            if (tab === 'stocks') {
                res = await marketApi.getDbStocks({ stock_code: code, skip, limit: size });

            } else if (tab === 'industry') {
                res = await marketApi.getIndustryMarket({
                    skip,
                    limit: size,
                    sort_by: 'change_percent',
                    order: 'desc'
                });
            } else if (tab === 'stock_interactive_qa') {
                res = await marketApi.getDbData(tab, { stock_code: code, skip, limit: size, sort_by: 'answer_time', order: 'desc' });
            } else {
                const actualTab = tab;

                const extraParams: any = {};
                if (tab === 'stock_limit_up_pool' && limitUpDate) {
                    extraParams.update_date = limitUpDate.format('YYYY-MM-DD');
                } else if (tab === 'stock_limit_down_pool' && limitDownDate) {
                    extraParams.update_date = limitDownDate.format('YYYY-MM-DD');
                } else if (tab === 'stock_zhaban_pool' && zhabanDate) {
                    extraParams.update_date = zhabanDate.format('YYYY-MM-DD');
                } else if (tab === 'stock_shareholder_count') {
                    extraParams.sort_by = 'end_date';
                    extraParams.order = 'desc';
                } else if (tab === 'stock_top_holders') {
                    extraParams.sort_by = 'report_date';
                    extraParams.order = 'desc';
                }
                res = await marketApi.getDbData(actualTab, { stock_code: code, skip, limit: size, ...extraParams });
            }
            setData(res);
        } catch (error) {
            console.error('Fetch error:', error);
            message.error(`Failed to fetch ${tab} data`);
        } finally {
            setLoading(false);
        }
    }, [limitDownDate, limitUpDate, message, zhabanDate]);

    useEffect(() => {
        fetchData(activeTab, stockCode, paginationCurrent, paginationPageSize);
    }, [activeTab, fetchData, paginationCurrent, paginationPageSize, stockCode]);

    useWebSocketSubscription('task_completed', (msg: WebSocketMessage) => {
            const data = (msg as TaskCompletedMessage).data;
            if (!data) {
                return;
            }
            if (data.status === 'completed' || data.status === 'success') {
                // 如果是股票基础信息或全量基础信息同步任务，释放 loading 状态
                if (basicSyncTaskIdRef.current === data.task_id) {
                    setBasicSyncing(false);
                    basicSyncTaskIdRef.current = null;
                }
                if (baseInfoSyncTaskIdRef.current === data.task_id) {
                    setBaseInfoSyncing(false);
                    baseInfoSyncTaskIdRef.current = null;
                }
                // Refresh data after task completion
                fetchData(activeTab, stockCode, paginationCurrent, paginationPageSize);
            } else if (data.status === 'failed' || data.status === 'error') {
                // 如果是股票基础信息或全量基础信息同步任务，释放 loading 状态
                if (basicSyncTaskIdRef.current === data.task_id) {
                    setBasicSyncing(false);
                    basicSyncTaskIdRef.current = null;
                }
                if (baseInfoSyncTaskIdRef.current === data.task_id) {
                    setBaseInfoSyncing(false);
                    baseInfoSyncTaskIdRef.current = null;
                }

                // Handle Bulk Sync task updates
                if (data.task_type === 'bulk_data_sync') {
                    setBulkSyncing(false);
                }
            }
    });

    const handleSearch = () => {
        setPagination({ ...pagination, current: 1 });
        fetchData(activeTab, stockCode, 1, pagination.pageSize);
    };

    const handleSync = async (code?: string) => {
        const targetCode = code || stockCode;
        if (!targetCode) {
            message.warning(t('common.please_filter_by_stock_code_first'));
            return;
        }
        setSyncing(true);
        try {
            const startDate = syncDateRange?.[0]?.format('YYYYMMDD');
            const endDate = syncDateRange?.[1]?.format('YYYYMMDD');
            const res = await marketApi.syncDbData(targetCode, startDate, endDate);
            // Show tip message
            message.success(res.message);
        } catch (error) {
            message.error(getApiErrorMessage(error, 'Sync failed'));
        } finally {
            setSyncing(false);
        }
    };

    const handleDragonTigerSync = async () => {
        setDragonTigerSyncing(true);
        try {
            const startDate = dragonTigerDateRange[0]?.format('YYYYMMDD');
            const endDate = dragonTigerDateRange[1]?.format('YYYYMMDD');

            if (!startDate) {
                message.error("Please select a date range");
                return;
            }

            const res = await marketApi.syncDragonTiger(startDate, endDate);
            message.success(res.message);
        } catch (error) {
            message.error(getApiErrorMessage(error, 'Dragon Tiger sync failed'));
        } finally {
            setDragonTigerSyncing(false);
        }
    };

    const handleIndustrySync = async () => {
        setIndustrySyncing(true);
        try {
            const res = await marketApi.syncIndustryMarket();
            message.success(res.message);
            // WebSocket notification will handle completion
        } catch (error) {
            message.error(getApiErrorMessage(error, 'Industry sync failed'));
        } finally {
            setIndustrySyncing(false);
        }
    };

    const handleSectorMoneyFlowSync = async () => {
        if (!stockCode) {
            message.warning(t('sector_money_flow.stock_code_required'));
            return;
        }

        setSectorMoneyFlowSyncing(true);
        try {
            const res = await marketApi.syncSectorMoneyFlow(stockCode);
            message.success(res.message);
            // WebSocket notification will handle completion
        } catch (error) {
            message.error(getApiErrorMessage(error, 'Sector money flow sync failed'));
        } finally {
            setSectorMoneyFlowSyncing(false);
        }
    };

    const handleNorthboundSync = async () => {
        setNorthboundSyncing(true);
        try {
            const res = await marketApi.syncNorthboundData(stockCode || undefined);
            message.success(res.message);
        } catch (error) {
            message.error(getApiErrorMessage(error, 'Northbound sync failed'));
        } finally {
            setNorthboundSyncing(false);
        }
    };

    const handleMoneyFlowSync = async () => {
        if (!stockCode) {
            message.warning(t('common.please_select_stock'));
            return;
        }
        setMoneyFlowSyncing(true);
        try {
            const res = await marketApi.syncGranularData(stockCode, 'money_flow');
            message.success(res.message);
        } catch (error) {
            message.error(getApiErrorMessage(error, 'Money Flow sync failed'));
        } finally {
            setMoneyFlowSyncing(false);
        }
    };

    const handleGranularSync = async (_dataType: string, setLoadingState: (v: boolean) => void, apiType: string) => {
        if (!stockCode && apiType !== 'block_trade') {
            message.warning(t('common.please_select_stock'));
            return;
        }
        setLoadingState(true);
        try {
            let startDate, endDate;
            if (apiType === 'block_trade') {
                startDate = blockTradeDateRange[0]?.format('YYYYMMDD');
                endDate = blockTradeDateRange[1]?.format('YYYYMMDD');
            }
            const res = await marketApi.syncGranularData(stockCode || '', apiType, startDate, endDate);
            message.success(res.message);
        } catch (error) {
            message.error(getApiErrorMessage(error, 'Sync failed'));
        } finally {
            setLoadingState(false);
        }
    };

    const handleLimitUpSync = async () => {
        setLimitUpSyncing(true);
        try {
            const dateStr = limitUpDate?.format('YYYY-MM-DD');
            const res = await marketApi.syncLimitUpPool(dateStr);
            message.success(res.message);
        } catch (error) {
            message.error(getApiErrorMessage(error, 'Limit up sync failed'));
        } finally {
            setLimitUpSyncing(false);
        }
    };

    const handleLimitDownSync = async () => {
        setLimitDownSyncing(true);
        try {
            const dateStr = limitDownDate?.format('YYYY-MM-DD');
            const res = await marketApi.syncLimitDownPool(dateStr);
            message.success(res.message);
        } catch (error) {
            message.error(getApiErrorMessage(error, 'Limit down sync failed'));
        } finally {
            setLimitDownSyncing(false);
        }
    };

    const handleZhabanSync = async () => {
        setZhabanSyncing(true);
        try {
            const dateStr = zhabanDate?.format('YYYY-MM-DD');
            const res = await marketApi.syncZhabanPool(dateStr);
            message.success(res.message);
        } catch (error) {
            message.error(getApiErrorMessage(error, 'Zhaban sync failed'));
        } finally {
            setZhabanSyncing(false);
        }
    };

    const handlePledgeSummarySync = async () => {
        setPledgeSummarySyncing(true);
        try {
            const res = await marketApi.syncPledgeSummary(stockCode);
            message.success(res.message);
        } catch (error) {
            message.error(getApiErrorMessage(error, 'Pledge summary sync failed'));
        } finally {
            setPledgeSummarySyncing(false);
        }
    };

    const handleStockBasicSync = async () => {
        setBasicSyncing(true);
        try {
            // If stockCode is active (filtered), pass it to sync only that stock
            const codeToSync = stockCode || undefined;
            const res = await marketApi.syncStockBasic(codeToSync, resumeSync);
            // 记录任务 ID，由 WebSocket 通知来释放 loading 状态
            if (res.task_id) {
                basicSyncTaskIdRef.current = res.task_id;
            } else {
                // 如果没有任务 ID (单股同步等情况), 立即释放
                setBasicSyncing(false);
            }
            message.success(res.message);
        } catch (error) {
            message.error(getApiErrorMessage(error, 'Stock basic sync failed'));
            setBasicSyncing(false);
            basicSyncTaskIdRef.current = null;
        }
    };

    const handleCalculateIndicators = async () => {
        setIndicatorsSyncing(true);
        try {
            const res = await marketApi.syncIndicators(stockCode || undefined);
            message.success(res.message);
        } catch (error) {
            message.error(getApiErrorMessage(error, 'Calculation failed'));
        } finally {
            setIndicatorsSyncing(false);
        }
    };

    const handleInteractiveQASync = async () => {
        if (!stockCode) {
            message.warning(t('common.please_select_stock'));
            return;
        }
        setInteractiveQASyncing(true);
        try {
            const res = await marketApi.syncInteractiveQA(stockCode);
            message.success(res.message);
        } catch (error) {
            message.error(getApiErrorMessage(error, 'Interactive QA sync failed'));
        } finally {
            setInteractiveQASyncing(false);
        }
    };

    const handleValuationSync = async () => {
        setValuationSyncing(true);
        try {
            const res = await marketApi.syncStockValuation(stockCode || undefined);
            message.success(res.message);
        } catch (error) {
            message.error(getApiErrorMessage(error, 'Valuation sync failed'));
        } finally {
            setValuationSyncing(false);
        }
    };

    const handleRealtimeSync = async () => {
        if (!stockCode) {
            message.warning(t('common.please_select_stock'));
            return;
        }
        setRealtimeSyncing(true);
        try {
            const res = await marketApi.syncRealtimeMarket(stockCode);
            message.success(res.message);
        } catch (error) {
            message.error(getApiErrorMessage(error, 'Realtime sync failed'));
        } finally {
            setRealtimeSyncing(false);
        }
    };

    const handleBaseInfoSync = async (scope: 'all' | 'warehouse' | 'core' = 'all') => {
        let title = t('common.confirm_sync_base_info');
        let content = t('common.sync_base_info_confirm_content');
        let successKey: 'common.sync_base_info' | 'common.sync_warehouse_base_info' | 'common.sync_core_base_info' = 'common.sync_base_info';

        if (scope === 'warehouse') {
            title = t('common.confirm_sync_warehouse_base_info');
            content = t('common.sync_warehouse_base_info_confirm_content');
            successKey = 'common.sync_warehouse_base_info';
        } else if (scope === 'core') {
            title = t('common.confirm_sync_core_base_info');
            content = t('common.sync_core_base_info_confirm_content');
            successKey = 'common.sync_core_base_info';
        }

        Modal.confirm({
            title: title,
            icon: <ExclamationCircleOutlined />,
            content: content,
            okText: t('common.confirm'),
            cancelText: t('common.cancel'),
            onOk: async () => {
                setBaseInfoSyncing(true);
                try {
                    const codeToSync = stockCode || undefined;
                    const res = await marketApi.syncBaseInfo(codeToSync, resumeSync, scope);
                    if (res.task_id) {
                        baseInfoSyncTaskIdRef.current = res.task_id;
                        message.success(`${t(successKey)} ${t('common.task_submitted')}: ${res.task_id}`);
                    } else {
                        message.success(res.message);
                    }
                } catch (error) {
                    message.error(getApiErrorMessage(error, t('common.sync_failed')));
                    setBaseInfoSyncing(false);
                    baseInfoSyncTaskIdRef.current = null;
                }
            }
        });
    };

    const handleBulkSyncSubmit = async () => {
        if (selectedBulkTables.length === 0) {
            message.warning(t('market.data_manager.select_tables_warning'));
            return;
        }
        setBulkSyncing(true);
        try {
            const startDate = bulkSyncDateRange[0]?.format('YYYY-MM-DD') || '';
            const endDate = bulkSyncDateRange[1]?.format('YYYY-MM-DD') || '';
            const res = await marketApi.syncBulkData(selectedBulkTables, startDate, endDate, bulkSyncStockCodes, bulkSyncStockScope);
            message.success(`${t('market.data_manager.bulk_sync')} ${t('common.task_submitted')}: ${res.task_id}`);
            setIsBulkSyncModalVisible(false);
            setSelectedBulkTables([]);
        } catch (error) {
            message.error(getApiErrorMessage(error, t('common.sync_failed')));
        } finally {
            setBulkSyncing(false);
        }
    };

    const handleTopHoldersSync = async () => {
        if (!stockCode) {
            message.warning(t('common.please_select_stock'));
            return;
        }
        setTopHoldersSyncing(true);
        try {
            const res = await marketApi.syncTopHolders(stockCode);
            message.success(res.message);
        } catch (error) {
            message.error(getApiErrorMessage(error, 'Top holders sync failed'));
        } finally {
            setTopHoldersSyncing(false);
        }
    };

    const handleDelete = (record: any) => {
        const stockCode = record['stock_basic.stock_code'];
        const stockName = record['stock_basic.name'];

        Modal.confirm({
            title: t('common.confirm_delete'),
            icon: <ExclamationCircleOutlined />,
            content: (
                <div>
                    <p>{t('common.confirm_delete_content', { name: stockName, code: stockCode })}</p>
                    <Text type="danger">{t('common.delete_warning')}</Text>
                </div>
            ),
            okText: t('common.confirm'),
            okType: 'danger',
            cancelText: t('common.cancel'),
            onOk: async () => {
                try {
                    setLoading(true);
                    const res = await marketApi.deleteStockData(stockCode);
                    message.success(res.message);

                    // Show detailed deletion counts notification
                    notification.info({
                        message: t('common.delete_success'),
                        description: (
                            <div>
                                <p>{t('common.deleted_items')}:</p>
                                <ul>
                                    {Object.entries(res.deleted_counts).map(([table, count]) => (
                                        <li key={table}>{table}: {count as number}</li>
                                    ))}
                                </ul>
                            </div>
                        ),
                        duration: 5
                    });

                    // Refresh current list
                    fetchData(activeTab, stockCode, pagination.current, pagination.pageSize);
                } catch (error) {
                    message.error(getApiErrorMessage(error, t('common.delete_failed')));
                } finally {
                    setLoading(false);
                }
            },
        });
    };

    const columnsMap: Record<string, any[]> = {
        stocks: [
            { title: t('stock_basic.stock_code'), dataIndex: ['stock_basic.stock_code'], key: 'stock_basic.stock_code' },
            { title: t('stock_basic.name'), dataIndex: ['stock_basic.name'], key: 'stock_basic.name' },
            { title: t('stock_basic.market'), dataIndex: ['stock_basic.market'], key: 'stock_basic.market', render: (m: string) => <Tag color="green">{m}</Tag> },
            { title: t('stock_basic.data_source'), dataIndex: ['stock_basic.data_source'], key: 'stock_basic.data_source', render: (s: string) => <Tag color="blue">{s}</Tag> },
            { title: t('stock_basic.updated_at'), dataIndex: ['stock_basic.updated_at'], key: 'stock_basic.updated_at', render: (t: string) => t ? dayjs(t).format('YYYY-MM-DD HH:mm:ss') : '-' },
            {
                title: t('common.action'),
                key: 'action',
                render: (_: any, record: any) => (
                    <Button
                        type="text"
                        danger
                        icon={<DeleteOutlined />}
                        onClick={(e) => {
                            e.stopPropagation();
                            handleDelete(record);
                        }}
                    >
                        {t('common.delete')}
                    </Button>
                )
            }
        ],
        kline: [
            { title: t('kline_data.date'), dataIndex: ['kline_data.date'], key: 'kline_data.date' },
            { title: t('kline_data.stock_code'), dataIndex: ['kline_data.stock_code'], key: 'kline_data.stock_code' },
            { title: t('kline_data.open'), dataIndex: ['kline_data.open'], key: 'kline_data.open' },
            { title: t('kline_data.close'), dataIndex: ['kline_data.close'], key: 'kline_data.close' },
            { title: t('kline_data.high'), dataIndex: ['kline_data.high'], key: 'kline_data.high' },
            { title: t('kline_data.low'), dataIndex: ['kline_data.low'], key: 'kline_data.low' },
            { title: t('kline_data.volume'), dataIndex: ['kline_data.volume'], key: 'kline_data.volume' },
        ],
        index_daily: [
            { title: t('index_daily.trade_date'), dataIndex: ['index_daily.trade_date'], key: 'index_daily.trade_date', render: (t: string) => dayjs(t).format('YYYY-MM-DD') },
            { title: t('index_daily.index_code'), dataIndex: ['index_daily.index_code'], key: 'index_daily.index_code' },
            { title: t('index_daily.open'), dataIndex: ['index_daily.open'], key: 'index_daily.open', render: (v: number) => v?.toFixed(2) },
            { title: t('index_daily.close'), dataIndex: ['index_daily.close'], key: 'index_daily.close', render: (v: number) => v?.toFixed(2) },
            { title: t('index_daily.high'), dataIndex: ['index_daily.high'], key: 'index_daily.high', render: (v: number) => v?.toFixed(2) },
            { title: t('index_daily.low'), dataIndex: ['index_daily.low'], key: 'index_daily.low', render: (v: number) => v?.toFixed(2) },
            { title: t('index_daily.volume'), dataIndex: ['index_daily.volume'], key: 'index_daily.volume' },
            { title: t('index_daily.change'), dataIndex: ['index_daily.change'], key: 'index_daily.change', render: (v: number) => v != null ? <Text type={v > 0 ? 'danger' : 'success'}>{v.toFixed(2)}</Text> : '-' },
            { title: t('index_daily.pct_chg'), dataIndex: ['index_daily.pct_chg'], key: 'index_daily.pct_chg', render: (v: number) => v != null ? <Text type={v > 0 ? 'danger' : 'success'}>{v.toFixed(2)}%</Text> : '-' },
        ],
        stock_indicators: [
            { title: t('stock_indicators.trade_date'), dataIndex: ['stock_indicators.trade_date'], key: 'stock_indicators.trade_date', render: (t: string) => dayjs(t).format('YYYY-MM-DD'), fixed: 'left', width: 110 },
            { title: t('stock_indicators.stock_code'), dataIndex: ['stock_indicators.stock_code'], key: 'stock_indicators.stock_code', fixed: 'left', width: 90 },
            { title: t('stock_indicators.ma5'), dataIndex: ['stock_indicators.ma5'], key: 'stock_indicators.ma5', render: (v: number) => v?.toFixed(2), width: 80 },
            { title: t('stock_indicators.ma10'), dataIndex: ['stock_indicators.ma10'], key: 'stock_indicators.ma10', render: (v: number) => v?.toFixed(2), width: 80 },
            { title: t('stock_indicators.ma20'), dataIndex: ['stock_indicators.ma20'], key: 'stock_indicators.ma20', render: (v: number) => v?.toFixed(2), width: 80 },
            { title: t('stock_indicators.macd'), dataIndex: ['stock_indicators.macd'], key: 'stock_indicators.macd', render: (v: number) => v?.toFixed(3), width: 80 },
            { title: t('stock_indicators.macd_signal'), dataIndex: ['stock_indicators.macd_signal'], key: 'stock_indicators.macd_signal', render: (v: number) => v?.toFixed(3), width: 90 },
            { title: t('stock_indicators.macd_hist'), dataIndex: ['stock_indicators.macd_hist'], key: 'stock_indicators.macd_hist', render: (v: number) => v != null ? <Text type={v > 0 ? 'danger' : 'success'}>{v.toFixed(3)}</Text> : '-', width: 90 },
            { title: t('stock_indicators.kdj_k'), dataIndex: ['stock_indicators.kdj_k'], key: 'stock_indicators.kdj_k', render: (v: number) => v?.toFixed(2), width: 80 },
            { title: t('stock_indicators.kdj_d'), dataIndex: ['stock_indicators.kdj_d'], key: 'stock_indicators.kdj_d', render: (v: number) => v?.toFixed(2), width: 80 },
            { title: t('stock_indicators.kdj_j'), dataIndex: ['stock_indicators.kdj_j'], key: 'stock_indicators.kdj_j', render: (v: number) => v?.toFixed(2), width: 80 },
            { title: t('stock_indicators.rsi_6'), dataIndex: ['stock_indicators.rsi_6'], key: 'stock_indicators.rsi_6', render: (v: number) => v?.toFixed(2), width: 80 },
            { title: t('stock_indicators.rsi_12'), dataIndex: ['stock_indicators.rsi_12'], key: 'stock_indicators.rsi_12', render: (v: number) => v?.toFixed(2), width: 80 },
            { title: t('stock_indicators.rsi_24'), dataIndex: ['stock_indicators.rsi_24'], key: 'stock_indicators.rsi_24', render: (v: number) => v?.toFixed(2), width: 80 },
            { title: t('stock_indicators.cci'), dataIndex: ['stock_indicators.cci'], key: 'stock_indicators.cci', render: (v: number) => v?.toFixed(2), width: 80 },
            { title: t('stock_indicators.atr'), dataIndex: ['stock_indicators.atr'], key: 'stock_indicators.atr', render: (v: number) => v?.toFixed(3), width: 80 },
            { title: t('stock_indicators.wr_14'), dataIndex: ['stock_indicators.wr_14'], key: 'stock_indicators.wr_14', render: (v: number) => v?.toFixed(2), width: 80 },
            { title: t('stock_indicators.boll_upper'), dataIndex: ['stock_indicators.boll_upper'], key: 'stock_indicators.boll_upper', render: (v: number) => v?.toFixed(2), width: 90 },
            { title: t('stock_indicators.boll_mid'), dataIndex: ['stock_indicators.boll_mid'], key: 'stock_indicators.boll_mid', render: (v: number) => v?.toFixed(2), width: 90 },
            { title: t('stock_indicators.boll_lower'), dataIndex: ['stock_indicators.boll_lower'], key: 'stock_indicators.boll_lower', render: (v: number) => v?.toFixed(2), width: 90 },
            { title: t('stock_indicators.obv'), dataIndex: ['stock_indicators.obv'], key: 'stock_indicators.obv', render: (v: number) => v?.toFixed(0), width: 100 },
        ],
        valuation: [
            { title: t('stock_valuation_history.data_date'), dataIndex: ['stock_valuation_history.data_date'], key: 'stock_valuation_history.data_date', render: (t: string) => dayjs(t).format('YYYY-MM-DD') },
            { title: t('stock_valuation_history.stock_code'), dataIndex: ['stock_valuation_history.stock_code'], key: 'stock_valuation_history.stock_code' },
            { title: t('stock_valuation_history.close_price'), dataIndex: ['stock_valuation_history.close_price'], key: 'stock_valuation_history.close_price', render: (v: number) => v != null ? v.toFixed(2) : '-' },
            { title: t('stock_valuation_history.pe_ttm'), dataIndex: ['stock_valuation_history.pe_ttm'], key: 'stock_valuation_history.pe_ttm', render: (v: number) => v != null ? v.toFixed(2) : '-' },
            { title: t('stock_valuation_history.pe_static'), dataIndex: ['stock_valuation_history.pe_static'], key: 'stock_valuation_history.pe_static', render: (v: number) => v != null ? v.toFixed(2) : '-' },
            { title: t('stock_valuation_history.pb'), dataIndex: ['stock_valuation_history.pb'], key: 'stock_valuation_history.pb', render: (v: number) => v != null ? v.toFixed(2) : '-' },
            { title: t('stock_valuation_history.total_market_value'), dataIndex: ['stock_valuation_history.total_market_value'], key: 'stock_valuation_history.total_market_value', render: (v: number) => v != null ? formatNumber(v) : '-' },
        ],
        financial: [] as any[], // Will be generated dynamically
        stock_interactive_qa: [
            { title: t('stock_interactive_qa.stock_code'), dataIndex: ['stock_interactive_qa.stock_code'], key: 'stock_code', width: 100, fixed: 'left' as const },
            { title: t('stock_interactive_qa.answer_time'), dataIndex: ['stock_interactive_qa.answer_time'], key: 'answer_time', width: 160, render: (v: string) => v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '-' },
            { title: t('stock_interactive_qa.trade_date'), dataIndex: ['stock_interactive_qa.trade_date'], key: 'trade_date', width: 120, render: (v: string) => v ? dayjs(v).format('YYYY-MM-DD') : '-' },
            { title: t('stock_interactive_qa.data_source'), dataIndex: ['stock_interactive_qa.data_source'], key: 'data_source', width: 140, render: (s: string) => <Tag color="blue">{s}</Tag> },
            {
                title: t('common.action'),
                key: 'action',
                width: 90,
                fixed: 'right',
                render: (_: any, record: any) => (
                    <Button
                        type="link"
                        size="small"
                        onClick={() => {
                            const stock = record['stock_interactive_qa.stock_code'];
                            const question = record['stock_interactive_qa.question'] || '-';
                            const answer = record['stock_interactive_qa.answer'] || '-';
                            setModalTitle(`${stock} - ${t('market.data_manager.stock_interactive_qa')}`);
                            setNewsDetail(`### ${t('stock_interactive_qa.question')}\n\n${question}\n\n### ${t('stock_interactive_qa.answer')}\n\n${answer}`);
                            setIsNewsModalVisible(true);
                        }}
                    >
                        {t('common.view')}
                    </Button>
                )
            }
        ],
        northbound: [
            { title: t('northbound_data.date'), dataIndex: ['northbound_data.date'], key: 'northbound_data.date' },
            { title: t('northbound_data.stock_code'), dataIndex: ['northbound_data.stock_code'], key: 'northbound_data.stock_code' },
            { title: t('northbound_data.close_price'), dataIndex: ['northbound_data.close_price'], key: 'northbound_data.close_price', render: (v: number) => v != null ? v.toFixed(2) : '-' },
            { title: t('northbound_data.change_percent'), dataIndex: ['northbound_data.change_percent'], key: 'northbound_data.change_percent', render: (v: number) => v != null ? <Text type={v > 0 ? 'danger' : 'success'}>{v.toFixed(2)}%</Text> : '-' },
            { title: t('northbound_data.hold_shares'), dataIndex: ['northbound_data.hold_shares'], key: 'northbound_data.hold_shares', render: (v: number) => v != null ? v.toLocaleString() : '-' },
            { title: t('northbound_data.hold_value'), dataIndex: ['northbound_data.hold_value'], key: 'northbound_data.hold_value', render: (v: number) => v != null ? formatNumber(v) : '-' },
            { title: t('northbound_data.hold_ratio'), dataIndex: ['northbound_data.hold_ratio'], key: 'northbound_data.hold_ratio', render: (v: number) => v != null ? `${(v * 100).toFixed(2)}%` : '-' },
            { title: t('northbound_data.net_buy_volume'), dataIndex: ['northbound_data.net_buy_volume'], key: 'northbound_data.net_buy_volume', render: (v: number) => v != null ? v.toLocaleString() : '-' },
            { title: t('northbound_data.net_buy_amount'), dataIndex: ['northbound_data.net_buy_amount'], key: 'northbound_data.net_buy_amount', render: (v: number) => v != null ? formatNumber(v) : '-' },
            { title: t('northbound_data.hold_value_change'), dataIndex: ['northbound_data.hold_value_change'], key: 'northbound_data.hold_value_change', render: (v: number) => v != null ? formatNumber(v) : '-' },
        ],

        dragontiger: [
            {
                title: t('dragon_tiger_data.trade_date'),
                dataIndex: ['dragon_tiger_data.trade_date'],
                key: 'dragon_tiger_data.trade_date',
                sorter: (a: any, b: any) => (a['dragon_tiger_data.trade_date'] || '').localeCompare(b['dragon_tiger_data.trade_date'] || ''),
                defaultSortOrder: 'descend' as const
            },
            {
                title: t('dragon_tiger_data.stock_code'),
                dataIndex: ['dragon_tiger_data.stock_code'],
                key: 'dragon_tiger_data.stock_code',
                sorter: (a: any, b: any) => (a['dragon_tiger_data.stock_code'] || '').localeCompare(b['dragon_tiger_data.stock_code'] || '')
            },
            {
                title: t('dragon_tiger_data.stock_name'),
                dataIndex: ['dragon_tiger_data.stock_name'],
                key: 'dragon_tiger_data.stock_name',
                sorter: (a: any, b: any) => (a['dragon_tiger_data.stock_name'] || '').localeCompare(b['dragon_tiger_data.stock_name'] || '')
            },
            {
                title: t('dragon_tiger_data.net_buy_amount'),
                dataIndex: ['dragon_tiger_data.net_buy_amount'],
                key: 'dragon_tiger_data.net_buy_amount',
                render: (v: number) => v != null ? <Text type={v >= 0 ? 'danger' : 'success'}>{formatNumber(v)}</Text> : '-',
                sorter: (a: any, b: any) => (a['dragon_tiger_data.net_buy_amount'] || 0) - (b['dragon_tiger_data.net_buy_amount'] || 0)
            },
            {
                title: t('dragon_tiger_data.buy_amount'),
                dataIndex: ['dragon_tiger_data.buy_amount'],
                key: 'dragon_tiger_data.buy_amount',
                render: (v: number) => v != null ? formatNumber(v) : '-',
                sorter: (a: any, b: any) => (a['dragon_tiger_data.buy_amount'] || 0) - (b['dragon_tiger_data.buy_amount'] || 0)
            },
            {
                title: t('dragon_tiger_data.sell_amount'),
                dataIndex: ['dragon_tiger_data.sell_amount'],
                key: 'dragon_tiger_data.sell_amount',
                render: (v: number) => v != null ? formatNumber(v) : '-',
                sorter: (a: any, b: any) => (a['dragon_tiger_data.sell_amount'] || 0) - (b['dragon_tiger_data.sell_amount'] || 0)
            },
            {
                title: t('dragon_tiger_data.price_change_percent'),
                dataIndex: ['dragon_tiger_data.price_change_percent'],
                key: 'dragon_tiger_data.price_change_percent',
                render: (v: number) => v != null ? <Text type={v > 0 ? 'danger' : 'success'}>{v.toFixed(2)}%</Text> : '-',
                sorter: (a: any, b: any) => (a['dragon_tiger_data.price_change_percent'] || 0) - (b['dragon_tiger_data.price_change_percent'] || 0)
            },
            {
                title: t('dragon_tiger_data.listing_reason'),
                dataIndex: ['dragon_tiger_data.listing_reason'],
                key: 'dragon_tiger_data.listing_reason',
                ellipsis: true,
                sorter: (a: any, b: any) => (a['dragon_tiger_data.listing_reason'] || '').localeCompare(b['dragon_tiger_data.listing_reason'] || '')
            },
        ],
        realtime: [
            { title: t('stock_realtime_market.stock_code'), dataIndex: ['stock_realtime_market.stock_code'], key: 'stock_realtime_market.stock_code', width: 90, fixed: 'left' },
            { title: t('stock_realtime_market.current_price'), dataIndex: ['stock_realtime_market.current_price'], key: 'stock_realtime_market.current_price', width: 100, render: (v: number) => v?.toFixed(2) },
            { title: t('stock_realtime_market.change_percent'), dataIndex: ['stock_realtime_market.change_percent'], key: 'stock_realtime_market.change_percent', width: 100, render: (v: number) => <Text type={v > 0 ? 'danger' : v < 0 ? 'success' : undefined}>{v?.toFixed(2)}%</Text> },
            { title: t('stock_realtime_market.change_amount'), dataIndex: ['stock_realtime_market.change_amount'], key: 'stock_realtime_market.change_amount', width: 100, render: (v: number) => <Text type={v > 0 ? 'danger' : v < 0 ? 'success' : undefined}>{v?.toFixed(2)}</Text> },
            { title: t('stock_realtime_market.volume'), dataIndex: ['stock_realtime_market.volume'], key: 'stock_realtime_market.volume', width: 120 },
            { title: t('stock_realtime_market.turnover'), dataIndex: ['stock_realtime_market.turnover'], key: 'stock_realtime_market.turnover', width: 120, render: (v: number) => v != null ? formatNumber(v) : '-' },
            { title: t('stock_realtime_market.change_60days'), dataIndex: ['stock_realtime_market.change_60days'], key: 'stock_realtime_market.change_60days', width: 120, render: (v: number) => <Text type={v > 0 ? 'danger' : v < 0 ? 'success' : undefined}>{v != null ? v.toFixed(2) + '%' : '-'}</Text> },
            { title: t('stock_realtime_market.change_ytd'), dataIndex: ['stock_realtime_market.change_ytd'], key: 'stock_realtime_market.change_ytd', width: 120, render: (v: number) => <Text type={v > 0 ? 'danger' : v < 0 ? 'success' : undefined}>{v != null ? v.toFixed(2) + '%' : '-'}</Text> },
            { title: t('stock_realtime_market.main_net_inflow_today'), dataIndex: ['stock_realtime_market.main_net_inflow_today'], key: 'stock_realtime_market.main_net_inflow_today', width: 140, render: (v: number) => <Text type={v > 0 ? 'danger' : v < 0 ? 'success' : undefined}>{v != null ? formatNumber(v) : '-'}</Text> },
            { title: t('stock_realtime_market.main_net_inflow_5d'), dataIndex: ['stock_realtime_market.main_net_inflow_5d'], key: 'stock_realtime_market.main_net_inflow_5d', width: 140, render: (v: number) => <Text type={v > 0 ? 'danger' : v < 0 ? 'success' : undefined}>{v != null ? formatNumber(v) : '-'}</Text> },
            { title: t('stock_realtime_market.main_net_inflow_10d'), dataIndex: ['stock_realtime_market.main_net_inflow_10d'], key: 'stock_realtime_market.main_net_inflow_10d', width: 140, render: (v: number) => <Text type={v > 0 ? 'danger' : v < 0 ? 'success' : undefined}>{v != null ? formatNumber(v) : '-'}</Text> },
            { title: t('stock_realtime_market.high'), dataIndex: ['stock_realtime_market.high'], key: 'stock_realtime_market.high', width: 100, render: (v: number) => v?.toFixed(2) },
            { title: t('stock_realtime_market.low'), dataIndex: ['stock_realtime_market.low'], key: 'stock_realtime_market.low', width: 100, render: (v: number) => v?.toFixed(2) },
            { title: t('stock_realtime_market.open'), dataIndex: ['stock_realtime_market.open'], key: 'stock_realtime_market.open', width: 100, render: (v: number) => v?.toFixed(2) },
            { title: t('stock_realtime_market.prev_close'), dataIndex: ['stock_realtime_market.prev_close'], key: 'stock_realtime_market.prev_close', width: 100, render: (v: number) => v?.toFixed(2) },
            { title: t('stock_realtime_market.turnover_rate'), dataIndex: ['stock_realtime_market.turnover_rate'], key: 'stock_realtime_market.turnover_rate', width: 100, render: (v: number) => v != null ? v.toFixed(2) + '%' : '-' },
            { title: t('stock_realtime_market.pe_dynamic'), dataIndex: ['stock_realtime_market.pe_dynamic'], key: 'stock_realtime_market.pe_dynamic', width: 100, render: (v: number) => v?.toFixed(2) },
            { title: t('stock_realtime_market.pb_ratio'), dataIndex: ['stock_realtime_market.pb_ratio'], key: 'stock_realtime_market.pb_ratio', width: 100, render: (v: number) => v?.toFixed(2) },
            { title: t('stock_realtime_market.total_market_cap'), dataIndex: ['stock_realtime_market.total_market_cap'], key: 'stock_realtime_market.total_market_cap', width: 150, render: (v: number) => v != null ? formatNumber(v) : '-' },
            { title: t('stock_realtime_market.timestamp'), dataIndex: ['stock_realtime_market.timestamp'], key: 'stock_realtime_market.timestamp', width: 180, render: (t: string) => t ? dayjs(t).format('YYYY-MM-DD HH:mm:ss') : '-' },
        ],
        industry: [
            { title: t('industry_data.rank'), dataIndex: ['industry_data.rank'], key: 'industry_data.rank', width: 80 },
            { title: t('industry_data.board_name'), dataIndex: ['industry_data.board_name'], key: 'industry_data.board_name', width: 120 },
            { title: t('industry_data.board_code'), dataIndex: ['industry_data.board_code'], key: 'industry_data.board_code', width: 100 },
            { title: t('industry_data.latest_price'), dataIndex: ['industry_data.latest_price'], key: 'industry_data.latest_price', width: 100 },
            { title: t('industry_data.change_percent'), dataIndex: ['industry_data.change_percent'], key: 'industry_data.change_percent', width: 100, render: (v: number) => <Text type={v > 0 ? 'danger' : v < 0 ? 'success' : undefined}>{v?.toFixed(2)}%</Text> },
            { title: t('industry_data.change_amount'), dataIndex: ['industry_data.change_amount'], key: 'industry_data.change_amount', width: 100, render: (v: number) => <Text type={v > 0 ? 'danger' : v < 0 ? 'success' : undefined}>{v?.toFixed(2)}</Text> },
            { title: t('industry_data.total_market_cap'), dataIndex: ['industry_data.total_market_cap'], key: 'industry_data.total_market_cap', width: 150, render: (v: number) => formatNumber(v) },
            { title: t('industry_data.turnover_rate'), dataIndex: ['industry_data.turnover_rate'], key: 'industry_data.turnover_rate', width: 100, render: (v: number) => v?.toFixed(2) + '%' },
            { title: t('industry_data.rising_stocks_count'), dataIndex: ['industry_data.rising_stocks_count'], key: 'industry_data.rising_stocks_count', width: 100, render: (v: number) => <Text type="danger">{v}</Text> },
            { title: t('industry_data.falling_stocks_count'), dataIndex: ['industry_data.falling_stocks_count'], key: 'industry_data.falling_stocks_count', width: 100, render: (v: number) => <Text type="success">{v}</Text> },
            { title: t('industry_data.leading_stock_name'), dataIndex: ['industry_data.leading_stock_name'], key: 'industry_data.leading_stock_name', width: 120 },
            { title: t('industry_data.leading_stock_change_percent'), dataIndex: ['industry_data.leading_stock_change_percent'], key: 'industry_data.leading_stock_change_percent', width: 120, render: (v: number) => <Text type={v > 0 ? 'danger' : v < 0 ? 'success' : undefined}>{v?.toFixed(2)}%</Text> },
            { title: t('industry_data.updated_at'), dataIndex: ['industry_data.timestamp'], key: 'industry_data.timestamp', width: 180, render: (t: string) => t ? dayjs(t).format('YYYY-MM-DD HH:mm:ss') : '-' },
        ],
        stock_limit_up_pool: [
            { title: t('stock_limit_up_pool.update_date'), dataIndex: ['stock_limit_up_pool.update_date'], key: 'stock_limit_up_pool.update_date', sorter: true },
            { title: t('stock_limit_up_pool.stock_code'), dataIndex: ['stock_limit_up_pool.stock_code'], key: 'stock_limit_up_pool.stock_code' },
            { title: t('stock_limit_up_pool.stock_name'), dataIndex: ['stock_limit_up_pool.stock_name'], key: 'stock_limit_up_pool.stock_name' },
            { title: t('stock_limit_up_pool.limit_up_price'), dataIndex: ['stock_limit_up_pool.limit_up_price'], key: 'stock_limit_up_pool.limit_up_price', render: (v: number) => v != null ? v.toFixed(2) : '-' },
            { title: t('stock_limit_up_pool.pct_chg'), dataIndex: ['stock_limit_up_pool.pct_chg'], key: 'stock_limit_up_pool.pct_chg', render: (v: number) => v != null ? <Text type={v > 0 ? 'danger' : 'success'}>{v.toFixed(3)}%</Text> : '-' },
            { title: t('stock_limit_up_pool.turnover'), dataIndex: ['stock_limit_up_pool.turnover'], key: 'stock_limit_up_pool.turnover', render: (v: number) => v != null ? formatNumber(v) : '-' },
            { title: t('stock_limit_up_pool.turnover_rate'), dataIndex: ['stock_limit_up_pool.turnover_rate'], key: 'stock_limit_up_pool.turnover_rate', render: (v: number) => v != null ? `${v.toFixed(2)}%` : '-' },
            { title: t('stock_limit_up_pool.fund_amount'), dataIndex: ['stock_limit_up_pool.fund_amount'], key: 'stock_limit_up_pool.fund_amount', render: (v: number) => v != null ? formatNumber(v, 0) : '-' },
            { title: t('stock_limit_up_pool.first_limit_up_time'), dataIndex: ['stock_limit_up_pool.first_limit_up_time'], key: 'stock_limit_up_pool.first_limit_up_time' },
            { title: t('stock_limit_up_pool.last_limit_up_time'), dataIndex: ['stock_limit_up_pool.last_limit_up_time'], key: 'stock_limit_up_pool.last_limit_up_time' },
            { title: t('stock_limit_up_pool.open_times'), dataIndex: ['stock_limit_up_pool.open_times'], key: 'stock_limit_up_pool.open_times' },
            { title: t('stock_limit_up_pool.limit_up_stats'), dataIndex: ['stock_limit_up_pool.limit_up_stats'], key: 'stock_limit_up_pool.limit_up_stats' },
            { title: t('stock_limit_up_pool.limit_up_days'), dataIndex: ['stock_limit_up_pool.limit_up_days'], key: 'stock_limit_up_pool.limit_up_days', render: (t: string) => <Tag color="red">{t}板</Tag> },
            { title: t('stock_limit_up_pool.limit_up_reason'), dataIndex: ['stock_limit_up_pool.limit_up_reason'], key: 'stock_limit_up_pool.limit_up_reason', ellipsis: true },
        ],
        stock_limit_down_pool: [
            { title: t('stock_limit_down_pool.update_date'), dataIndex: ['stock_limit_down_pool.update_date'], key: 'stock_limit_down_pool.update_date', sorter: true },
            { title: t('stock_limit_down_pool.stock_code'), dataIndex: ['stock_limit_down_pool.stock_code'], key: 'stock_limit_down_pool.stock_code' },
            { title: t('stock_limit_down_pool.stock_name'), dataIndex: ['stock_limit_down_pool.stock_name'], key: 'stock_limit_down_pool.stock_name' },
            { title: t('stock_limit_down_pool.limit_down_price'), dataIndex: ['stock_limit_down_pool.limit_down_price'], key: 'stock_limit_down_pool.limit_down_price', render: (v: number) => v != null ? v.toFixed(2) : '-' },
            { title: t('stock_limit_down_pool.pct_chg'), dataIndex: ['stock_limit_down_pool.pct_chg'], key: 'stock_limit_down_pool.pct_chg', render: (v: number) => v != null ? <Text type={v > 0 ? 'danger' : 'success'}>{v.toFixed(3)}%</Text> : '-' },
            { title: t('stock_limit_down_pool.turnover'), dataIndex: ['stock_limit_down_pool.turnover'], key: 'stock_limit_down_pool.turnover', render: (v: number) => v != null ? formatNumber(v) : '-' },
            { title: t('stock_limit_down_pool.turnover_rate'), dataIndex: ['stock_limit_down_pool.turnover_rate'], key: 'stock_limit_down_pool.turnover_rate', render: (v: number) => v != null ? `${v.toFixed(2)}%` : '-' },
            { title: t('stock_limit_down_pool.fund_amount'), dataIndex: ['stock_limit_down_pool.fund_amount'], key: 'stock_limit_down_pool.fund_amount', render: (v: number) => v != null ? formatNumber(v, 0) : '-' },
            { title: t('stock_limit_down_pool.board_turnover'), dataIndex: ['stock_limit_down_pool.board_turnover'], key: 'stock_limit_down_pool.board_turnover', render: (v: number) => v != null ? formatNumber(v) : '-' },
            { title: t('stock_limit_down_pool.first_limit_down_time'), dataIndex: ['stock_limit_down_pool.first_limit_down_time'], key: 'stock_limit_down_pool.first_limit_down_time' },
            { title: t('stock_limit_down_pool.last_limit_down_time'), dataIndex: ['stock_limit_down_pool.last_limit_down_time'], key: 'stock_limit_down_pool.last_limit_down_time' },
            { title: t('stock_limit_down_pool.open_times'), dataIndex: ['stock_limit_down_pool.open_times'], key: 'stock_limit_down_pool.open_times' },
            { title: t('stock_limit_down_pool.limit_down_stats'), dataIndex: ['stock_limit_down_pool.limit_down_stats'], key: 'stock_limit_down_pool.limit_down_stats' },
            { title: t('stock_limit_down_pool.limit_down_days'), dataIndex: ['stock_limit_down_pool.limit_down_days'], key: 'stock_limit_down_pool.limit_down_days', render: (t: string) => <Tag color="green">{t}板</Tag> },
            { title: t('stock_limit_down_pool.limit_down_reason'), dataIndex: ['stock_limit_down_pool.limit_down_reason'], key: 'stock_limit_down_pool.limit_down_reason', ellipsis: true },
            { title: t('stock_limit_down_pool.dynamic_pe'), dataIndex: ['stock_limit_down_pool.dynamic_pe'], key: 'stock_limit_down_pool.dynamic_pe', render: (v: number) => v != null ? v.toFixed(2) : '-' },
        ],
        stock_zhaban_pool: [
            { title: t('stock_zhaban_pool.update_date'), dataIndex: ['stock_zhaban_pool.update_date'], key: 'stock_zhaban_pool.update_date', sorter: true },
            { title: t('stock_zhaban_pool.stock_code'), dataIndex: ['stock_zhaban_pool.stock_code'], key: 'stock_zhaban_pool.stock_code' },
            { title: t('stock_zhaban_pool.stock_name'), dataIndex: ['stock_zhaban_pool.stock_name'], key: 'stock_zhaban_pool.stock_name' },
            { title: t('stock_zhaban_pool.latest_price'), dataIndex: ['stock_zhaban_pool.latest_price'], key: 'stock_zhaban_pool.latest_price', render: (v: number) => v != null ? v.toFixed(2) : '-' },
            { title: t('stock_zhaban_pool.pct_chg'), dataIndex: ['stock_zhaban_pool.pct_chg'], key: 'stock_zhaban_pool.pct_chg', render: (v: number) => v != null ? <Text type={v > 0 ? 'danger' : 'success'}>{v.toFixed(3)}%</Text> : '-' },
            { title: t('stock_zhaban_pool.turnover'), dataIndex: ['stock_zhaban_pool.turnover'], key: 'stock_zhaban_pool.turnover', render: (v: number) => v != null ? formatNumber(v) : '-' },
            { title: t('stock_zhaban_pool.turnover_rate'), dataIndex: ['stock_zhaban_pool.turnover_rate'], key: 'stock_zhaban_pool.turnover_rate', render: (v: number) => v != null ? `${v.toFixed(2)}%` : '-' },
            { title: t('stock_zhaban_pool.limit_up_price'), dataIndex: ['stock_zhaban_pool.limit_up_price'], key: 'stock_zhaban_pool.limit_up_price', render: (v: number) => v != null ? v.toFixed(2) : '-' },
            { title: t('stock_zhaban_pool.first_limit_up_time'), dataIndex: ['stock_zhaban_pool.first_limit_up_time'], key: 'stock_zhaban_pool.first_limit_up_time' },
            { title: t('stock_zhaban_pool.last_limit_up_time'), dataIndex: ['stock_zhaban_pool.last_limit_up_time'], key: 'stock_zhaban_pool.last_limit_up_time' },
            { title: t('stock_zhaban_pool.open_times'), dataIndex: ['stock_zhaban_pool.open_times'], key: 'stock_zhaban_pool.open_times' },
            { title: t('stock_zhaban_pool.limit_up_stats'), dataIndex: ['stock_zhaban_pool.limit_up_stats'], key: 'stock_zhaban_pool.limit_up_stats' },
            { title: t('stock_zhaban_pool.limit_up_reason'), dataIndex: ['stock_zhaban_pool.limit_up_reason'], key: 'stock_zhaban_pool.limit_up_reason', ellipsis: true },
            { title: t('stock_zhaban_pool.speed_increase'), dataIndex: ['stock_zhaban_pool.speed_increase'], key: 'stock_zhaban_pool.speed_increase', render: (v: number) => v != null ? `${v.toFixed(3)}%` : '-' },
        ],
        stock_money_flow: [
            { title: t('stock_money_flow.trade_date'), dataIndex: ['stock_money_flow.trade_date'], key: 'trade_date', width: 110, fixed: 'left' },
            { title: t('stock_money_flow.stock_code'), dataIndex: ['stock_money_flow.stock_code'], key: 'stock_code', width: 100, fixed: 'left' },
            { title: t('stock_money_flow.close_price'), dataIndex: ['stock_money_flow.close_price'], key: 'close_price', width: 100, render: (v: number) => v?.toFixed(2) },
            { title: t('stock_money_flow.change_pct'), dataIndex: ['stock_money_flow.change_pct'], key: 'change_pct', width: 100, render: (v: number) => <Text type={v > 0 ? 'danger' : v < 0 ? 'success' : undefined}>{v?.toFixed(2)}%</Text> },

            { title: t('stock_money_flow.net_inflow_main'), dataIndex: ['stock_money_flow.net_inflow_main'], key: 'net_inflow_main', width: 120, render: (v: number) => <Text type={v > 0 ? 'danger' : 'success'}>{v ? formatNumber(v) : '-'}</Text> },
            { title: t('stock_money_flow.net_inflow_ratio_main'), dataIndex: ['stock_money_flow.net_inflow_ratio_main'], key: 'net_inflow_ratio_main', width: 100, render: (v: number) => <Text type={v > 0 ? 'danger' : 'success'}>{v?.toFixed(2)}%</Text> },

            { title: t('stock_money_flow.net_inflow_huge'), dataIndex: ['stock_money_flow.net_inflow_huge'], key: 'net_inflow_huge', width: 120, render: (v: number) => <Text type={v > 0 ? 'danger' : 'success'}>{v ? formatNumber(v) : '-'}</Text> },
            { title: t('stock_money_flow.net_inflow_ratio_huge'), dataIndex: ['stock_money_flow.net_inflow_ratio_huge'], key: 'net_inflow_ratio_huge', width: 100, render: (v: number) => v?.toFixed(2) + '%' },

            { title: t('stock_money_flow.net_inflow_large'), dataIndex: ['stock_money_flow.net_inflow_large'], key: 'net_inflow_large', width: 120, render: (v: number) => <Text type={v > 0 ? 'danger' : 'success'}>{v ? formatNumber(v) : '-'}</Text> },
            { title: t('stock_money_flow.net_inflow_ratio_large'), dataIndex: ['stock_money_flow.net_inflow_ratio_large'], key: 'net_inflow_ratio_large', width: 100, render: (v: number) => v?.toFixed(2) + '%' },

            { title: t('stock_money_flow.net_inflow_medium'), dataIndex: ['stock_money_flow.net_inflow_medium'], key: 'net_inflow_medium', width: 120, render: (v: number) => <Text type={v > 0 ? 'danger' : 'success'}>{v ? formatNumber(v) : '-'}</Text> },
            { title: t('stock_money_flow.net_inflow_ratio_medium'), dataIndex: ['stock_money_flow.net_inflow_ratio_medium'], key: 'net_inflow_ratio_medium', width: 100, render: (v: number) => v?.toFixed(2) + '%' },

            { title: t('stock_money_flow.net_inflow_small'), dataIndex: ['stock_money_flow.net_inflow_small'], key: 'net_inflow_small', width: 120, render: (v: number) => <Text type={v > 0 ? 'danger' : 'success'}>{v ? formatNumber(v) : '-'}</Text> },
            { title: t('stock_money_flow.net_inflow_ratio_small'), dataIndex: ['stock_money_flow.net_inflow_ratio_small'], key: 'net_inflow_ratio_small', width: 100, render: (v: number) => v?.toFixed(2) + '%' },

            { title: t('stock_money_flow.net_inflow_main_3d'), dataIndex: ['stock_money_flow.net_inflow_main_3d'], key: 'net_inflow_main_3d', width: 130, render: (v: number) => <Text type={v > 0 ? 'danger' : 'success'}>{v ? formatNumber(v) : '-'}</Text> },
            { title: t('stock_money_flow.net_inflow_main_5d'), dataIndex: ['stock_money_flow.net_inflow_main_5d'], key: 'net_inflow_main_5d', width: 130, render: (v: number) => <Text type={v > 0 ? 'danger' : 'success'}>{v ? formatNumber(v) : '-'}</Text> },
            { title: t('stock_money_flow.net_inflow_main_10d'), dataIndex: ['stock_money_flow.net_inflow_main_10d'], key: 'net_inflow_main_10d', width: 130, render: (v: number) => <Text type={v > 0 ? 'danger' : 'success'}>{v ? formatNumber(v) : '-'}</Text> },
        ],
        stock_shareholder_count: [
            { title: t('stock_shareholder_count.end_date'), dataIndex: ['stock_shareholder_count.end_date'], key: 'end_date', width: 110, fixed: 'left' },
            { title: t('stock_shareholder_count.ann_date'), dataIndex: ['stock_shareholder_count.ann_date'], key: 'ann_date', width: 110 },
            { title: t('stock_shareholder_count.stock_code'), dataIndex: ['stock_shareholder_count.stock_code'], key: 'stock_code', width: 100 },
            { title: t('stock_shareholder_count.holder_count'), dataIndex: ['stock_shareholder_count.holder_count'], key: 'holder_count', width: 100 },
            { title: t('stock_shareholder_count.holder_count_change'), dataIndex: ['stock_shareholder_count.holder_count_change'], key: 'holder_count_change', width: 100, render: (v: number) => <Text type={v > 0 ? 'danger' : v < 0 ? 'success' : undefined}>{v}</Text> },
            { title: t('stock_shareholder_count.holder_count_change_ratio'), dataIndex: ['stock_shareholder_count.holder_count_change_ratio'], key: 'holder_count_change_ratio', width: 110, render: (v: number) => <Text type={v > 0 ? 'danger' : v < 0 ? 'success' : undefined}>{v?.toFixed(2)}%</Text> },
            { title: t('stock_shareholder_count.avg_hold_shares'), dataIndex: ['stock_shareholder_count.avg_hold_shares'], key: 'avg_hold_shares', width: 100, render: (v: number) => v?.toFixed(0) },
            { title: t('stock_shareholder_count.avg_hold_value'), dataIndex: ['stock_shareholder_count.avg_hold_value'], key: 'avg_hold_value', width: 120, render: (v: number) => v ? formatNumber(v) : '-' },
            { title: t('stock_shareholder_count.price_at_end'), dataIndex: ['stock_shareholder_count.price_at_end'], key: 'price_at_end', width: 100, render: (v: number) => v?.toFixed(2) },
            { title: t('stock_shareholder_count.price_change_ratio'), dataIndex: ['stock_shareholder_count.price_change_ratio'], key: 'price_change_ratio', width: 110, render: (v: number) => <Text type={v > 0 ? 'danger' : v < 0 ? 'success' : undefined}>{v?.toFixed(2)}%</Text> },
            { title: t('stock_shareholder_count.total_mv'), dataIndex: ['stock_shareholder_count.total_mv'], key: 'total_mv', width: 120, render: (v: number) => v ? formatNumber(v) : '-' },
            { title: t('stock_shareholder_count.total_share'), dataIndex: ['stock_shareholder_count.total_share'], key: 'total_share', width: 120, render: (v: number) => v ? formatNumber(v) : '-' },
            { title: t('stock_shareholder_count.share_change'), dataIndex: ['stock_shareholder_count.share_change'], key: 'share_change', width: 120, render: (v: number) => v ? formatNumber(v) : '-' },
            { title: t('stock_shareholder_count.share_change_reason'), dataIndex: ['stock_shareholder_count.share_change_reason'], key: 'share_change_reason', width: 150 },
        ],
        stock_pledge_risk: [
            { title: t('stock_pledge_risk.stock_code'), dataIndex: ['stock_pledge_risk.stock_code'], key: 'stock_code' },
            { title: t('stock_pledge_risk.pledgor_name'), dataIndex: ['stock_pledge_risk.pledgor_name'], key: 'pledgor_name' },
            { title: t('stock_pledge_risk.pledgee_name'), dataIndex: ['stock_pledge_risk.pledgee_name'], key: 'pledgee_name' },
            { title: t('stock_pledge_risk.pledge_shares'), dataIndex: ['stock_pledge_risk.pledge_shares'], key: 'pledge_shares' },
            { title: t('stock_pledge_risk.pledge_ratio_to_total'), dataIndex: ['stock_pledge_risk.pledge_ratio_to_total'], key: 'pledge_ratio_to_total', render: (v: number) => `${v}%` },
            { title: t('stock_pledge_risk.current_price'), dataIndex: ['stock_pledge_risk.current_price'], key: 'current_price' },
            { title: t('stock_pledge_risk.liquidate_price'), dataIndex: ['stock_pledge_risk.liquidate_price'], key: 'liquidate_price' },
            { title: t('stock_pledge_risk.ann_date'), dataIndex: ['stock_pledge_risk.ann_date'], key: 'ann_date' },
        ],
        stock_pledge_summary: [
            { title: t('stock_pledge_summary.stock_code'), dataIndex: ['stock_pledge_summary.stock_code'], key: 'stock_code', fixed: 'left' as const, width: 100 },
            { title: t('stock_pledge_summary.trade_date'), dataIndex: ['stock_pledge_summary.trade_date'], key: 'trade_date', width: 110 },
            { title: t('stock_pledge_summary.pledge_ratio'), dataIndex: ['stock_pledge_summary.pledge_ratio'], key: 'pledge_ratio', width: 100, render: (v: number) => v != null ? <Text type={v > 50 ? 'danger' : v > 20 ? 'warning' : undefined}>{v.toFixed(2)}%</Text> : '-' },
            { title: t('stock_pledge_summary.pledge_shares'), dataIndex: ['stock_pledge_summary.pledge_shares'], key: 'pledge_shares', width: 120, render: (v: number) => v != null ? formatNumber(v) : '-' },
            { title: t('stock_pledge_summary.pledge_market_value'), dataIndex: ['stock_pledge_summary.pledge_market_value'], key: 'pledge_market_value', width: 120, render: (v: number) => v != null ? formatNumber(v) : '-' },
            { title: t('stock_pledge_summary.pledge_count'), dataIndex: ['stock_pledge_summary.pledge_count'], key: 'pledge_count', width: 100 },
            { title: t('stock_pledge_summary.unrestricted_pledge_shares'), dataIndex: ['stock_pledge_summary.unrestricted_pledge_shares'], key: 'unrestricted_pledge_shares', width: 120, render: (v: number) => v != null ? formatNumber(v) : '-' },
            { title: t('stock_pledge_summary.restricted_pledge_shares'), dataIndex: ['stock_pledge_summary.restricted_pledge_shares'], key: 'restricted_pledge_shares', width: 120, render: (v: number) => v != null ? formatNumber(v) : '-' },
            { title: t('stock_pledge_summary.total_share'), dataIndex: ['stock_pledge_summary.total_share'], key: 'total_share', width: 120, render: (v: number) => v != null ? formatNumber(v) : '-' },
            { title: t('stock_pledge_summary.industry'), dataIndex: ['stock_pledge_summary.industry'], key: 'industry', width: 150 },
            { title: t('stock_pledge_summary.price_change_1y'), dataIndex: ['stock_pledge_summary.price_change_1y'], key: 'price_change_1y', width: 120, render: (v: number) => v != null ? <Text type={v > 0 ? 'danger' : 'success'}>{v.toFixed(2)}%</Text> : '-' },
        ],

        stock_insider_trading: [
            { title: t('stock_insider_trading.trade_date'), dataIndex: ['stock_insider_trading.trade_date'], key: 'trade_date' },
            { title: t('stock_insider_trading.stock_code'), dataIndex: ['stock_insider_trading.stock_code'], key: 'stock_code' },
            { title: t('stock_insider_trading.insider_name'), dataIndex: ['stock_insider_trading.insider_name'], key: 'insider_name' },
            { title: t('stock_insider_trading.change_type'), dataIndex: ['stock_insider_trading.change_type'], key: 'change_type', render: (v: string) => v ? <Tag color={v.includes('减') ? 'green' : 'red'}>{v}</Tag> : null },
            { title: t('stock_insider_trading.change_shares'), dataIndex: ['stock_insider_trading.change_shares'], key: 'change_shares' },
            { title: t('stock_insider_trading.change_avg_price'), dataIndex: ['stock_insider_trading.change_avg_price'], key: 'change_avg_price' },
            { title: t('stock_insider_trading.change_ratio'), dataIndex: ['stock_insider_trading.change_ratio'], key: 'change_ratio', render: (v: number) => v ? `${v}%` : '-' },
            { title: t('stock_insider_trading.shares_after_change'), dataIndex: ['stock_insider_trading.shares_after_change'], key: 'shares_after_change' },
            { title: t('stock_insider_trading.ratio_after_change'), dataIndex: ['stock_insider_trading.ratio_after_change'], key: 'ratio_after_change', render: (v: number) => v ? `${v}%` : '-' },
            { title: t('stock_insider_trading.ann_date'), dataIndex: ['stock_insider_trading.ann_date'], key: 'ann_date' },
        ],
        stock_lockup_release: [
            { title: t('stock_lockup_release.release_date'), dataIndex: ['stock_lockup_release.release_date'], key: 'release_date' },
            { title: t('stock_lockup_release.stock_code'), dataIndex: ['stock_lockup_release.stock_code'], key: 'stock_code' },
            { title: t('stock_lockup_release.release_shares'), dataIndex: ['stock_lockup_release.release_shares'], key: 'release_shares' },
            { title: t('stock_lockup_release.release_market_value'), dataIndex: ['stock_lockup_release.release_market_value'], key: 'release_market_value' },
            { title: t('stock_lockup_release.ratio_to_total'), dataIndex: ['stock_lockup_release.ratio_to_total'], key: 'ratio_to_total', render: (v: number) => `${v}%` },
            { title: t('stock_lockup_release.release_type'), dataIndex: ['stock_lockup_release.release_type'], key: 'release_type', ellipsis: true },
        ],
        stock_margin_data: [
            { title: t('stock_margin_data.trade_date'), dataIndex: ['stock_margin_data.trade_date'], key: 'trade_date' },
            { title: t('stock_margin_data.stock_code'), dataIndex: ['stock_margin_data.stock_code'], key: 'stock_code' },
            { title: t('stock_margin_data.margin_balance'), dataIndex: ['stock_margin_data.margin_balance'], key: 'margin_balance', render: (v: number) => formatNumber(v) },
            { title: t('stock_margin_data.margin_buy_amount'), dataIndex: ['stock_margin_data.margin_buy_amount'], key: 'margin_buy_amount', render: (v: number) => formatNumber(v) },
            { title: t('stock_margin_data.short_balance'), dataIndex: ['stock_margin_data.short_balance'], key: 'short_balance', render: (v: number) => formatNumber(v) },
            { title: t('stock_margin_data.margin_short_balance'), dataIndex: ['stock_margin_data.margin_short_balance'], key: 'margin_short_balance', render: (v: number) => formatNumber(v) },
            { title: t('stock_margin_data.data_source'), dataIndex: ['stock_margin_data.data_source'], key: 'data_source', render: (v: string) => <Tag color="blue">{v}</Tag> },
        ],
        stock_block_trade: [
            { title: t('stock_block_trade.trade_date'), dataIndex: ['stock_block_trade.trade_date'], key: 'trade_date', width: 110, fixed: 'left' as const, render: (t: string) => dayjs(t).format('YYYY-MM-DD') },
            { title: t('stock_block_trade.stock_code'), dataIndex: ['stock_block_trade.stock_code'], key: 'stock_code', width: 100, fixed: 'left' as const },
            { title: t('stock_block_trade.price'), dataIndex: ['stock_block_trade.price'], key: 'price', width: 90, render: (v: number) => v?.toFixed(2) },
            { title: t('stock_block_trade.volume'), dataIndex: ['stock_block_trade.volume'], key: 'volume', width: 110, render: (v: number) => v?.toFixed(2) },
            { title: t('stock_block_trade.amount'), dataIndex: ['stock_block_trade.amount'], key: 'amount', width: 110, render: (v: number) => v?.toFixed(2) },
            { title: t('stock_block_trade.premium_rate'), dataIndex: ['stock_block_trade.premium_rate'], key: 'premium_rate', width: 90, render: (v: number) => v != null ? `${v.toFixed(2)}%` : '-' },
            { title: t('stock_block_trade.buyer'), dataIndex: ['stock_block_trade.buyer'], key: 'buyer', width: 200, ellipsis: true },
            { title: t('stock_block_trade.seller'), dataIndex: ['stock_block_trade.seller'], key: 'seller', width: 200, ellipsis: true },
            { title: t('stock_block_trade.data_source'), dataIndex: ['stock_block_trade.data_source'], key: 'data_source', width: 100, render: (s: string) => <Tag color="blue">{s}</Tag> },
        ],
        sector_money_flow: [
            { title: t('sector_money_flow.trade_date'), dataIndex: ['sector_money_flow.trade_date'], key: 'trade_date', width: 110, fixed: 'left' as const, render: (t: string) => dayjs(t).format('YYYY-MM-DD') },
            { title: t('sector_money_flow.sector_name'), dataIndex: ['sector_money_flow.sector_name'], key: 'sector_name', width: 150, fixed: 'left' as const },
            { title: t('sector_money_flow.close_price'), dataIndex: ['sector_money_flow.close_price'], key: 'close_price', width: 100, render: (v: number) => v?.toFixed(2) },
            { title: t('sector_money_flow.change_percent'), dataIndex: ['sector_money_flow.change_percent'], key: 'change_percent', width: 100, render: (v: number) => <Text type={v > 0 ? 'danger' : v < 0 ? 'success' : undefined}>{v != null ? v.toFixed(2) + '%' : '-'}</Text> },
            { title: t('sector_money_flow.net_inflow'), dataIndex: ['sector_money_flow.net_inflow'], key: 'net_inflow', width: 130, render: (v: number) => <Text type={v > 0 ? 'danger' : 'success'}>{v ? formatNumber(v) : '-'}</Text> },
            { title: t('sector_money_flow.net_inflow_rate'), dataIndex: ['sector_money_flow.net_inflow_rate'], key: 'net_inflow_rate', width: 110, render: (v: number) => <Text type={v > 0 ? 'danger' : 'success'}>{v != null ? (v * 100).toFixed(2) + '%' : '-'}</Text> },

            { title: t('sector_money_flow.huge_net_inflow'), dataIndex: ['sector_money_flow.huge_net_inflow'], key: 'huge_net_inflow', width: 130, render: (v: number) => <Text type={v > 0 ? 'danger' : 'success'}>{v ? formatNumber(v) : '-'}</Text> },
            { title: t('sector_money_flow.huge_net_inflow_rate'), dataIndex: ['sector_money_flow.huge_net_inflow_rate'], key: 'huge_net_inflow_rate', width: 110, render: (v: number) => <Text type={v > 0 ? 'danger' : 'success'}>{v != null ? v.toFixed(2) + '%' : '-'}</Text> },

            { title: t('sector_money_flow.large_net_inflow'), dataIndex: ['sector_money_flow.large_net_inflow'], key: 'large_net_inflow', width: 130, render: (v: number) => <Text type={v > 0 ? 'danger' : 'success'}>{v ? formatNumber(v) : '-'}</Text> },
            { title: t('sector_money_flow.large_net_inflow_rate'), dataIndex: ['sector_money_flow.large_net_inflow_rate'], key: 'large_net_inflow_rate', width: 110, render: (v: number) => <Text type={v > 0 ? 'danger' : 'success'}>{v != null ? v.toFixed(2) + '%' : '-'}</Text> },

            { title: t('sector_money_flow.medium_net_inflow'), dataIndex: ['sector_money_flow.medium_net_inflow'], key: 'medium_net_inflow', width: 130, render: (v: number) => <Text type={v > 0 ? 'danger' : 'success'}>{v ? formatNumber(v) : '-'}</Text> },
            { title: t('sector_money_flow.medium_net_inflow_rate'), dataIndex: ['sector_money_flow.medium_net_inflow_rate'], key: 'medium_net_inflow_rate', width: 110, render: (v: number) => <Text type={v > 0 ? 'danger' : 'success'}>{v != null ? v.toFixed(2) + '%' : '-'}</Text> },

            { title: t('sector_money_flow.small_net_inflow'), dataIndex: ['sector_money_flow.small_net_inflow'], key: 'small_net_inflow', width: 130, render: (v: number) => <Text type={v > 0 ? 'danger' : 'success'}>{v ? formatNumber(v) : '-'}</Text> },
            { title: t('sector_money_flow.small_net_inflow_rate'), dataIndex: ['sector_money_flow.small_net_inflow_rate'], key: 'small_net_inflow_rate', width: 110, render: (v: number) => <Text type={v > 0 ? 'danger' : 'success'}>{v != null ? v.toFixed(2) + '%' : '-'}</Text> },

            { title: t('sector_money_flow.data_source'), dataIndex: ['sector_money_flow.data_source'], key: 'data_source', width: 100, render: (s: string) => <Tag color="blue">{s}</Tag> },
        ],
        stock_top_holders: [
            { title: t('stock_top_holders.stock_code'), dataIndex: ['stock_top_holders.stock_code'], key: 'stock_code', width: 100, fixed: 'left' as const },
            { title: t('stock_top_holders.report_date'), dataIndex: ['stock_top_holders.report_date'], key: 'report_date', width: 120, fixed: 'left' as const },
            { title: t('stock_top_holders.holder_rank'), dataIndex: ['stock_top_holders.holder_rank'], key: 'holder_rank', width: 80 },
            { title: t('stock_top_holders.holder_name'), dataIndex: ['stock_top_holders.holder_name'], key: 'holder_name', width: 220, ellipsis: true },
            { title: t('stock_top_holders.holder_type'), dataIndex: ['stock_top_holders.holder_type'], key: 'holder_type', width: 150, ellipsis: true },
            {
                title: t('stock_top_holders.hold_amount'),
                dataIndex: ['stock_top_holders.hold_amount'],
                key: 'hold_amount',
                width: 130,
                render: (v: number) => v ? formatNumber(v) : '-'
            },
            {
                title: t('stock_top_holders.hold_ratio'),
                dataIndex: ['stock_top_holders.hold_ratio'],
                key: 'hold_ratio',
                width: 110,
                render: (v: number) => v != null ? v.toFixed(2) + '%' : '-'
            },
            { title: t('stock_top_holders.change'), dataIndex: ['stock_top_holders.change'], key: 'change', width: 120 },
            {
                title: t('stock_top_holders.change_ratio'),
                dataIndex: ['stock_top_holders.change_ratio'],
                key: 'change_ratio',
                width: 130,
                render: (v: number) => v != null ? <Text type={v > 0 ? 'danger' : v < 0 ? 'success' : undefined}>{v.toFixed(2)}%</Text> : '-'
            },
        ],
    };

    return (
        <div style={{ padding: '0px' }}>
            <Space direction="vertical" size="large" style={{ width: '100%' }}>
                <Card variant="borderless" styles={{ body: { padding: '20px' } }}>
                    <Space size="middle" wrap>
                        <AutoComplete
                            options={searchOptions}
                            onSearch={handleSearchStock}
                            onSelect={(val) => {
                                setStockCode(val);
                            }}
                            value={stockCode}
                            onChange={(val) => setStockCode(val)}
                            style={{ width: 300 }}
                            allowClear
                        >
                            <Input
                                placeholder={t('common.filter_by_stock_code_or_name')}
                                prefix={<SearchOutlined />}
                                onPressEnter={handleSearch}
                            />
                        </AutoComplete>
                        <Button type="primary" icon={<SearchOutlined />} onClick={handleSearch}>
                            {t('common.filter')}
                        </Button>
                        <DatePicker.RangePicker
                            value={syncDateRange}
                            onChange={(dates) => setSyncDateRange(dates as [dayjs.Dayjs | null, dayjs.Dayjs | null])}
                            style={{ width: 240 }}
                        />
                        <Tooltip title={t('market.data_manager.sync_warehouse_tip')}>
                            <Button
                                icon={<SyncOutlined spin={syncing} />}
                                onClick={() => handleSync()}
                                loading={syncing}
                            >
                                {t('market.data_manager.sync_warehouse')} {stockCode ? `[${stockCode}]` : ''}
                            </Button>
                        </Tooltip>
                        <Button
                            icon={<SettingOutlined />}
                            onClick={() => setIsDataSourceModalVisible(true)}
                        >
                            {currentDataSource ? t('market.data_manager.current_source', { source: currentDataSource }) : t('market.data_manager.switch_source')}
                        </Button>
                        <Button
                            danger
                            icon={<DeleteOutlined />}
                            onClick={openClearDataModal}
                        >
                            {t('market.data_manager.clear_data')}
                        </Button>
                        {activeTab === 'stocks' && (
                            <Space>
                                <Tooltip title={t('market.data_manager.sync_stock_basic_tip')}>
                                    <Button
                                        type="primary"
                                        icon={<SyncOutlined spin={basicSyncing} />}
                                        onClick={handleStockBasicSync}
                                        loading={basicSyncing}
                                    >
                                        {stockCode ? `${t('market.data_manager.sync_stock_basic')} [${stockCode}]` : t('market.data_manager.sync_stock_basic')}
                                    </Button>
                                </Tooltip>

                                <Checkbox
                                    checked={resumeSync}
                                    onChange={e => setResumeSync(e.target.checked)}
                                >
                                    {t('market.data_manager.resume_sync')}
                                </Checkbox>
                                <Tooltip title={t('common.sync_base_info_tip')}>
                                    <Button
                                        type="primary"
                                        style={{ backgroundColor: '#722ed1', borderColor: '#722ed1' }}
                                        icon={<SyncOutlined spin={baseInfoSyncing} />}
                                        onClick={() => handleBaseInfoSync('all')}
                                        loading={baseInfoSyncing}
                                    >
                                        {t('common.sync_base_info')} {stockCode ? `[${stockCode}]` : ''}
                                    </Button>
                                </Tooltip>
                                <Tooltip title={t('common.sync_warehouse_base_info_tip')}>
                                    <Button
                                        type="primary"
                                        style={{ backgroundColor: '#eb2f96', borderColor: '#eb2f96' }}
                                        icon={<SyncOutlined spin={baseInfoSyncing} />}
                                        onClick={() => handleBaseInfoSync('warehouse')}
                                        loading={baseInfoSyncing}
                                    >
                                        {t('common.sync_warehouse_base_info')} {stockCode ? `[${stockCode}]` : ''}
                                    </Button>
                                </Tooltip>
                                <Tooltip title={t('common.sync_core_base_info_tip')}>
                                    <Button
                                        type="primary"
                                        style={{ backgroundColor: '#fa8c16', borderColor: '#fa8c16' }}
                                        icon={<SyncOutlined spin={baseInfoSyncing} />}
                                        onClick={() => handleBaseInfoSync('core')}
                                        loading={baseInfoSyncing}
                                    >
                                        {t('common.sync_core_base_info')} {stockCode ? `[${stockCode}]` : ''}
                                    </Button>
                                </Tooltip>
                                <Button
                                    type="primary"
                                    icon={<DatabaseOutlined />}
                                    onClick={() => {
                                        // Load available tables from dbTables or a predefined list
                                        if (dbTables.length === 0) {
                                            fetchDbTables();
                                        }
                                        setIsBulkSyncModalVisible(true);
                                    }}
                                >
                                    {t('market.data_manager.bulk_sync')}
                                </Button>
                            </Space>
                        )}
                        {activeTab === 'valuation' && (
                            <Button
                                type="primary"
                                icon={<SyncOutlined spin={valuationSyncing} />}
                                onClick={handleValuationSync}
                                loading={valuationSyncing}
                            >
                                {stockCode ? `${t('market.data_manager.sync_valuation')} [${stockCode}]` : t('market.data_manager.sync_valuation')}
                            </Button>
                        )}
                        {activeTab === 'kline' && (
                            <Button
                                type="primary"
                                icon={<SyncOutlined spin={dailySyncing} />}
                                onClick={() => setIsDailySyncModalVisible(true)}
                                loading={dailySyncing}
                            >
                                {t('market.data_manager.sync_daily')}
                            </Button>
                        )}
                        {activeTab === 'index_daily' && (
                            <Button
                                type="primary"
                                icon={<SyncOutlined spin={indexDailySyncing} />}
                                onClick={() => setIsIndexDailySyncModalVisible(true)}
                                loading={indexDailySyncing}
                            >
                                {t('market.data_manager.sync_index_daily')}
                            </Button>
                        )}
                        {activeTab === 'stock_indicators' && (
                            <Button
                                type="primary"
                                icon={<SyncOutlined spin={indicatorsSyncing} />}
                                onClick={handleCalculateIndicators}
                                loading={indicatorsSyncing}
                            >
                                {t('tasks.names.calculate_indicators').replace('{info}', stockCode || 'All')}
                            </Button>
                        )}

                        {activeTab === 'dragontiger' && (
                            <>
                                <DatePicker.RangePicker
                                    value={dragonTigerDateRange}
                                    onChange={(dates) => setDragonTigerDateRange(dates as any)}
                                    format="YYYY-MM-DD"
                                    style={{ width: 250 }}
                                    disabledDate={(current) => current && current > dayjs().endOf('day')}
                                />
                                <Button
                                    type="primary"
                                    icon={<FireOutlined />}
                                    loading={dragonTigerSyncing}
                                    onClick={handleDragonTigerSync}
                                >
                                    {t('data_manager.sync_dragon_tiger')} [{dragonTigerDateRange[0]?.format('YYYY-MM-DD')} - {dragonTigerDateRange[1]?.format('YYYY-MM-DD')}]
                                </Button>
                            </>
                        )}
                        {activeTab === 'northbound' && (
                            <Button
                                type="primary"
                                icon={<SyncOutlined spin={northboundSyncing} />}
                                onClick={handleNorthboundSync}
                                loading={northboundSyncing}
                            >
                                {stockCode ? `${t('market.data_manager.sync_northbound')} [${stockCode}]` : t('market.data_manager.sync_northbound')}
                            </Button>
                        )}
                        {activeTab === 'realtime' && (
                            <Button
                                type="primary"
                                icon={<SyncOutlined spin={realtimeSyncing} />}
                                onClick={handleRealtimeSync}
                                loading={realtimeSyncing}
                            >
                                {t('data_manager.sync_realtime')} {stockCode ? `[${stockCode}]` : ''}
                            </Button>
                        )}
                        {activeTab === 'industry' && (
                            <Button
                                type="primary"
                                icon={<SyncOutlined spin={industrySyncing} />}
                                onClick={handleIndustrySync}
                                loading={industrySyncing}
                            >
                                {t('industry_data.sync_button')}
                            </Button>
                        )}
                        {activeTab === 'sector_money_flow' && (
                            <Button
                                type="primary"
                                icon={<SyncOutlined spin={sectorMoneyFlowSyncing} />}
                                onClick={handleSectorMoneyFlowSync}
                                loading={sectorMoneyFlowSyncing}
                            >
                                {t('sector_money_flow.sync_button')}
                            </Button>
                        )}
                        {activeTab === 'stock_limit_up_pool' && (
                            <>
                                <DatePicker
                                    value={limitUpDate}
                                    onChange={(date) => setLimitUpDate(date)}
                                    format="YYYY-MM-DD"
                                    style={{ width: 150 }}
                                    placeholder="Select Date"
                                    disabledDate={(current) => current && current > dayjs().endOf('day')}
                                />
                                <Button
                                    type="primary"
                                    icon={<SyncOutlined spin={limitUpSyncing} />}
                                    onClick={handleLimitUpSync}
                                    loading={limitUpSyncing}
                                >
                                    {t('market.data_manager.sync_limit_up_pool', { date: limitUpDate ? limitUpDate.format('YYYY-MM-DD') : '' })}
                                </Button>
                            </>

                        )}
                        {activeTab === 'stock_limit_down_pool' && (
                            <>
                                <DatePicker
                                    value={limitDownDate}
                                    onChange={(date) => setLimitDownDate(date)}
                                    format="YYYY-MM-DD"
                                    style={{ width: 150 }}
                                    placeholder="Select Date"
                                    disabledDate={(current) => current && current > dayjs().endOf('day')}
                                />
                                <Button
                                    type="primary"
                                    icon={<SyncOutlined spin={limitDownSyncing} />}
                                    onClick={handleLimitDownSync}
                                    loading={limitDownSyncing}
                                >
                                    {t('market.data_manager.sync_limit_down_pool', { date: limitDownDate ? limitDownDate.format('YYYY-MM-DD') : '' })}
                                </Button>
                            </>
                        )}
                        {activeTab === 'stock_zhaban_pool' && (
                            <>
                                <DatePicker
                                    value={zhabanDate}
                                    onChange={(date) => setZhabanDate(date)}
                                    format="YYYY-MM-DD"
                                    style={{ width: 150 }}
                                    placeholder="Select Date"
                                    disabledDate={(current) => current && current > dayjs().endOf('day')}
                                />
                                <Button
                                    type="primary"
                                    icon={<SyncOutlined spin={zhabanSyncing} />}
                                    onClick={handleZhabanSync}
                                    loading={zhabanSyncing}
                                >
                                    {t('market.data_manager.sync_zhaban_pool', { date: zhabanDate ? zhabanDate.format('YYYY-MM-DD') : '' })}
                                </Button>
                            </>
                        )}
                        {activeTab === 'stock_money_flow' && (
                            <Tooltip title={!stockCode ? t('common.please_select_stock') : t('market.data_manager.sync_money_flow')}>
                                <Button
                                    type="primary"
                                    icon={<SyncOutlined spin={moneyFlowSyncing} />}
                                    onClick={handleMoneyFlowSync}
                                    loading={moneyFlowSyncing}
                                    disabled={!stockCode}
                                >
                                    {t('market.data_manager.sync_money_flow')} {stockCode ? `[${stockCode}]` : ''}
                                </Button>
                            </Tooltip>
                        )}
                        {activeTab === 'stock_shareholder_count' && (
                            <Tooltip title={!stockCode ? t('common.please_select_stock') : t('market.data_manager.sync_shareholders')}>
                                <Button
                                    type="primary"
                                    icon={<SyncOutlined spin={shareholderSyncing} />}
                                    onClick={() => handleGranularSync('Shareholders', setShareholderSyncing, 'shareholders')}
                                    loading={shareholderSyncing}
                                    disabled={!stockCode}
                                >
                                    {t('market.data_manager.sync_shareholders')} {stockCode ? `[${stockCode}]` : ''}
                                </Button>
                            </Tooltip>
                        )}
                        {activeTab === 'stock_pledge_risk' && (
                            <Tooltip title={!stockCode ? t('common.please_select_stock') : t('market.data_manager.sync_pledge')}>
                                <Button
                                    type="primary"
                                    icon={<SyncOutlined spin={pledgeSyncing} />}
                                    onClick={() => handleGranularSync('Pledge Risk', setPledgeSyncing, 'pledge')}
                                    loading={pledgeSyncing}
                                    disabled={!stockCode}
                                >
                                    {t('market.data_manager.sync_pledge')} {stockCode ? `[${stockCode}]` : ''}
                                </Button>
                            </Tooltip>
                        )}
                        {activeTab === 'stock_pledge_summary' && (
                            <Tooltip title={t('market.data_manager.sync_pledge_summary')}>
                                <Button
                                    type="primary"
                                    icon={<SyncOutlined spin={pledgeSummarySyncing} />}
                                    onClick={handlePledgeSummarySync}
                                    loading={pledgeSummarySyncing}
                                >
                                    {t('market.data_manager.sync_pledge_summary')} {stockCode ? `[${stockCode}]` : ''}
                                </Button>
                            </Tooltip>
                        )}
                        {activeTab === 'stock_insider_trading' && (
                            <Tooltip title={!stockCode ? t('common.please_select_stock') : t('common.sync_insider')}>
                                <Button
                                    type="primary"
                                    icon={<SyncOutlined spin={insiderSyncing} />}
                                    onClick={() => handleGranularSync('Insider Trading', setInsiderSyncing, 'insider')}
                                    loading={insiderSyncing}
                                    disabled={!stockCode}
                                >
                                    {t('common.sync_insider')} {stockCode ? `[${stockCode}]` : ''}
                                </Button>
                            </Tooltip>
                        )}
                        {activeTab === 'stock_lockup_release' && (
                            <Tooltip title={!stockCode ? t('common.please_select_stock') : t('common.sync_lockup')}>
                                <Button
                                    type="primary"
                                    icon={<SyncOutlined spin={lockupSyncing} />}
                                    onClick={() => handleGranularSync('Lockup Release', setLockupSyncing, 'lockup')}
                                    loading={lockupSyncing}
                                    disabled={!stockCode}
                                >
                                    {t('common.sync_lockup')} {stockCode ? `[${stockCode}]` : ''}
                                </Button>
                            </Tooltip>
                        )}
                        {activeTab === 'stock_margin_data' && (
                            <Tooltip title={!stockCode ? t('common.please_select_stock') : t('common.sync_margin')}>
                                <Button
                                    type="primary"
                                    icon={<SyncOutlined spin={marginSyncing} />}
                                    onClick={() => handleGranularSync('Margin Data', setMarginSyncing, 'margin')}
                                    loading={marginSyncing}
                                    disabled={!stockCode}
                                >
                                    {t('common.sync_margin')} {stockCode ? `[${stockCode}]` : ''}
                                </Button>
                            </Tooltip>
                        )}
                        {activeTab === 'stock_block_trade' && (
                            <Space>
                                <DatePicker.RangePicker
                                    value={blockTradeDateRange}
                                    onChange={(dates) => setBlockTradeDateRange(dates as [dayjs.Dayjs | null, dayjs.Dayjs | null])}
                                    style={{ width: 260 }}
                                />
                                <Tooltip title={t('common.sync_block_trade')}>
                                    <Button
                                        type="primary"
                                        icon={<SyncOutlined spin={blockTradeSyncing} />}
                                        onClick={() => handleGranularSync('Block Trade', setBlockTradeSyncing, 'block_trade')}
                                        loading={blockTradeSyncing}
                                    >
                                        {t('common.stock_block_trade')}
                                    </Button>
                                </Tooltip>
                            </Space>
                        )}
                        {activeTab === 'stock_interactive_qa' && (
                            <Tooltip title={!stockCode ? t('common.please_select_stock') : t('market.data_manager.sync_interactive_qa')}>
                                <Button
                                    type="primary"
                                    icon={<SyncOutlined spin={interactiveQASyncing} />}
                                    onClick={handleInteractiveQASync}
                                    loading={interactiveQASyncing}
                                    disabled={!stockCode}
                                >
                                    {t('market.data_manager.sync_interactive_qa')} {stockCode ? `[${stockCode}]` : ''}
                                </Button>
                            </Tooltip>
                        )}
                        {activeTab === 'stock_top_holders' && (
                            <Tooltip title={!stockCode ? t('common.please_select_stock') : t('market.data_manager.sync_top_holders')}>
                                <Button
                                    type="primary"
                                    icon={<SyncOutlined spin={topHoldersSyncing} />}
                                    onClick={handleTopHoldersSync}
                                    loading={topHoldersSyncing}
                                    disabled={!stockCode}
                                >
                                    {t('market.data_manager.sync_top_holders')} {stockCode ? `[${stockCode}]` : ''}
                                </Button>
                            </Tooltip>
                        )}
                        {stockCode && (
                            <Text type="secondary">Showing data for {stockCode}</Text>
                        )}
                    </Space>
                </Card>

                <Card variant="borderless">
                    <Tabs
                        activeKey={activeTab}
                        onChange={(key) => {
                            setActiveTab(key);
                            setData({ total: 0, items: [] }); // Clear data immediately to avoid dynamic column logic using old data
                            setPagination({ current: 1, pageSize: pagination.pageSize });
                        }}
                        type="line"
                        items={[
                            {
                                key: 'stocks',
                                label: <span><DatabaseOutlined />{t('market.data_manager.stock_basics')}</span>,
                            },
                            {
                                key: 'kline',
                                label: <span><LineChartOutlined />{t('market.data_manager.daily_kline')}</span>,
                            },
                            {
                                key: 'index_daily',
                                label: <span><LineChartOutlined />{t('market.data_manager.index_daily')}</span>,
                            },
                            {
                                key: 'stock_indicators',
                                label: <span><LineChartOutlined />{t('common.technical_indicators')}</span>,
                            },

                            {
                                key: 'realtime',
                                label: <span><SyncOutlined />{t('market.data_manager.realtime')}</span>,
                            },
                            {
                                key: 'valuation',
                                label: <span><FundViewOutlined />{t('market.valuation_metrics')}</span>,
                            },
                            {
                                key: 'industry',
                                label: <span><DatabaseOutlined />{t('market.data_manager.industry')}</span>,
                            },
                            {
                                key: 'stock_interactive_qa',
                                label: <span><QuestionCircleOutlined />{t('market.data_manager.stock_interactive_qa')}</span>,
                            },
                            {
                                key: 'northbound',
                                label: <span><TransactionOutlined />{t('market.data_manager.northbound')}</span>,
                            },
                            {
                                key: 'dragontiger',
                                label: <span><FireOutlined />{t('market.data_manager.dragon_tiger')}</span>,
                            },
                            {
                                key: 'stock_limit_up_pool',
                                label: <span><FireOutlined />{t('market.data_manager.stock_limit_pool')}</span>,
                            },
                            {
                                key: 'stock_limit_down_pool',
                                label: <span><FireOutlined rotate={180} />{t('market.data_manager.stock_limit_down_pool')}</span>,
                            },
                            {
                                key: 'stock_zhaban_pool',
                                label: <span><FireOutlined style={{ color: '#faad14' }} />{t('market.data_manager.stock_zhaban_pool')}</span>,
                            },
                            {
                                key: 'stock_money_flow',
                                label: <span><DollarOutlined />{t('market.data_manager.stock_money_flow')}</span>,
                            },
                            {
                                key: 'stock_shareholder_count',
                                label: <span><TransactionOutlined />{t('market.data_manager.stock_shareholder_count')}</span>,
                            },
                            {
                                key: 'stock_pledge_risk',
                                label: <span><ExclamationCircleOutlined />{t('market.data_manager.stock_pledge_risk')}</span>,
                            },
                            {
                                key: 'stock_pledge_summary',
                                label: <span><ExclamationCircleOutlined />{t('market.data_manager.stock_pledge_summary')}</span>,
                            },
                            {
                                key: 'stock_insider_trading',
                                label: <span><UserOutlined />{t('market.data_manager.stock_insider_trading')}</span>,
                            },
                            {
                                key: 'stock_lockup_release',
                                label: <span><ReadOutlined />{t('market.data_manager.stock_lockup_release')}</span>,
                            },
                            {
                                key: 'stock_margin_data',
                                label: <span><LineChartOutlined />{t('market.data_manager.stock_margin_data')}</span>,
                            },
                            {
                                key: 'stock_block_trade',
                                label: <span><TransactionOutlined />{t('common.stock_block_trade')}</span>,
                            },
                            {
                                key: 'sector_money_flow',
                                label: <span><FundOutlined />{t('common.sector_money_flow')}</span>,
                            },
                            {
                                key: 'stock_top_holders',
                                label: <span><UserOutlined />{t('market.data_manager.stock_top_holders')}</span>,
                            },
                        ]}

                    />

                    <Table
                        dataSource={data.items}
                        columns={columnsMap[activeTab] || []}
                        rowKey={(record) => record['stock_interactive_qa.id'] || record['stock_zhaban_pool.id'] || record['stock_limit_up_pool.id'] || record['stock_limit_down_pool.id'] || record['stock_basic.stock_code'] || record['kline_data.date'] || record.id || `${record['stock_zhaban_pool.stock_code'] || record.stock_code}-${record['stock_zhaban_pool.update_date'] || record.date || record.report_date || record.trade_date || record.publish_date || Math.random()}`}
                        loading={loading}
                        pagination={{
                            current: pagination.current,
                            pageSize: pagination.pageSize,
                            total: data.total,
                            showSizeChanger: true,
                            pageSizeOptions: ['10', '20', '50', '100'],
                            onChange: (page, pageSize) => setPagination({ current: page, pageSize }),
                        }}
                        size="middle"
                        scroll={{
                            x: ['stock_interactive_qa', 'stock_zhaban_pool'].includes(activeTab) && data.items.length > 0 ?
                                'max-content' :
                                1200
                        }}
                    />
                </Card>
            </Space >


            <Modal
                title={modalTitle || t('common.view')}
                open={isNewsModalVisible}
                onOk={() => setIsNewsModalVisible(false)}
                onCancel={() => setIsNewsModalVisible(false)}
                width={900}
                footer={null}
            >
                <div style={{ maxHeight: '70vh', overflow: 'auto', padding: '15px' }}>
                    {newsDetail ? (
                        <ReactMarkdown>{newsDetail}</ReactMarkdown>
                    ) : (
                        <Text type="secondary">{t('market.data_manager.no_content_available')}</Text>
                    )}
                </div>
            </Modal>

            {/* Data Source Switch Modal */}
            <Modal
                title={t('market.data_manager.switch_data_source')}
                open={isDataSourceModalVisible}
                onCancel={() => setIsDataSourceModalVisible(false)}
                cancelText={t('common.cancel')}
                footer={null}
            >
                <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                    <Text>{t('market.data_manager.current_data_source')}: <Tag color="blue">{currentDataSource}</Tag></Text>
                    <Text type="secondary">{t('market.data_manager.select_new_source_tip')}</Text>

                    <Radio.Group
                        onChange={(e) => handleSwitchDataSource(e.target.value)}
                        value={currentDataSource}
                    >
                        <Space direction="vertical">
                            {dataSourceList && dataSourceList.sources && dataSourceList.sources.map((source: string) => (
                                <Radio value={source} key={source}>
                                    {source.toUpperCase()}
                                    {dataSourceList.default_source === source && <Tag color="green" style={{ marginLeft: 8 }}>{t('market.data_manager.current_default')}</Tag>}
                                </Radio>
                            ))}
                        </Space>
                    </Radio.Group>
                </div>
            </Modal>

            {/* Daily Data Sync Modal */}
            <Modal
                title={t('market.data_manager.sync_daily_kline_title')}
                open={isDailySyncModalVisible}
                onOk={handleDailySync}
                onCancel={() => setIsDailySyncModalVisible(false)}
                okText={t('common.sync')}
                cancelText={t('common.cancel')}
                confirmLoading={dailySyncing}
            >
                <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                    <div>
                        <Text strong style={{ display: 'block', marginBottom: '8px' }}>{t('market.data_manager.stock_code')}</Text>
                        <Input
                            value={stockCode}
                            onChange={(e) => setStockCode(e.target.value.toUpperCase())}
                            placeholder={t('common.input_stock_placeholder')}
                        />
                    </div>

                    <div>
                        <Text strong style={{ display: 'block', marginBottom: '8px' }}>{t('market.data_manager.date_range')}</Text>
                        <DatePicker.RangePicker
                            value={dailySyncDateRange}
                            onChange={(dates) => setDailySyncDateRange(dates as any)}
                            format="YYYY-MM-DD"
                            style={{ width: '100%' }}
                            disabledDate={(current) => current && current > dayjs().endOf('day')}
                        />
                    </div>

                    <div>
                        <Text strong style={{ display: 'block', marginBottom: '8px' }}>{t('market.data_manager.adjustment')}</Text>
                        <Select
                            value={dailySyncAdjust}
                            onChange={setDailySyncAdjust}
                            style={{ width: '100%' }}
                            options={[
                                { value: 'qfq', label: t('market.data_manager.adjustment_qfq') },
                                { value: 'hfq', label: t('market.data_manager.adjustment_hfq') },
                                { value: 'None', label: t('market.data_manager.adjustment_none') },
                            ]}
                        />
                    </div>
                </div>
            </Modal>

            {/* Index Daily Sync Modal */}
            <Modal
                title={t('market.data_manager.sync_index_daily_title')}
                open={isIndexDailySyncModalVisible}
                onOk={handleIndexDailySync}
                onCancel={() => setIsIndexDailySyncModalVisible(false)}
                okText={t('common.sync')}
                cancelText={t('common.cancel')}
                confirmLoading={indexDailySyncing}
            >
                <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                    <div>
                        <Text strong style={{ display: 'block', marginBottom: '8px' }}>{t('index_daily.index_code')}</Text>
                        <Input
                            value={indexCode}
                            onChange={(e) => setIndexCode(e.target.value.toUpperCase())}
                            placeholder={t('market.data_manager.index_code_placeholder', { default: 'e.g. 000001' })}
                        />
                    </div>

                    <div>
                        <Text strong style={{ display: 'block', marginBottom: '8px' }}>{t('market.data_manager.date_range')}</Text>
                        <DatePicker.RangePicker
                            value={indexDailySyncDateRange}
                            onChange={(dates) => setIndexDailySyncDateRange(dates as any)}
                            format="YYYY-MM-DD"
                            style={{ width: '100%' }}
                            disabledDate={(current) => current && current > dayjs().endOf('day')}
                        />
                    </div>
                </div>
            </Modal>


            {/* Clear Data Confirmation Modal */}
            <Modal
                title={<Space><ExclamationCircleOutlined style={{ color: 'red' }} /> {t('market.data_manager.clear_data')}</Space>}
                open={isClearDataModalVisible}
                onOk={handleClearData}
                onCancel={() => setIsClearDataModalVisible(false)}
                okText={t('market.data_manager.confirm_clear')}
                cancelText={t('common.cancel')}
                okButtonProps={{ danger: true, disabled: (clearConfirmationText.trim().toLowerCase() !== 'confirm' && clearConfirmationText.trim() !== '确认') }}
                confirmLoading={clearingTable}
            >
                <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                    <div style={{ padding: '10px', background: '#fff2f0', border: '1px solid #ffccc7', borderRadius: '4px' }}>
                        <Text type="danger" strong>{t('market.data_manager.warning_undone')}</Text>
                        <br />
                        <Text>{t('market.data_manager.warning_delete')}</Text>
                    </div>

                    <div>
                        <Text strong style={{ display: 'block', marginBottom: '8px' }}>{t('market.data_manager.select_table_to_clear')}</Text>
                        <Select
                            showSearch
                            style={{ width: '100%' }}
                            value={selectedTableToClear}
                            onChange={setSelectedTableToClear}
                            filterOption={(input, option) =>
                                (option?.label ?? '').toLowerCase().includes(input.toLowerCase())
                            }
                            options={[
                                { value: 'all', label: t('market.data_manager.all_tables_dangerous') },
                                ...(dbTables || []).map(t => ({ value: t, label: t }))
                            ]}
                        />
                    </div>

                    <div>
                        <Text strong style={{ display: 'block', marginBottom: '8px' }}>
                            {t('market.data_manager.type_confirm_to_verify')}:
                        </Text>
                        <Input
                            value={clearConfirmationText}
                            onChange={(e) => setClearConfirmationText(e.target.value)}
                            placeholder={t('common.confirm')}
                            status={(clearConfirmationText && clearConfirmationText.trim().toLowerCase() !== 'confirm' && clearConfirmationText.trim() !== '确认') ? 'error' : ''}
                        />
                    </div>
                </div>
            </Modal>

            {/* Bulk Sync Modal */}
            <Modal
                title={t('market.data_manager.bulk_sync')}
                open={isBulkSyncModalVisible}
                onOk={handleBulkSyncSubmit}
                onCancel={() => {
                    setIsBulkSyncModalVisible(false);
                    setSelectedBulkTables([]);
                    setBulkSyncStockCodes('');
                }}
                confirmLoading={bulkSyncing}
                okText={t('common.confirm')}
                cancelText={t('common.cancel')}
                width={800}
            >
                <div style={{ marginBottom: 16 }}>
                    <Text type="secondary">{t('market.data_manager.bulk_sync_desc')}</Text>
                </div>
                <div style={{ marginBottom: 24, padding: '16px', background: 'var(--app-bg-muted)', borderRadius: '4px' }}>
                    <Text strong style={{ display: 'block', marginBottom: 8 }}>{t('market.data_manager.date_range')}</Text>
                    <DatePicker.RangePicker
                        style={{ width: '100%' }}
                        value={bulkSyncDateRange}
                        onChange={(dates) => setBulkSyncDateRange(dates as any)}
                        format="YYYY-MM-DD"
                    />
                </div>
                <div style={{ marginBottom: 24, padding: '16px', background: 'var(--app-bg-muted)', borderRadius: '4px' }}>
                    <Text strong style={{ display: 'block', marginBottom: 8 }}>{t('market.data_manager.stock_code')}</Text>
                    <Input
                        placeholder={t('market.data_manager.stock_code_placeholder')}
                        value={bulkSyncStockCodes}
                        onChange={(e) => setBulkSyncStockCodes(e.target.value)}
                    />
                </div>
                <div style={{ marginBottom: 24, padding: '16px', background: 'var(--app-bg-muted)', borderRadius: '4px' }}>
                    <Space style={{ marginBottom: 8 }}>
                        <Text strong>{t('market.data_manager.stock_scope')}</Text>
                        <Tooltip title={t('market.data_manager.stock_scope_tooltip')}>
                            <QuestionCircleOutlined style={{ fontSize: 13, color: '#888' }} />
                        </Tooltip>
                    </Space>
                    <Select
                        style={{ width: '100%' }}
                        value={bulkSyncStockScope}
                        onChange={(val) => setBulkSyncStockScope(val)}
                        disabled={!!bulkSyncStockCodes.trim()}
                        options={[
                            { value: 'warehouse', label: t('market.data_manager.stock_scope_warehouse') },
                            { value: 'all', label: t('market.data_manager.stock_scope_all') },
                        ]}
                    />
                </div>
                <div style={{ maxHeight: '400px', overflowY: 'auto' }}>
                    <Checkbox.Group
                        style={{ width: '100%' }}
                        value={selectedBulkTables}
                        onChange={(checkedValues) => setSelectedBulkTables(checkedValues as string[])}
                    >
                        {/* We use predefined functional categories mapped to the backend TABLE_METHOD_MAPPING keys */}
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '16px' }}>
                            <Card size="small" title={t('common.fundamentals')}>
                                <Space direction="vertical">
                                    <Checkbox value="stocks">{t('market.data_manager.stock_basics')}</Checkbox>
                                    <Checkbox value="kline">{t('market.data_manager.daily_kline')}</Checkbox>
                                    <Checkbox value="index_daily">{t('market.data_manager.index_daily')}</Checkbox>
                                </Space>
                            </Card>
                            <Card size="small" title={t('market.data_manager.financial_data')}>
                                <Space direction="vertical">
                                    <Checkbox value="valuation">{t('market.valuation_metrics')}</Checkbox>
                                </Space>
                            </Card>
                            <Card size="small" title={t('common.realtime_quote')}>
                                <Space direction="vertical">
                                    <Checkbox value="realtime">{t('market.data_manager.realtime')}</Checkbox>
                                    <Checkbox value="stock_interactive_qa">{t('market.data_manager.stock_interactive_qa')}</Checkbox>
                                </Space>
                            </Card>
                            <Card size="small" title={t('common.money_flow')}>
                                <Space direction="vertical">
                                    <Checkbox value="northbound">{t('market.data_manager.northbound')}</Checkbox>
                                    <Checkbox value="dragontiger">{t('market.data_manager.dragon_tiger')}</Checkbox>
                                    <Checkbox value="stock_money_flow">{t('market.data_manager.stock_money_flow')}</Checkbox>
                                    <Checkbox value="sector_money_flow">{t('common.sector_money_flow')}</Checkbox>
                                    <Checkbox value="stock_block_trade">{t('common.stock_block_trade')}</Checkbox>
                                    <Checkbox value="stock_margin_data">{t('market.data_manager.stock_margin_data')}</Checkbox>
                                </Space>
                            </Card>
                            <Card size="small" title={t('market.data_manager.stock_limit_pool')}>
                                <Space direction="vertical">
                                    <Checkbox value="stock_limit_up_pool">{t('market.data_manager.stock_limit_pool')}</Checkbox>
                                    <Checkbox value="stock_limit_down_pool">{t('market.data_manager.stock_limit_down_pool')}</Checkbox>
                                    <Checkbox value="stock_zhaban_pool">{t('market.data_manager.stock_zhaban_pool')}</Checkbox>
                                    <Checkbox value="stock_insider_trading">{t('market.data_manager.stock_insider_trading')}</Checkbox>
                                </Space>
                            </Card>
                            <Card size="small" title={t('common.shareholder_count')}>
                                <Space direction="vertical">
                                    <Checkbox value="stock_shareholder_count">{t('market.data_manager.stock_shareholder_count')}</Checkbox>
                                    <Checkbox value="stock_top_holders">{t('market.data_manager.stock_top_holders')}</Checkbox>
                                    <Checkbox value="stock_pledge_risk">{t('market.data_manager.stock_pledge_risk')}</Checkbox>
                                    <Checkbox value="stock_pledge_summary">{t('market.data_manager.stock_pledge_summary')}</Checkbox>
                                    <Checkbox value="stock_lockup_release">{t('market.data_manager.stock_lockup_release')}</Checkbox>
                                </Space>
                            </Card>
                            <Card size="small" title={t('market.data_manager.industry')}>
                                <Space direction="vertical">
                                    <Checkbox value="industry">{t('market.data_manager.industry')}</Checkbox>
                                </Space>
                            </Card>
                        </div>
                    </Checkbox.Group>
                </div>
            </Modal>
        </div>
    );
};

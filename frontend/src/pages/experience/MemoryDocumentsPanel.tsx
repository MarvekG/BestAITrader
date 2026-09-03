import React from 'react';
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  List,
  Segmented,
  Space,
  Tag,
  Typography,
} from 'antd';
import { ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import { useTranslation } from 'react-i18next';

import { memoryDocumentsApi, MemoryDocumentDetail, MemoryDocumentListParams, MemoryDocumentSummary } from '../../api/memoryDocuments';
import { DebateMarkdown } from '../../features/brain/DebateMarkdown';
import { formatErrorMessage, getApiErrorDetail } from '../../utils/errorUtils';
import { useFeedback } from '../../hooks/useFeedback';

const { Paragraph, Text } = Typography;

type ViewMode = 'rendered' | 'source';

interface SearchValues {
  keyword?: string;
}

const formatDateTime = (value: string) => dayjs(value).format('YYYY-MM-DD HH:mm');

export const MemoryDocumentsPanel: React.FC = () => {
  const { t } = useTranslation();
  const message = useFeedback();
  const [form] = Form.useForm<SearchValues>();
  const [loading, setLoading] = React.useState(false);
  const [items, setItems] = React.useState<MemoryDocumentSummary[]>([]);
  const [total, setTotal] = React.useState(0);
  const [page, setPage] = React.useState(1);
  const [pageSize, setPageSize] = React.useState(20);
  const [maxChars, setMaxChars] = React.useState(0);
  const [activeStockCode, setActiveStockCode] = React.useState<string | null>(null);
  const [detail, setDetail] = React.useState<MemoryDocumentDetail | null>(null);
  const [detailLoading, setDetailLoading] = React.useState(false);
  const [viewMode, setViewMode] = React.useState<ViewMode>('rendered');

  const loadDocuments = React.useCallback(async (
    nextPage: number,
    nextPageSize: number,
    params?: MemoryDocumentListParams,
  ) => {
    setLoading(true);
    try {
      const data = await memoryDocumentsApi.list({
        ...params,
        page: nextPage,
        page_size: nextPageSize,
      });
      setItems(data.items || []);
      setTotal(data.total || 0);
      setPage(data.page || nextPage);
      setPageSize(data.page_size || nextPageSize);
      setMaxChars(data.max_chars || 0);
    } catch (error) {
      message.error(formatErrorMessage(getApiErrorDetail(error)) || t('common.error'));
    } finally {
      setLoading(false);
    }
  }, [message, t]);

  const loadCurrentPage = React.useCallback(async (nextPage: number, nextPageSize: number) => {
    const values = form.getFieldsValue();
    await loadDocuments(nextPage, nextPageSize, values);
  }, [form, loadDocuments]);

  React.useEffect(() => {
    void loadCurrentPage(1, pageSize);
  }, [loadCurrentPage, pageSize]);

  const handleInspect = async (stockCode: string) => {
    setActiveStockCode(stockCode);
    setDetail(null);
    setDetailLoading(true);
    setViewMode('rendered');
    try {
      const data = await memoryDocumentsApi.get(stockCode);
      setDetail(data);
    } catch (error) {
      setActiveStockCode(null);
      message.error(formatErrorMessage(getApiErrorDetail(error)) || t('common.error'));
    } finally {
      setDetailLoading(false);
    }
  };

  const handleCloseDetail = () => {
    setActiveStockCode(null);
    setDetail(null);
  };

  const handleSearch = (values: SearchValues) => {
    void loadDocuments(1, pageSize, values);
  };

  const handleReset = () => {
    form.resetFields();
    void loadDocuments(1, pageSize, {});
  };

  const handleRefreshDetail = () => {
    if (activeStockCode) {
      void handleInspect(activeStockCode);
    }
  };

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card>
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Alert type="info" showIcon message={t('memory_documents.source_notice')} />
          <Form<SearchValues> form={form} layout="inline" onFinish={handleSearch}>
            <Form.Item name="keyword">
              <Input allowClear placeholder={t('memory_documents.keyword_placeholder')} style={{ width: 260 }} />
            </Form.Item>
            <Form.Item>
              <Space>
                <Button type="primary" icon={<SearchOutlined />} htmlType="submit">
                  {t('memory_documents.search')}
                </Button>
                <Button onClick={handleReset}>{t('memory_documents.reset')}</Button>
                <Button
                  icon={<ReloadOutlined />}
                  loading={loading}
                  onClick={() => void loadCurrentPage(page, pageSize)}
                >
                  {t('memory_documents.refresh')}
                </Button>
              </Space>
            </Form.Item>
          </Form>
        </Space>
      </Card>

      <Card loading={loading}>
        {items.length ? (
          <List
            dataSource={items}
            pagination={{
              current: page,
              pageSize,
              total,
              showSizeChanger: true,
              onChange: (nextPage, nextPageSize) => (
                void loadCurrentPage(nextPage, nextPageSize || pageSize)
              ),
            }}
            renderItem={(item) => (
              <List.Item
                actions={[
                  <Button key="view" type="link" onClick={() => void handleInspect(item.stock_code)}>
                    {t('memory_documents.view_document')}
                  </Button>,
                ]}
              >
                <List.Item.Meta
                  title={(
                    <Space wrap>
                      <Text strong>{item.stock_code}</Text>
                      {item.stock_name ? <Tag>{item.stock_name}</Tag> : null}
                      <Tag color="blue">{t('memory_documents.version', { version: item.version })}</Tag>
                    </Space>
                  )}
                  description={(
                    <Space wrap>
                      <Text type="secondary">
                        {t('memory_documents.size_chars', { size: item.size_chars, max: maxChars })}
                      </Text>
                      <Text type="secondary">
                        {t('memory_documents.updated_at', { time: formatDateTime(item.updated_at) })}
                      </Text>
                    </Space>
                  )}
                />
              </List.Item>
            )}
          />
        ) : (
          <Empty description={t('memory_documents.empty')} />
        )}
      </Card>

      <Drawer
        title={detail ? `${detail.stock_code} - ${detail.stock_name || detail.stock_code}` : activeStockCode || t('memory_documents.detail_title')}
        width="calc(100vw - 240px)"
        placement="right"
        push={false}
        open={activeStockCode !== null}
        onClose={handleCloseDetail}
        loading={detailLoading}
        extra={detail ? (
          <Space>
            <Segmented<ViewMode>
              value={viewMode}
              onChange={setViewMode}
              options={[
                { value: 'rendered', label: t('memory_documents.rendered') },
                { value: 'source', label: t('memory_documents.source') },
              ]}
            />
            <Button icon={<ReloadOutlined />} loading={detailLoading} onClick={handleRefreshDetail}>
              {t('memory_documents.refresh')}
            </Button>
          </Space>
        ) : null}
      >
        {detail ? (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Alert type="info" showIcon message={t('memory_documents.read_only')} />
            <Descriptions bordered size="small" column={{ xs: 1, sm: 2, lg: 3 }}>
              <Descriptions.Item label={t('memory_documents.stock_code')}>
                {detail.stock_code}
              </Descriptions.Item>
              <Descriptions.Item label={t('memory_documents.stock_name')}>
                {detail.stock_name || detail.stock_code}
              </Descriptions.Item>
              <Descriptions.Item label={t('memory_documents.version_label')}>
                {detail.version}
              </Descriptions.Item>
              <Descriptions.Item label={t('memory_documents.size_label')}>
                {t('memory_documents.size_chars', { size: detail.size_chars, max: detail.max_chars })}
              </Descriptions.Item>
              <Descriptions.Item label={t('memory_documents.created_at_label')}>
                {formatDateTime(detail.created_at)}
              </Descriptions.Item>
              <Descriptions.Item label={t('memory_documents.updated_at_label')}>
                {formatDateTime(detail.updated_at)}
              </Descriptions.Item>
            </Descriptions>
            <Card size="small" title={t('memory_documents.content')}>
              {viewMode === 'rendered' ? (
                <DebateMarkdown content={detail.content || t('memory_documents.empty_content')} />
              ) : (
                <Paragraph
                  code
                  style={{ marginBottom: 0, maxHeight: 620, overflow: 'auto', whiteSpace: 'pre-wrap' }}
                >
                  {detail.content || t('memory_documents.empty_content')}
                </Paragraph>
              )}
            </Card>
          </Space>
        ) : null}
      </Drawer>
    </Space>
  );
};

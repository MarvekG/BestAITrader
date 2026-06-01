import React from 'react';
import {
  Alert,
  App as AntdApp,
  Button,
  Card,
  DatePicker,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  List,
  Select,
  Space,
  Tag,
  Typography,
} from 'antd';
import { ReloadOutlined, SearchOutlined } from '@ant-design/icons';
import dayjs, { Dayjs } from 'dayjs';
import { useTranslation } from 'react-i18next';

import {
  experienceApi,
  ExperienceLibraryDetail,
  ExperienceLibraryFilters,
  ExperienceLibraryItem,
  ExperienceReviewHorizon,
} from '../../api/experience';
import { formatErrorMessage, getApiErrorResponseData } from '../../utils/errorUtils';
import { ReviewTriadCards } from './ReviewTriadCards';

const { Paragraph, Text, Title } = Typography;
const { RangePicker } = DatePicker;

type FilterValues = Omit<ExperienceLibraryFilters, 'created_from' | 'created_to'> & {
  created_range?: [Dayjs, Dayjs];
};

const compactFilters = (values: FilterValues): ExperienceLibraryFilters => {
  const filters: ExperienceLibraryFilters = {};
  for (const [key, value] of Object.entries(values)) {
    if (key === 'created_range' || value === undefined || value === null || value === '') {
      continue;
    }
    filters[key as keyof ExperienceLibraryFilters] = value as never;
  }
  if (values.created_range?.length === 2) {
    filters.created_from = values.created_range[0].startOf('day').toISOString();
    filters.created_to = values.created_range[1].endOf('day').toISOString();
  }
  filters.page = values.page || 1;
  filters.page_size = values.page_size || 20;
  return filters;
};

const flattenTags = (tags?: Record<string, string[]>) => (
  Object.values(tags || {}).flat().filter(Boolean)
);

interface ExperienceLibraryPanelProps {
  onOpenReview: (item: ExperienceLibraryItem) => void;
}

export const ExperienceLibraryPanel: React.FC<ExperienceLibraryPanelProps> = ({ onOpenReview }) => {
  const { t } = useTranslation();
  const { message } = AntdApp.useApp();
  const [form] = Form.useForm<FilterValues>();
  const [loading, setLoading] = React.useState(false);
  const [rebuilding, setRebuilding] = React.useState(false);
  const [items, setItems] = React.useState<ExperienceLibraryItem[]>([]);
  const [total, setTotal] = React.useState(0);
  const [page, setPage] = React.useState(1);
  const [pageSize, setPageSize] = React.useState(20);
  const [detailLoading, setDetailLoading] = React.useState(false);
  const [detail, setDetail] = React.useState<ExperienceLibraryDetail | null>(null);

  const loadLibrary = React.useCallback(async (nextPage: number, nextPageSize: number) => {
    setLoading(true);
    try {
      const filters = compactFilters({
        ...form.getFieldsValue(),
        page: nextPage,
        page_size: nextPageSize,
      });
      const data = await experienceApi.listLibrary(filters);
      setItems(data.items || []);
      setTotal(data.total || 0);
      setPage(data.page || nextPage);
      setPageSize(data.page_size || nextPageSize);
    } catch (error) {
      const responseData = getApiErrorResponseData(error) as { detail?: unknown } | null | undefined;
      message.error(formatErrorMessage(responseData?.detail) || t('common.error'));
    } finally {
      setLoading(false);
    }
  }, [form, message, t]);

  React.useEffect(() => {
    void loadLibrary(1, pageSize);
  }, [loadLibrary, pageSize]);

  const handleInspect = async (item: ExperienceLibraryItem) => {
    setDetailLoading(true);
    try {
      const data = await experienceApi.getLibraryDetail(item.id);
      setDetail(data);
    } catch (error) {
      const responseData = getApiErrorResponseData(error) as { detail?: unknown } | null | undefined;
      message.error(formatErrorMessage(responseData?.detail) || t('common.error'));
    } finally {
      setDetailLoading(false);
    }
  };

  const handleRebuild = async () => {
    setRebuilding(true);
    try {
      const stats = await experienceApi.rebuildLibrary();
      message.success(
        t('experience_library.rebuild_success', {
          created: stats.created,
          updated: stats.updated,
          skipped: stats.skipped,
          failed: stats.failed,
        }),
      );
      await loadLibrary(1, pageSize);
    } catch (error) {
      const responseData = getApiErrorResponseData(error) as { detail?: unknown } | null | undefined;
      message.error(formatErrorMessage(responseData?.detail) || t('common.error'));
    } finally {
      setRebuilding(false);
    }
  };

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card>
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <div>
            <Title level={3} style={{ marginBottom: 4 }}>{t('experience_library.title')}</Title>
            <Text type="secondary">{t('experience_library.subtitle')}</Text>
          </div>
          <Alert type="info" showIcon message={t('experience_library.memory_first_notice')} />
          <Form form={form} layout="inline" onFinish={() => void loadLibrary(1, pageSize)}>
            <Form.Item name="stock_code">
              <Input allowClear placeholder={t('experience_library.stock_placeholder')} style={{ width: 150 }} />
            </Form.Item>
            <Form.Item name="industry">
              <Input allowClear placeholder={t('experience_library.industry_placeholder')} style={{ width: 150 }} />
            </Form.Item>
            <Form.Item name="strategy">
              <Input allowClear placeholder={t('experience_library.strategy_placeholder')} style={{ width: 150 }} />
            </Form.Item>
            <Form.Item name="review_horizon">
              <Select<ExperienceReviewHorizon>
                allowClear
                placeholder={t('experience_library.horizon_placeholder')}
                style={{ width: 130 }}
                options={[
                  { value: '5d', label: t('experience.review_horizons.5d') },
                  { value: '20d', label: t('experience.review_horizons.20d') },
                  { value: '60d', label: t('experience.review_horizons.60d') },
                ]}
              />
            </Form.Item>
            <Form.Item name="correctness">
              <Select
                allowClear
                placeholder={t('experience_library.correctness_placeholder')}
                style={{ width: 150 }}
                options={['correct', 'partially_correct', 'incorrect', 'inconclusive'].map((value) => ({
                  value,
                  label: t(`experience.correctness_statuses.${value}`),
                }))}
              />
            </Form.Item>
            <Form.Item name="importance">
              <Select
                allowClear
                placeholder={t('experience_library.importance_placeholder')}
                style={{ width: 130 }}
                options={['low', 'medium', 'high'].map((value) => ({
                  value,
                  label: t(`experience.memory_importance_${value}`),
                }))}
              />
            </Form.Item>
            <Form.Item name="tag">
              <Input allowClear placeholder={t('experience_library.tag_placeholder')} style={{ width: 150 }} />
            </Form.Item>
            <Form.Item name="keyword">
              <Input allowClear placeholder={t('experience_library.keyword_placeholder')} style={{ width: 220 }} />
            </Form.Item>
            <Form.Item name="created_range">
              <RangePicker />
            </Form.Item>
            <Form.Item>
              <Space>
                <Button type="primary" icon={<SearchOutlined />} htmlType="submit">
                  {t('experience_library.search')}
                </Button>
                <Button
                  onClick={() => {
                    form.resetFields();
                    void loadLibrary(1, pageSize);
                  }}
                >
                  {t('experience_library.reset')}
                </Button>
                <Button icon={<ReloadOutlined />} loading={rebuilding} onClick={handleRebuild}>
                  {t('experience_library.rebuild')}
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
              onChange: (nextPage, nextPageSize) => void loadLibrary(nextPage, nextPageSize),
            }}
            renderItem={(item) => (
              <List.Item
                actions={[
                  <Button key="detail" type="link" onClick={() => void handleInspect(item)}>
                    {t('experience_library.view_detail')}
                  </Button>,
                  <Button
                    key="review"
                    type="link"
                    onClick={() => onOpenReview(item)}
                  >
                    {t('experience_library.open_review')}
                  </Button>,
                ]}
              >
                <List.Item.Meta
                  title={(
                    <Space wrap>
                      <Text strong>{item.stock_code || '-'}</Text>
                      {item.stock_name ? <Tag>{item.stock_name}</Tag> : null}
                      {item.industry ? <Tag>{item.industry}</Tag> : null}
                      {item.strategy ? <Tag>{item.strategy}</Tag> : null}
                      {item.review_horizon ? <Tag color="blue">{t(`experience.review_horizons.${item.review_horizon}`)}</Tag> : null}
                      {item.correctness ? <Tag>{t(`experience.correctness_statuses.${item.correctness}`)}</Tag> : null}
                      {item.importance ? <Tag>{t(`experience.memory_importance_${item.importance}`)}</Tag> : null}
                    </Space>
                  )}
                  description={(
                    <Space direction="vertical" size={8} style={{ width: '100%' }}>
                      <Paragraph style={{ marginBottom: 0 }}>{item.summary}</Paragraph>
                      <Space wrap>
                        {flattenTags(item.tags).slice(0, 12).map((tag) => <Tag key={tag}>{tag}</Tag>)}
                      </Space>
                      <Text type="secondary">{dayjs(item.created_at).format('YYYY-MM-DD HH:mm')}</Text>
                    </Space>
                  )}
                />
              </List.Item>
            )}
          />
        ) : (
          <Empty description={t('experience_library.empty')} />
        )}
      </Card>

      <Drawer
        title={t('experience_library.detail_title')}
        width={900}
        open={detail !== null || detailLoading}
        onClose={() => setDetail(null)}
        loading={detailLoading}
      >
        {detail ? (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Alert type="info" showIcon message={t('experience_library.memory_first_notice')} />
            <Descriptions bordered size="small" column={1}>
              <Descriptions.Item label={t('experience_library.memory_observation_id')}>{detail.memory_observation_id || '-'}</Descriptions.Item>
              <Descriptions.Item label={t('experience_library.memory_source_id')}>{detail.memory_source_id || '-'}</Descriptions.Item>
              <Descriptions.Item label={t('experience.review_run_id')}>{detail.review_run_id}</Descriptions.Item>
              <Descriptions.Item label={t('experience.session')}>{detail.session_id}</Descriptions.Item>
              <Descriptions.Item label={t('experience_library.outcome_label')}>{detail.outcome_label || '-'}</Descriptions.Item>
            </Descriptions>
            <Card size="small" title={t('experience_library.summary')}>
              <Paragraph style={{ whiteSpace: 'pre-wrap' }}>{detail.summary}</Paragraph>
            </Card>
            <ReviewTriadCards triads={detail.review_triads} />
            <Card size="small" title={t('experience_library.market_outcome')}>
              <Paragraph code style={{ whiteSpace: 'pre-wrap' }}>
                {JSON.stringify(detail.market_outcome_summary || {}, null, 2)}
              </Paragraph>
            </Card>
          </Space>
        ) : null}
      </Drawer>
    </Space>
  );
};

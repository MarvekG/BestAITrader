import React from 'react';
import { Alert, Button, Card, List, Space, Tag, Typography } from 'antd';
import { useTranslation } from 'react-i18next';

import type { ExperienceWrittenMemory } from '../../api/experience';

const { Text } = Typography;

const successfulMemoryStatuses = new Set(['success', 'accepted']);

const isSuccessfulMemoryStatus = (status?: string | null) => (
  status ? successfulMemoryStatuses.has(status) : false
);

type Props = {
  memories?: ExperienceWrittenMemory[];
  onOpenDocument?: (stockCode?: string | null) => void;
};

export const WrittenMemoryCards: React.FC<Props> = ({ memories = [], onOpenDocument }) => {
  const { t } = useTranslation();
  const hasMemoryWriteFailure = memories.some((item) => item.error || item.status === 'failed');

  const getStatusLabel = (status?: string | null) => {
    if (isSuccessfulMemoryStatus(status)) {
      return t('experience.memory_status_success');
    }
    if (status === 'failed' || status === 'error') {
      return t('experience.memory_status_failed');
    }
    return t('experience.memory_status_unknown');
  };

  return (
    <Card title={t('experience.written_memories')} size="small">
      <Space direction="vertical" size={10} style={{ width: '100%' }}>
        {hasMemoryWriteFailure ? (
          <Alert type="error" showIcon message={t('experience.memory_write_failed_alert')} />
        ) : null}
        {!memories.length ? (
          <Alert type="warning" showIcon message={t('experience.memory_document_skipped')} />
        ) : (
          <List
            size="small"
            dataSource={memories}
            renderItem={(item) => (
              <List.Item
                actions={onOpenDocument ? [
                  <Button key="open" type="link" size="small" onClick={() => onOpenDocument(item.stock_code)}>
                    {t('experience.open_memory_document')}
                  </Button>,
                ] : undefined}
              >
                <Space wrap>
                  <Tag color={isSuccessfulMemoryStatus(item.status) ? 'green' : item.status === 'failed' ? 'red' : 'default'}>
                    {getStatusLabel(item.status)}
                  </Tag>
                  {item.stock_code ? <Tag>{item.stock_code}</Tag> : null}
                  {item.version != null ? (
                    <Tag>{t('experience.memory_document_version', { version: item.version })}</Tag>
                  ) : null}
                  {item.size_chars != null ? (
                    <Tag>
                      {t('experience.memory_document_size', {
                        size: item.size_chars,
                        max: item.max_chars ?? '-',
                      })}
                    </Tag>
                  ) : null}
                  {item.error ? <Text type="danger">{item.error}</Text> : null}
                </Space>
              </List.Item>
            )}
          />
        )}
      </Space>
    </Card>
  );
};

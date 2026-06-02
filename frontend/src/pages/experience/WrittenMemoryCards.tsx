import React from 'react';
import { Alert, Button, Card, Drawer, Empty, List, Space, Tag, Typography } from 'antd';
import { useTranslation } from 'react-i18next';

import type { ExperienceWrittenMemory } from '../../api/experience';

const { Paragraph, Text } = Typography;

const successfulMemoryStatuses = new Set(['success', 'accepted']);

const isSuccessfulMemoryStatus = (status?: string) => !status || successfulMemoryStatuses.has(status);

type Props = {
  memories?: ExperienceWrittenMemory[];
  getMemoSessionLabel: (value?: string) => string;
  getMemoryImportanceLabel: (value?: string) => string;
};

export const WrittenMemoryCards: React.FC<Props> = ({
  memories = [],
  getMemoSessionLabel,
  getMemoryImportanceLabel,
}) => {
  const { t } = useTranslation();
  const [activeMemory, setActiveMemory] = React.useState<ExperienceWrittenMemory | null>(null);
  const hasMemoryWriteFailure = memories.some((item) => item.error || !isSuccessfulMemoryStatus(item.status));

  return (
    <Card title={t('experience.written_memories')} size="small">
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        {hasMemoryWriteFailure ? (
          <Alert type="error" showIcon message={t('experience.memory_write_failed_alert')} />
        ) : null}
        {!memories.length ? (
          <Alert type="warning" showIcon message={t('experience.memory_write_empty_alert')} />
        ) : null}
        {memories.length ? (
          <List
            dataSource={memories}
            renderItem={(item, index) => (
              <List.Item>
                <Space direction="vertical" size={8} style={{ width: '100%' }}>
                  <Space wrap>
                    <Text strong>{`${index + 1}.`}</Text>
                    <Tag color={item.memo_session === 'stock' ? 'blue' : 'default'}>{getMemoSessionLabel(item.memo_session)}</Tag>
                    <Tag>{getMemoryImportanceLabel(item.importance)}</Tag>
                    {item.stock_code ? <Tag>{item.stock_code}</Tag> : null}
                    {item.status ? <Tag color={isSuccessfulMemoryStatus(item.status) ? 'green' : 'default'}>{item.status}</Tag> : null}
                    {item.error ? <Tag color="red">{item.error}</Tag> : null}
                  </Space>
                  <Space direction="vertical" size={4} style={{ width: '100%' }}>
                    <Text type="secondary">{t('experience.memory_write_content')}</Text>
                    <Paragraph style={{ marginBottom: 0, whiteSpace: 'pre-wrap' }}>{item.content || '-'}</Paragraph>
                  </Space>
                  <Button size="small" onClick={() => setActiveMemory(item)}>
                    {t('experience.view_memory_evidence')}
                  </Button>
                </Space>
              </List.Item>
            )}
          />
        ) : (
          <Empty description={t('experience.no_written_memories')} />
        )}
      </Space>
      <Drawer
        title={t('experience.memory_evidence_chain')}
        width={720}
        open={activeMemory !== null}
        onClose={() => setActiveMemory(null)}
      >
        {activeMemory ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Paragraph style={{ whiteSpace: 'pre-wrap' }}>{activeMemory.content || '-'}</Paragraph>
            <Paragraph code style={{ whiteSpace: 'pre-wrap' }}>
              {JSON.stringify(activeMemory.evidence_chain || {}, null, 2)}
            </Paragraph>
          </Space>
        ) : null}
      </Drawer>
    </Card>
  );
};

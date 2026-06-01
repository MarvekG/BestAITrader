import React from 'react';
import { Card, Col, Empty, List, Row, Space, Tag, Typography } from 'antd';
import { useTranslation } from 'react-i18next';

import type { ExperienceReviewTriads } from '../../api/experience';

const { Paragraph, Text } = Typography;

type Props = {
  triads?: ExperienceReviewTriads;
};

const renderStringList = (items?: string[]) => (
  items && items.length ? (
    <List size="small" dataSource={items} renderItem={(item) => <List.Item>{item}</List.Item>} />
  ) : (
    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="-" />
  )
);

export const ReviewTriadCards: React.FC<Props> = ({ triads }) => {
  const { t } = useTranslation();
  const original = triads?.original_judgment;
  const signals = triads?.signal_validation;
  const improvements = triads?.decision_process_improvement;

  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} lg={8}>
        <Card title={t('experience.triad_original_judgment')} size="small">
          <div className="experience-scroll-panel experience-triad-card-scroll">
            {original ? (
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                <Space wrap>
                  <Tag>{original.pm_decision || '-'}</Tag>
                  <Tag>{t(`experience.correctness_statuses.${original.verdict || 'inconclusive'}`)}</Tag>
                  <Tag>{Number(original.score || 0).toFixed(1)}</Tag>
                </Space>
                <Paragraph style={{ marginBottom: 0 }}>{original.outcome_basis || '-'}</Paragraph>
                <Text type="secondary">{original.reasoning || '-'}</Text>
              </Space>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="-" />
            )}
          </div>
        </Card>
      </Col>
      <Col xs={24} lg={8}>
        <Card title={t('experience.triad_signal_validation')} size="small">
          <div className="experience-scroll-panel experience-triad-card-scroll">
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              <div>
                <Text strong>{t('experience.validated_signals')}</Text>
                <List
                  size="small"
                  dataSource={signals?.validated_signals || []}
                  locale={{ emptyText: '-' }}
                  renderItem={(item) => (
                    <List.Item>
                      <Space direction="vertical" size={2}>
                        <Text>{item.signal}</Text>
                        <Text type="secondary">{item.evidence || item.lesson || '-'}</Text>
                      </Space>
                    </List.Item>
                  )}
                />
              </div>
              <div>
                <Text strong>{t('experience.invalidated_signals')}</Text>
                <List
                  size="small"
                  dataSource={signals?.invalidated_signals || []}
                  locale={{ emptyText: '-' }}
                  renderItem={(item) => (
                    <List.Item>
                      <Space direction="vertical" size={2}>
                        <Text>{item.signal}</Text>
                        <Text type="secondary">{item.evidence || item.lesson || '-'}</Text>
                      </Space>
                    </List.Item>
                  )}
                />
              </div>
            </Space>
          </div>
        </Card>
      </Col>
      <Col xs={24} lg={8}>
        <Card title={t('experience.triad_process_improvement')} size="small">
          <div className="experience-scroll-panel experience-triad-card-scroll">
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              <div><Text strong>{t('experience.debate_changes')}</Text>{renderStringList(improvements?.debate_changes)}</div>
              <div><Text strong>{t('experience.pm_changes')}</Text>{renderStringList(improvements?.pm_changes)}</div>
              <div><Text strong>{t('experience.risk_control_changes')}</Text>{renderStringList(improvements?.risk_control_changes)}</div>
            </Space>
          </div>
        </Card>
      </Col>
    </Row>
  );
};

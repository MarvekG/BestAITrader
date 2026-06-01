import React from 'react';
import { Button, Card, Empty, List, Segmented, Space, Tag, Typography } from 'antd';
import dayjs from 'dayjs';
import { useTranslation } from 'react-i18next';

import type { ExperienceReviewCandidate, ExperienceReviewHorizon } from '../../api/experience';

const { Text } = Typography;

const horizonColorMap: Record<ExperienceReviewHorizon, string> = {
  '5d': 'blue',
  '20d': 'green',
  '60d': 'purple',
};

type Props = {
  candidates: ExperienceReviewCandidate[];
  loading: boolean;
  selectedSessionId?: string;
  selectedHorizon?: ExperienceReviewHorizon;
  running: boolean;
  onSelect: (candidate: ExperienceReviewCandidate, horizon: ExperienceReviewHorizon) => void;
  onRun: (candidate: ExperienceReviewCandidate, horizon: ExperienceReviewHorizon) => void;
};

export const ReviewCandidatePanel: React.FC<Props> = ({
  candidates,
  loading,
  selectedSessionId,
  selectedHorizon,
  running,
  onSelect,
  onRun,
}) => {
  const { t } = useTranslation();
  const [horizonFilter, setHorizonFilter] = React.useState<ExperienceReviewHorizon | 'all'>('all');
  const visibleCandidates = React.useMemo(() => {
    if (horizonFilter === 'all') {
      return candidates;
    }
    return candidates.filter((item) => (
      item.eligible_horizons.includes(horizonFilter)
      || item.latest_completed_horizons.includes(horizonFilter)
      || item.active_horizons.includes(horizonFilter)
      || item.failed_horizons.includes(horizonFilter)
    ));
  }, [candidates, horizonFilter]);

  return (
    <Card
      title={t('experience.review_candidates')}
      loading={loading}
      extra={(
        <Segmented
          size="small"
          value={horizonFilter}
          options={[
            { label: t('experience.review_horizon_all'), value: 'all' },
            { label: t('experience.review_horizons.5d'), value: '5d' },
            { label: t('experience.review_horizons.20d'), value: '20d' },
            { label: t('experience.review_horizons.60d'), value: '60d' },
          ]}
          onChange={(value) => setHorizonFilter(value as ExperienceReviewHorizon | 'all')}
        />
      )}
    >
      <div className="experience-scroll-panel experience-review-candidate-scroll">
        {visibleCandidates.length ? (
          <List
            size="small"
            dataSource={visibleCandidates}
            renderItem={(item) => {
              const readyHorizons = item.eligible_horizons.filter(
                (horizon) => !item.latest_completed_horizons.includes(horizon) && !item.active_horizons.includes(horizon),
              );
              return (
                <List.Item>
                  <Space direction="vertical" size={6} style={{ width: '100%' }}>
                    <Space wrap>
                      <Tag>{item.stock_code}</Tag>
                      {item.stock_name ? <Tag>{item.stock_name}</Tag> : null}
                      {item.industry ? <Tag>{item.industry}</Tag> : null}
                      <Tag>{t(`experience.candidate_statuses.${item.review_status}`)}</Tag>
                      <Tag>{t('experience.market_day_count', { count: item.market_day_count })}</Tag>
                    </Space>
                    <Text type="secondary">
                      {item.pm_created_at ? dayjs(item.pm_created_at).format('YYYY-MM-DD HH:mm') : '-'} · {item.trading_frequency || '-'} · {item.trading_strategy || '-'}
                    </Text>
                    <Space wrap>
                      {item.eligible_horizons.map((horizon) => (
                        <Tag key={horizon} color={horizonColorMap[horizon]}>
                          {t(`experience.review_horizons.${horizon}`)}
                        </Tag>
                      ))}
                      {item.next_horizon ? (
                        <Tag>
                          {t('experience.next_horizon_gap', {
                            horizon: t(`experience.review_horizons.${item.next_horizon}`),
                            count: item.days_until_next_horizon || 0,
                          })}
                        </Tag>
                      ) : null}
                    </Space>
                    <Space wrap>
                      {readyHorizons.map((horizon) => (
                        <Button
                          key={horizon}
                          size="small"
                          type="primary"
                          loading={running && selectedSessionId === item.session_id && selectedHorizon === horizon}
                          onClick={() => {
                            onSelect(item, horizon);
                            onRun(item, horizon);
                          }}
                        >
                          {t('experience.run_horizon_review', { horizon: t(`experience.review_horizons.${horizon}`) })}
                        </Button>
                      ))}
                      {!readyHorizons.length ? <Text type="secondary">{t('experience.no_ready_horizon')}</Text> : null}
                    </Space>
                  </Space>
                </List.Item>
              );
            }}
          />
        ) : (
          <Empty description={t('experience.no_review_candidates')} />
        )}
      </div>
    </Card>
  );
};

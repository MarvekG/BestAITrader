import React, { useCallback, useEffect, useState } from 'react';
import { Card, Statistic, Row, Col, Button, Modal, InputNumber, App as AntdApp } from 'antd';
import { EditOutlined } from '@ant-design/icons';
import { tradeApi, AccountAssets } from '../../api/trade';
import { useSessionStore } from '../../store/useSessionStore';
import { useTranslation } from 'react-i18next';

export const AccountOverview: React.FC = () => {
  const { t } = useTranslation();
  const { activeSession } = useSessionStore();
  const [assets, setAssets] = useState<AccountAssets | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [newFunds, setNewFunds] = useState<number>(0);
  const { message } = AntdApp.useApp();

  const fetchAssets = useCallback(() => {
    if (activeSession) {
      tradeApi.getAccountAssets(activeSession.session_id).then(setAssets);
    } else {
      // 即使没有 session，也可以获取账户信息
      tradeApi.getAccountAssets().then(setAssets);
    }
  }, [activeSession]);

  useEffect(() => {
    fetchAssets();
    const interval = setInterval(fetchAssets, 3000); // Polling for now
    return () => clearInterval(interval);
  }, [fetchAssets]);

  const handleUpdateFunds = async () => {
    if (!newFunds || isNaN(Number(newFunds))) return;
    try {
      // 使用新的 API，session_id 作为可选参数
      await tradeApi.setTotalFunds(Number(newFunds), activeSession?.session_id);
      message.success(t('account.funds_updated'));
      setIsModalOpen(false);
      // Refresh
      if (activeSession) {
        tradeApi.getAccountAssets(activeSession.session_id).then(setAssets);
      } else {
        tradeApi.getAccountAssets().then(setAssets);
      }
    } catch {
      message.error(t('account.funds_update_failed'));
    }
  };

  if (!assets) return null;

  return (
    <Card
      size="small"
      style={{ marginBottom: 16 }}
      title={t('account.assets_title')}
      extra={
        <Button
          type="text"
          icon={<EditOutlined />}
          onClick={() => {
            setNewFunds(assets.total_assets);
            setIsModalOpen(true);
          }}
        />
      }
    >
      <Row gutter={16}>
        <Col span={8}>
          <Statistic
            title={t('account.total_assets')}
            value={assets.total_assets}
            precision={2}
            prefix="¥"
          />
        </Col>
        <Col span={8}>
          <Statistic
            title={t('account.available_cash')}
            value={assets.cash_balance}
            precision={2}
            prefix="¥"
          />
        </Col>
        <Col span={8}>
          <Statistic
            title={t('account.market_value')}
            value={assets.market_value}
            precision={2}
            prefix="¥"
          />
        </Col>
      </Row>

      <Modal
        title={t('account.adjust_capital_title')}
        open={isModalOpen}
        onOk={handleUpdateFunds}
        onCancel={() => setIsModalOpen(false)}
        cancelText={t('common.cancel')}
      >
        <p>{t('account.adjust_capital_desc')}</p>
        <InputNumber
          style={{ width: '100%' }}
          prefix="¥"
          value={newFunds}
          onChange={(v) => setNewFunds(v || 0)}
          min={0}
        />
      </Modal>
    </Card>
  );
};

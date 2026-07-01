import React, { useState } from 'react';
import { Layout, Menu, Button, Modal, Form, Input, Select, theme } from 'antd';
import { ThunderboltOutlined, PlusOutlined, LogoutOutlined } from '@ant-design/icons';
import { useSessionStore } from '../store/useSessionStore';
import { Session } from '../api/session';
import { wsManager } from '../services/websocket';
import { clearAuthSession } from '../services/authSession';
import LanguageSwitcher from '../components/LanguageSwitcher';
import ThemeSwitcher from '../components/ThemeSwitcher';
import { useThemeMode } from '../theme/useThemeMode';
import { useFeedback } from '../hooks/useFeedback';

import { useTranslation } from 'react-i18next';

const { Sider, Content } = Layout;

interface CreateSessionFormValues {
  code: string;
  name?: string;
  trading_frequency: string;
  trading_strategy: string;
}

export const MainLayout: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { t } = useTranslation();
  const { mode } = useThemeMode();
  const { sessions, activeSession, setActiveSession, fetchSessions, createSession } = useSessionStore();
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [form] = Form.useForm();
  const {
    token: { colorBgContainer, colorBgLayout, colorBorder, colorText },
  } = theme.useToken();
  const isDarkMode = mode === 'dark';
  const siderTheme = mode === 'dark' ? 'dark' : 'light';
  const siderBackground = isDarkMode ? 'var(--app-sider-bg)' : colorBgContainer;
  const siderBorderColor = isDarkMode ? 'var(--app-sider-border)' : colorBorder;
  const siderTextColor = isDarkMode ? '#ffffff' : colorText;
  const message = useFeedback();

  React.useEffect(() => {
    fetchSessions();
  }, [fetchSessions]);

  const handleCreate = async (values: CreateSessionFormValues) => {
    setLoading(true);
    try {
      // 映射表单字段到 API 期望的字段
      await createSession(
        values.code,
        values.name || t('layout.unknown_stock'),
        values.trading_frequency,
        values.trading_strategy
      );
      message.success(t('layout.session_created'));
      setIsModalOpen(false);
      form.resetFields();
    } catch (error) {
      console.error('Failed to create session:', error);
      message.error(t('layout.session_create_failed'));
    } finally {
      setLoading(false);
    }
  };

  const handleLogout = () => {
    wsManager.disconnect();
    clearAuthSession();
    // Reload page to reset state and return to login
    window.location.reload();
  };

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider
        width={250}
        theme={siderTheme}
        style={{ background: siderBackground, borderRight: `1px solid ${siderBorderColor}`, display: 'flex', flexDirection: 'column' }}
      >
        <div style={{ height: 64, color: siderTextColor, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 18, fontWeight: 'bold' }}>
          <ThunderboltOutlined style={{ marginRight: 8 }} /> {t('auth.title')}
        </div>

        <div style={{ padding: '0 16px 16px' }}>
          <Button type="primary" block icon={<PlusOutlined />} onClick={() => setIsModalOpen(true)}>
            {t('menu.new_session')}
          </Button>
        </div>

        <Menu
          theme={siderTheme}
          mode="inline"
          style={{ flex: 1, overflowY: 'auto' }}
          selectedKeys={activeSession ? [activeSession.session_id] : []}
          items={sessions.map((s: Session) => ({
            key: s.session_id,
            label: `${s.stock_name || s.stock_code}`,
            onClick: () => setActiveSession(s),
          }))}
        />

        <div style={{ padding: 16, borderTop: `1px solid ${siderBorderColor}`, display: 'flex', flexDirection: 'column', gap: '12px' }}>
          <ThemeSwitcher block />
          <LanguageSwitcher block />
          <Button block danger icon={<LogoutOutlined />} onClick={handleLogout}>
            {t('menu.logout')}
          </Button>
        </div>
      </Sider>

      <Layout style={{ background: colorBgLayout }}>
        <Content style={{ background: colorBgContainer, padding: 24 }}>
          {children}
        </Content>
      </Layout>

      <Modal title={t('layout.new_session_title')} open={isModalOpen} onCancel={() => setIsModalOpen(false)} footer={null}>
        <Form form={form} onFinish={handleCreate}>
          <Form.Item name="code" rules={[{ required: true }]}>
            <Input placeholder={t('layout.stock_code_placeholder')} />
          </Form.Item>
          <Form.Item name="name">
            <Input placeholder={t('layout.stock_name_placeholder')} />
          </Form.Item>
          <Form.Item name="trading_frequency" label={t('warehouse.trading_frequency')} initialValue={t('warehouse.freq_position_trading')} rules={[{ required: true }]}>
            <Select>
              <Select.Option value={t('warehouse.freq_day_trading')}>{t('warehouse.freq_day_trading')}</Select.Option>
              <Select.Option value={t('warehouse.freq_swing_trading')}>{t('warehouse.freq_swing_trading')}</Select.Option>
              <Select.Option value={t('warehouse.freq_position_trading')}>{t('warehouse.freq_position_trading')}</Select.Option>
            </Select>
          </Form.Item>
          <Form.Item name="trading_strategy" label={t('warehouse.trading_strategy')} initialValue={t('warehouse.strategy_trend')} rules={[{ required: true }]}>
            <Select>
              <Select.Option value={t('warehouse.strategy_value')}>{t('warehouse.strategy_value')}</Select.Option>
              <Select.Option value={t('warehouse.strategy_growth')}>{t('warehouse.strategy_growth')}</Select.Option>
              <Select.Option value={t('warehouse.strategy_trend')}>{t('warehouse.strategy_trend')}</Select.Option>
            </Select>
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={loading}>{t('layout.start')}</Button>
        </Form>
      </Modal>
    </Layout>
  );
};


import React from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { Layout, Menu, Button, theme } from 'antd';
import {
  DashboardOutlined,
  DatabaseOutlined,
  HistoryOutlined,
  SettingOutlined,
  LogoutOutlined,
  UserOutlined,
  LineChartOutlined,
  KeyOutlined,
  RobotOutlined,
  ExperimentOutlined,
  ReadOutlined,
} from '@ant-design/icons';
import { TaskCompletedMessage, WebSocketMessage, wsManager } from '../services/websocket';
import { useWebSocketSubscription } from '../hooks/useWebSocketSubscription';
import { useTranslation } from 'react-i18next';
import { apiHistory } from '../utils/apiHistory';
import { getApiErrorDetail } from '../utils/errorUtils';
import { Modal, Form, Input, message } from 'antd';
import { authApi } from '../api/auth';
import { GlobalTaskNotifications } from '../components/GlobalTaskNotifications';
import LanguageSwitcher from '../components/LanguageSwitcher';
import ThemeSwitcher from '../components/ThemeSwitcher';
import { useThemeMode } from '../theme/useThemeMode';
import { clearAuthSession } from '../services/authSession';

const { Sider, Content } = Layout;


export const DashboardLayout: React.FC = () => {
  const { t } = useTranslation();
  const { mode } = useThemeMode();
  const navigate = useNavigate();
  const location = useLocation();
  const {
    token: {
      borderRadiusLG,
      colorBgContainer,
      colorBgLayout,
      colorBorder,
      colorPrimary,
      colorText,
      colorTextSecondary,
    },
  } = theme.useToken();
  const isDarkMode = mode === 'dark';
  const siderTheme = mode === 'dark' ? 'dark' : 'light';
  const siderBackground = isDarkMode ? 'var(--app-sider-bg)' : colorBgContainer;
  const siderBorderColor = isDarkMode ? 'var(--app-sider-border)' : colorBorder;
  const siderTextColor = isDarkMode ? '#ffffff' : colorText;
  const siderSecondaryTextColor = isDarkMode ? 'rgba(255, 255, 255, 0.65)' : colorTextSecondary;

  const [isResetPwdModalVisible, setIsResetPwdModalVisible] = React.useState(false);
  const [resetPwdLoading, setResetPwdLoading] = React.useState(false);
  const [resetForm] = Form.useForm();

  const handleLogout = () => {
    clearAuthSession();
    navigate('/login');
  };

  const handleResetPassword = async () => {
    try {
      const values = await resetForm.validateFields();
      setResetPwdLoading(true);

      await authApi.resetPassword({ new_password: values.new_password });

      message.success(t('auth.password_reset_success'));
      setIsResetPwdModalVisible(false);
      resetForm.resetFields();

      // Force logout after password change
      handleLogout();
    } catch (error) {
      const errorDetail = getApiErrorDetail(error);
      const detail = typeof errorDetail === 'string' ? errorDetail : undefined;
      if (detail) {
        message.error(detail);
      } else if (!(error && typeof error === 'object' && 'errorFields' in error)) {
        // Not a validation error
        message.error(t('auth.password_reset_failed'));
      }
    } finally {
      setResetPwdLoading(false);
    }
  };

  const navItems = [
    { key: '/dashboard', icon: <DashboardOutlined />, label: t('layout.menu.trading_desk') },
    { key: '/warehouse', icon: <DatabaseOutlined />, label: t('layout.menu.stock_warehouse') },
    { key: '/market-watch', icon: <LineChartOutlined />, label: t('layout.menu.market_watch') },
    { key: '/data-manager', icon: <DatabaseOutlined />, label: t('layout.menu.data_manager') },
    { key: '/ai-stock-picker', icon: <RobotOutlined />, label: t('layout.menu.ai_stock_picker') },
    { key: '/experience', icon: <ExperimentOutlined />, label: t('layout.menu.experience_analyst') },
    { key: '/trading', icon: <HistoryOutlined />, label: t('layout.menu.trading_center') },
    {
      key: '/texts',
      icon: <ReadOutlined />,
      label: t('layout.menu.trading_novel'),
    },
    { key: '/api-history', icon: <HistoryOutlined />, label: t('layout.menu.api_history') },
    { key: '/settings', icon: <SettingOutlined />, label: t('layout.menu.settings') },
  ];

  /* 
   * Global WebSocket Connection
   * Ensure we have a connection for system-wide notifications (like task completion)
   */
  const sessionIdRef = React.useRef<string | null>(null);

  React.useEffect(() => {
    // Generate a stable session ID only once
    if (!sessionIdRef.current) {
      sessionIdRef.current = `dashboard-${Math.random().toString(36).substr(2, 9)}`;
    }

    wsManager.connect(sessionIdRef.current);

    // Note: We do NOT disconnect on cleanup to avoid React StrictMode double-mount issues
    // The wsManager handles reconnection and connection reuse internally
    // return () => {
    //   wsManager.disconnect();
    // };
  }, []);

  useWebSocketSubscription('task_completed', (msg: WebSocketMessage) => {
    const data = (msg as TaskCompletedMessage).data;
    const taskId = data?.task_id;
    if (data && taskId) {
      const rawStatus = data.status;
      if (rawStatus !== 'completed' && rawStatus !== 'success' && rawStatus !== 'failed' && rawStatus !== 'error') {
        return;
      }
      const status = (rawStatus === 'completed' || rawStatus === 'success') ? 'completed' : 'failed';
      const error = data.error_message || data.error;
      apiHistory.updateResponse(taskId, status, data, error);
    }
  });

  return (
    <Layout style={{ minHeight: '100vh', background: colorBgLayout }}>
      <GlobalTaskNotifications />
      <Sider
        width={240}
        theme={siderTheme}
        style={{ background: siderBackground, borderRight: `1px solid ${siderBorderColor}` }}
      >
        <div style={{
          height: 64,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          borderBottom: `1px solid ${siderBorderColor}`,
          marginBottom: 16
        }}>
          <div className="flex items-center space-x-2" style={{ color: siderTextColor, fontSize: 18, fontWeight: 'bold' }}>
            <LineChartOutlined style={{ color: colorPrimary, marginRight: 8 }} />
            <span>AI Trader</span>
          </div>
        </div>

        <Menu
          theme={siderTheme}
          mode="inline"
          selectedKeys={[location.pathname]}
          items={navItems.map(item => ({
            key: item.key,
            icon: item.icon,
            label: item.label,
            onClick: () => navigate(item.key)
          }))}
          style={{ borderRight: 0 }}
        />

        <div style={{
          position: 'absolute',
          bottom: 0,
          width: '100%',
          padding: 16,
          borderTop: `1px solid ${siderBorderColor}`
        }}>
          <div style={{ marginBottom: 12 }}>
            <ThemeSwitcher block />
          </div>
          <div style={{ marginBottom: 12 }}>
            <LanguageSwitcher block />
          </div>
          <div style={{ display: 'flex', alignItems: 'center', marginBottom: 16, paddingLeft: 8, color: siderSecondaryTextColor }}>
            <UserOutlined style={{ marginRight: 8 }} />
            <span>{t('common.admin')}</span>
          </div>
          <Button
            block
            icon={<KeyOutlined />}
            onClick={() => setIsResetPwdModalVisible(true)}
            type="text"
            style={{ textAlign: 'left', paddingLeft: 8, color: siderTextColor }}
          >
            {t('auth.reset_password')}
          </Button>
          <Button
            danger
            block
            icon={<LogoutOutlined />}
            onClick={handleLogout}
            type="text"
            style={{ textAlign: 'left', paddingLeft: 8 }}
          >
            {t('layout.menu.logout')}
          </Button>
        </div>
      </Sider>

      <Modal
        title={t('auth.reset_password')}
        open={isResetPwdModalVisible}
        onOk={handleResetPassword}
        onCancel={() => {
          setIsResetPwdModalVisible(false);
          resetForm.resetFields();
        }}
        confirmLoading={resetPwdLoading}
        destroyOnHidden
      >
        <Form form={resetForm} layout="vertical">
          <Form.Item
            name="new_password"
            label={t('auth.new_password')}
            rules={[{ required: true, message: t('auth.new_password_required') }, { min: 6, message: t('auth.password_required', { min: 6 }) }]}
          >
            <Input.Password placeholder={t('auth.new_password')} />
          </Form.Item>
          <Form.Item
            name="confirm_password"
            label={t('auth.confirm_password')}
            dependencies={['new_password']}
            rules={[
              { required: true, message: t('auth.confirm_password_required') },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || getFieldValue('new_password') === value) {
                    return Promise.resolve();
                  }
                  return Promise.reject(new Error(t('auth.password_mismatch')));
                },
              }),
            ]}
          >
            <Input.Password placeholder={t('auth.confirm_password')} />
          </Form.Item>
        </Form>
      </Modal>

      <Layout style={{ background: colorBgLayout }}>
        <Content style={{ margin: '1px', overflow: 'hidden' }}>
          <div style={{
            height: '100%',
            overflow: 'auto',
            background: colorBgContainer,
            borderRadius: borderRadiusLG,
            padding: 10
          }}>
            <Outlet />
          </div>
        </Content>
      </Layout>
    </Layout>
  );
};

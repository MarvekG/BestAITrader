import React from 'react';
import { Card, Form, Input, Button, Typography, App as AntdApp, theme } from 'antd';
import { UserOutlined, LockOutlined, ThunderboltOutlined } from '@ant-design/icons';
import axios from 'axios';
import { authApi } from '../../api/auth';
import { setAuthToken } from '../../services/authSession';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import LanguageSwitcher from '../../components/LanguageSwitcher';
import ThemeSwitcher from '../../components/ThemeSwitcher';

const { Title, Text } = Typography;

interface LoginFormValues {
  username: string;
  password: string;
}

export const Login: React.FC = () => {
  const { t } = useTranslation();
  const {
    token: { colorBgContainer, colorBgLayout, colorBorderSecondary, colorPrimary, colorText, colorTextQuaternary },
  } = theme.useToken();
  const [loading, setLoading] = React.useState(false);
  const { message } = AntdApp.useApp();
  const navigate = useNavigate();

  const handleSubmit = async (values: LoginFormValues) => {
    setLoading(true);
    try {
      const response = await authApi.login(new URLSearchParams({
        username: values.username,
        password: values.password
      }));

      setAuthToken(response.access_token);
      message.success(t('auth.welcome'));
      navigate('/dashboard');
    } catch (error) {
      console.error('Login failed:', error);

      let errorMsg = t('auth.login_failed');

      if (axios.isAxiosError(error) && error.response) {
        // 请求已发出，且服务器响应了状态码
        const status = error.response.status;
        if (status === 401) {
          errorMsg = t('auth.login_failed_401');
        } else if (status >= 500) {
          errorMsg = t('auth.login_failed_server');
        }
      } else if (axios.isAxiosError(error) && error.request) {
        // 请求已发出，但没有收到响应 (通常是网络问题或后端未启动)
        errorMsg = t('auth.login_failed_network');
      }

      message.error(errorMsg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{
      display: 'flex',
      justifyContent: 'center',
      alignItems: 'center',
      minHeight: '100vh',
      position: 'relative',
      background: colorBgLayout
    }}>
      <div style={{ position: 'absolute', right: 24, top: 24, display: 'flex', alignItems: 'center', gap: 12 }}>
        <ThemeSwitcher />
        <LanguageSwitcher />
      </div>
      <Card
        style={{ width: 400, maxWidth: 'calc(100vw - 32px)', background: colorBgContainer, border: `1px solid ${colorBorderSecondary}` }}
        styles={{ body: { padding: '40px 32px' } }}
      >
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <ThunderboltOutlined style={{ fontSize: 48, color: colorPrimary, marginBottom: 16 }} />
          <Title level={2} style={{ color: colorText, margin: 0 }}>{t('auth.title')}</Title>
          <Text type="secondary">{t('auth.subtitle')}</Text>
        </div>

        <Form
          onFinish={handleSubmit}
          layout="vertical"
        >
          <Form.Item name="username" rules={[{ required: true, message: t('auth.username_required') }]}>
            <Input
              prefix={<UserOutlined style={{ color: colorTextQuaternary }} />}
              placeholder={t('auth.username')}
              size="large"
            />
          </Form.Item>
          <Form.Item name="password" rules={[{ required: true, message: t('auth.password_required') }]}>
            <Input.Password
              prefix={<LockOutlined style={{ color: colorTextQuaternary }} />}
              placeholder={t('auth.password')}
              size="large"
            />
          </Form.Item>

          <Form.Item>
            <Button type="primary" htmlType="submit" block size="large" loading={loading}>
              {t('auth.sign_in')}
            </Button>
          </Form.Item>

        </Form>

        <div style={{ textAlign: 'center' }}>
          <Text type="secondary" style={{ fontSize: 12 }}>{t('auth.copyright')}</Text>
        </div>
      </Card>
    </div>
  );
};

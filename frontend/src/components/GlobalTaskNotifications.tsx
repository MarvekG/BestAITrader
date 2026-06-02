import React from 'react';
import { App } from 'antd';
import { useTranslation } from 'react-i18next';
import { TaskCompletedMessage, WebSocketMessage } from '../services/websocket';
import { useWebSocketSubscription } from '../hooks/useWebSocketSubscription';

export const GlobalTaskNotifications: React.FC = () => {
  const { t } = useTranslation();
  const { message, notification } = App.useApp();

  useWebSocketSubscription('task_completed', (msg: WebSocketMessage) => {
      const data = (msg as TaskCompletedMessage).data;
      if (!data) {
        return;
      }

      const taskName = data.task_name || t('common.task');
      const taskId = data.task_id;
      const status = data.status;

      if (status === 'running') {
        const result = data.result || {};
        const progress = result.progress;
        const total = result.total;
        const currentStep = result.current_step;
        const progressText = progress !== undefined
          ? total !== undefined ? ` (${progress}/${total})` : ` (${progress})`
          : '';

        message.loading({
          content: `${taskName}: ${currentStep || t('common.processing')}${progressText}`,
          key: taskId || taskName,
          duration: 0,
        });
        return;
      }

      if (taskId) {
        message.destroy(taskId);
      }

      if (status === 'completed' || status === 'success') {
        notification.success({
          message: t('common.task_completed'),
          description: `${taskName} (ID: ${taskId || '-'})`,
          duration: 5,
        });
        return;
      }

      if (status === 'failed' || status === 'error') {
        const errorMessage = data.error_message || data.error || t('common.error');
        notification.error({
          message: t('common.task_failed'),
          description: `${taskName} (ID: ${taskId || '-'})\n${t('common.error')}: ${errorMessage}`,
          duration: 5,
        });
      }
  });

  return null;
};

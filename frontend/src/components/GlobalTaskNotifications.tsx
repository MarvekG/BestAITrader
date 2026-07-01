import React from 'react';
import { App } from 'antd';
import { LoadingOutlined } from '@ant-design/icons';
import axios from 'axios';
import { useTranslation } from 'react-i18next';
import { TaskCompletedMessage, WebSocketMessage } from '../services/websocket';
import { useWebSocketSubscription } from '../hooks/useWebSocketSubscription';
import { tasksApi } from '../api/tasks';

const runningTaskStatuses = new Set(['pending', 'running', 'started']);
const completedTaskStatuses = new Set(['completed', 'success']);
const failedTaskStatuses = new Set(['failed', 'error', 'cancelled']);
const taskStatusPollIntervalMs = 60000;
const taskNotificationPlacement = 'top';
const taskNotificationStyle = { width: 'max-content', maxWidth: 'calc(100vw - 48px)' };

const renderTaskNotificationLine = (text: string) => (
  <span
    style={{
      display: 'block',
      maxWidth: 'calc(100vw - 120px)',
      overflow: 'hidden',
      textOverflow: 'ellipsis',
      whiteSpace: 'nowrap',
    }}
  >
    {text}
  </span>
);

export const GlobalTaskNotifications: React.FC = () => {
  const { t } = useTranslation();
  const { notification } = App.useApp();
  const taskPollTimersRef = React.useRef<Map<string, number>>(new Map());

  const clearTaskPollTimer = React.useCallback((taskId?: string) => {
    if (!taskId) {
      return;
    }

    const timer = taskPollTimersRef.current.get(taskId);
    if (timer !== undefined) {
      window.clearInterval(timer);
      taskPollTimersRef.current.delete(taskId);
    }
  }, []);

  const startTaskStatusPolling = React.useCallback((taskId?: string, taskName?: string) => {
    if (!taskId || taskPollTimersRef.current.has(taskId)) {
      return;
    }

    const timer = window.setInterval(async () => {
      try {
        const task = await tasksApi.getTask(taskId);
        if (runningTaskStatuses.has(task.status)) {
          return;
        }

        clearTaskPollTimer(taskId);
        notification.destroy(taskId);

        if (completedTaskStatuses.has(task.status)) {
          notification.success({
            message: renderTaskNotificationLine(
              `${t('common.task_completed')}: ${task.task_name || taskName || t('common.task')} (ID: ${taskId})`,
            ),
            duration: 5,
            placement: taskNotificationPlacement,
            style: taskNotificationStyle,
          });
          return;
        }

        if (failedTaskStatuses.has(task.status)) {
          notification.error({
            message: renderTaskNotificationLine(
              `${t('common.task_failed')}: ${task.task_name || taskName || t('common.task')} (ID: ${taskId}) - ${t('common.error')}: ${task.error_message || t('common.error')}`,
            ),
            duration: 5,
            placement: taskNotificationPlacement,
            style: taskNotificationStyle,
          });
        }
      } catch (error) {
        if (axios.isAxiosError(error) && error.response?.status === 404) {
          clearTaskPollTimer(taskId);
          notification.destroy(taskId);
          notification.error({
            message: renderTaskNotificationLine(
              `${t('common.task_failed')}: ${taskName || t('common.task')} (ID: ${taskId}) - ${t('common.error')}: ${t('common.error')}`,
            ),
            duration: 5,
            placement: taskNotificationPlacement,
            style: taskNotificationStyle,
          });
          return;
        }

        console.error('Failed to poll task status:', error);
      }
    }, taskStatusPollIntervalMs);

    taskPollTimersRef.current.set(taskId, timer);
  }, [clearTaskPollTimer, notification, t]);

  React.useEffect(() => () => {
    taskPollTimersRef.current.forEach((timer) => window.clearInterval(timer));
    taskPollTimersRef.current.clear();
  }, []);

  useWebSocketSubscription('task_completed', (msg: WebSocketMessage) => {
      const data = (msg as TaskCompletedMessage).data;
      if (!data) {
        return;
      }

      const taskName = data.task_name || t('common.task');
      const taskId = data.task_id;
      const status = data.status;

      if (status && runningTaskStatuses.has(status)) {
        const result = data.result || {};
        const progress = result.progress;
        const total = result.total;
        const currentStep = result.current_step;
        const progressText = progress !== undefined
          ? total !== undefined ? ` (${progress}/${total})` : ` (${progress})`
          : '';

        notification.open({
          icon: <LoadingOutlined />,
          key: taskId || taskName,
          message: renderTaskNotificationLine(`${taskName}: ${currentStep || t('common.processing')}${progressText}`),
          duration: 0,
          placement: taskNotificationPlacement,
          style: taskNotificationStyle,
        });
        startTaskStatusPolling(taskId, taskName);
        return;
      }

      if (taskId) {
        clearTaskPollTimer(taskId);
        notification.destroy(taskId);
      }

      if (status && completedTaskStatuses.has(status)) {
        notification.success({
          message: renderTaskNotificationLine(`${t('common.task_completed')}: ${taskName} (ID: ${taskId || '-'})`),
          duration: 5,
          placement: taskNotificationPlacement,
          style: taskNotificationStyle,
        });
        return;
      }

      if (status && failedTaskStatuses.has(status)) {
        const errorMessage = data.error_message || data.error || t('common.error');
        notification.error({
          message: renderTaskNotificationLine(
            `${t('common.task_failed')}: ${taskName} (ID: ${taskId || '-'}) - ${t('common.error')}: ${errorMessage}`,
          ),
          duration: 5,
          placement: taskNotificationPlacement,
          style: taskNotificationStyle,
        });
      }
  });

  return null;
};

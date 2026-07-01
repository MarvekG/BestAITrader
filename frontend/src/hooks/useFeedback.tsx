import React from 'react';
import { App } from 'antd';
import { CheckCircleOutlined, CloseCircleOutlined, ExclamationCircleOutlined, InfoCircleOutlined, LoadingOutlined } from '@ant-design/icons';

type FeedbackType = 'success' | 'error' | 'warning' | 'info' | 'loading';

type FeedbackArgs = React.ReactNode | {
  content?: React.ReactNode;
  duration?: number;
  key?: React.Key;
};

type FeedbackOptions = {
  content?: React.ReactNode;
  duration?: number;
  key?: React.Key;
};

const feedbackPlacement = 'top' as const;
const feedbackStyle = { width: 'max-content', maxWidth: 'calc(100vw - 48px)' };

const iconByType: Record<FeedbackType, React.ReactNode> = {
  success: <CheckCircleOutlined />,
  error: <CloseCircleOutlined />,
  warning: <ExclamationCircleOutlined />,
  info: <InfoCircleOutlined />,
  loading: <LoadingOutlined />,
};

const renderFeedbackLine = (content: React.ReactNode) => (
  <span className="app-feedback-line">{content}</span>
);

const isFeedbackOptions = (args: FeedbackArgs): args is FeedbackOptions => (
  args !== null
    && args !== undefined
    && typeof args === 'object'
    && !React.isValidElement(args)
    && ('content' in args || 'key' in args || 'duration' in args)
);

const normalizeFeedbackArgs = (args: FeedbackArgs): FeedbackOptions => {
  if (isFeedbackOptions(args)) {
    return args;
  }

  return { content: args };
};

export const useFeedback = () => {
  const { notification } = App.useApp();

  return React.useMemo(() => {
    const open = (type: FeedbackType, args: FeedbackArgs) => {
      const { content, duration, key } = normalizeFeedbackArgs(args);
      const notificationArgs = {
        duration: duration ?? (type === 'loading' ? 0 : 4.5),
        icon: iconByType[type],
        key: key === undefined ? undefined : String(key),
        message: renderFeedbackLine(content),
        placement: feedbackPlacement,
        style: feedbackStyle,
      };

      if (type === 'success') {
        notification.success(notificationArgs);
        return;
      }

      if (type === 'error') {
        notification.error(notificationArgs);
        return;
      }

      if (type === 'warning') {
        notification.warning(notificationArgs);
        return;
      }

      if (type === 'info') {
        notification.info(notificationArgs);
        return;
      }

      notification.open(notificationArgs);
    };

    return {
      destroy: (key?: React.Key) => {
        if (key === undefined) {
          notification.destroy();
          return;
        }

        notification.destroy(String(key));
      },
      error: (args: FeedbackArgs) => open('error', args),
      info: (args: FeedbackArgs) => open('info', args),
      loading: (args: FeedbackArgs) => open('loading', args),
      success: (args: FeedbackArgs) => open('success', args),
      warning: (args: FeedbackArgs) => open('warning', args),
    };
  }, [notification]);
};

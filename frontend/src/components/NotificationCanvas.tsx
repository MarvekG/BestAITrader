import React from 'react';
import { createPortal } from 'react-dom';

type CanvasRect = {
  id: string;
  left: number;
  top: number;
  width: number;
  height: number;
};

const notificationSelector = '.ant-notification';
const noticeSelector = '.ant-notification-notice';

const round = (value: number) => Math.round(value);

const areRectsEqual = (current: CanvasRect[], next: CanvasRect[]) => {
  if (current.length !== next.length) {
    return false;
  }

  return current.every((rect, index) => {
    const candidate = next[index];
    return rect.id === candidate.id
      && rect.left === candidate.left
      && rect.top === candidate.top
      && rect.width === candidate.width
      && rect.height === candidate.height;
  });
};

const getNotificationCanvasRects = (): CanvasRect[] => {
  return Array.from(document.querySelectorAll<HTMLElement>(notificationSelector)).flatMap((container, index) => {
    const notices = Array.from(container.querySelectorAll<HTMLElement>(noticeSelector));
    const visibleRects = notices
      .map((notice) => notice.getBoundingClientRect())
      .filter((rect) => rect.width > 0 && rect.height > 0);

    if (visibleRects.length === 0) {
      return [];
    }

    const left = Math.min(...visibleRects.map((rect) => rect.left));
    const top = Math.min(...visibleRects.map((rect) => rect.top));
    const right = Math.max(...visibleRects.map((rect) => rect.right));
    const bottom = Math.max(...visibleRects.map((rect) => rect.bottom));

    return [{
      id: `${container.className}-${index}`,
      left: round(left),
      top: round(top),
      width: round(right - left),
      height: round(bottom - top),
    }];
  });
};

export const NotificationCanvas: React.FC = () => {
  const [rects, setRects] = React.useState<CanvasRect[]>([]);

  React.useEffect(() => {
    let animationFrame: number | undefined;
    const resizeObserver = typeof ResizeObserver === 'undefined'
      ? undefined
      : new ResizeObserver(() => scheduleMeasure());

    const measure = () => {
      resizeObserver?.disconnect();

      document.querySelectorAll<HTMLElement>(notificationSelector).forEach((container) => {
        resizeObserver?.observe(container);
        container.querySelectorAll<HTMLElement>(noticeSelector).forEach((notice) => {
          resizeObserver?.observe(notice);
        });
      });

      const nextRects = getNotificationCanvasRects();
      setRects((currentRects) => areRectsEqual(currentRects, nextRects) ? currentRects : nextRects);
    };

    function scheduleMeasure() {
      if (animationFrame !== undefined) {
        return;
      }

      animationFrame = window.requestAnimationFrame(() => {
        animationFrame = undefined;
        measure();
      });
    }

    const mutationObserver = new MutationObserver(scheduleMeasure);
    mutationObserver.observe(document.body, {
      attributeFilter: ['class', 'style'],
      attributes: true,
      childList: true,
      subtree: true,
    });

    window.addEventListener('resize', scheduleMeasure);
    scheduleMeasure();

    return () => {
      if (animationFrame !== undefined) {
        window.cancelAnimationFrame(animationFrame);
      }
      mutationObserver.disconnect();
      resizeObserver?.disconnect();
      window.removeEventListener('resize', scheduleMeasure);
    };
  }, []);

  if (rects.length === 0) {
    return null;
  }

  return createPortal(
    <>
      {rects.map((rect) => (
        <div
          className="app-notification-canvas"
          key={rect.id}
          style={{
            height: rect.height,
            left: rect.left,
            top: rect.top,
            width: rect.width,
          }}
        />
      ))}
    </>,
    document.body,
  );
};

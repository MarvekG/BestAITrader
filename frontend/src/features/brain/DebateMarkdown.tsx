import React from 'react';
import ReactMarkdown from 'react-markdown';
import type { Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import './debateMarkdown.css';

interface DebateMarkdownProps {
  content: string;
  className?: string;
  style?: React.CSSProperties;
}

const markdownComponents: Components = {
  table: ({ children, node, ...props }) => {
    void node;
    return (
      <div className="debate-markdown-table-scroll">
        <table {...props}>{children}</table>
      </div>
    );
  },
};

export const DebateMarkdown: React.FC<DebateMarkdownProps> = ({ content, className, style }) => {
  const classes = ['debate-markdown', className].filter(Boolean).join(' ');

  return (
    <div className={classes} style={style}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
        {content}
      </ReactMarkdown>
    </div>
  );
};

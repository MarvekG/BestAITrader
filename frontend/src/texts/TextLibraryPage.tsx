import React, { useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { textItems, type TextItem } from './textCatalog';
import './TextLibraryPage.css';

export const TextLibraryPage: React.FC = () => {
  const initialTextId = textItems[0]?.id ?? '';
  const [selectedTextId, setSelectedTextId] = useState(initialTextId);
  const readerBodyRef = useRef<HTMLDivElement>(null);

  const selectedText = useMemo<TextItem | undefined>(
    () => textItems.find((textItem) => textItem.id === selectedTextId) ?? textItems[0],
    [selectedTextId]
  );

  useEffect(() => {
    readerBodyRef.current?.scrollTo({ top: 0 });
  }, [selectedText?.id]);

  if (!selectedText) {
    return <div className="text-empty">No files.</div>;
  }

  return (
    <div className="text-library-page">
      <section className="text-reader" aria-label="Content">
        <header className="text-reader-header">
          <h1>{selectedText.title}</h1>
        </header>

        <div className="text-reader-body" ref={readerBodyRef}>
          <article className="text-reader-markdown">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{selectedText.markdown}</ReactMarkdown>
          </article>
        </div>
      </section>

      <aside className="text-list-panel" aria-label="Files">
        {textItems.map((textItem) => {
          const isActive = textItem.id === selectedText.id;

          return (
            <button
              aria-pressed={isActive}
              className={`text-list-button${isActive ? ' is-active' : ''}`}
              key={textItem.id}
              onClick={() => setSelectedTextId(textItem.id)}
              type="button"
            >
              {textItem.title}
            </button>
          );
        })}
      </aside>
    </div>
  );
};

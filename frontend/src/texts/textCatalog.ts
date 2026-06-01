export type TextItem = {
  id: string;
  fileName: string;
  title: string;
  markdown: string;
};

const contentModules = import.meta.glob('./content/*.md', {
  eager: true,
  import: 'default',
  query: '?raw',
});

const getFileName = (path: string): string => path.split('/').pop() ?? path;

const getTitle = (fileName: string): string => fileName.replace(/\.md$/i, '');

export const textItems: TextItem[] = Object.entries(contentModules)
  .map(([path, markdown]) => {
    const fileName = getFileName(path);

    return {
      id: fileName,
      fileName,
      title: getTitle(fileName),
      markdown: String(markdown),
    };
  })
  .sort((left, right) => left.fileName.localeCompare(right.fileName, undefined, { numeric: true }));

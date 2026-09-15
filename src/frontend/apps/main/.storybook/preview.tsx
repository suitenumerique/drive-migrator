import type { Preview } from '@storybook/nextjs-vite';

import '../src/app/global.scss';
import { AppProvider } from '@/core/AppProvider';

const preview: Preview = {
  parameters: {
    nextjs: {
      appDirectory: true,
    },
    controls: {
      matchers: {
        color: /(background|color)$/i,
        date: /Date$/i,
      },
    },
  },
  decorators: [
    (Story) => (
      <AppProvider>
        <Story />
      </AppProvider>
    ),
  ],
};

export default preview;

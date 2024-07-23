import type { Preview } from '@storybook/react';

import "../src/app/global.scss"
import { AppProvider } from '@/core/AppProvider';


const preview: Preview = {
  parameters: {
    controls: {
      matchers: {
        color: /(background|color)$/i,
        date: /Date$/i,
      },
    },
  },
  decorators: [
    (Story) => (
      <AppProvider><Story/></AppProvider>
    )
  ]
};

export default preview;

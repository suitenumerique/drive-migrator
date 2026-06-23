'use client';

import { CunninghamProvider } from '@gouvfr-lasuite/cunningham-react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import dynamic from 'next/dynamic';
import { useState } from 'react';

import { Support } from '@/components/Support/Support';
import { getFrontendTheme } from '@/cunningham';
import '@/i18n/initI18n';

import { Auth } from './auth/Auth';

const ReactQueryDevtools =
  process.env.NODE_ENV === 'development'
    ? dynamic(
        () =>
          import('@tanstack/react-query-devtools').then(
            (mod) => mod.ReactQueryDevtools,
          ),
        { ssr: false },
      )
    : () => null;

export function AppProvider({ children }: { children: React.ReactNode }) {
  const theme = getFrontendTheme();
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 1000 * 60 * 3,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <CunninghamProvider theme={theme}>
        <Auth>
          <Support>{children}</Support>
        </Auth>
      </CunninghamProvider>
      <ReactQueryDevtools initialIsOpen={false} buttonPosition="bottom-right" />
    </QueryClientProvider>
  );
}

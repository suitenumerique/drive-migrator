'use client';

import dynamic from 'next/dynamic';
import { PropsWithChildren } from 'react';

import { AppHelpMenu } from '@/components/AppHelpMenu/AppHelpMenu';
import { Header } from '@/components/Header/Header';

const AppProvider = dynamic(
  () =>
    import('@/core/AppProvider').then((mod) => ({ default: mod.AppProvider })),
  { ssr: false },
);

export default function SubLayout({ children }: PropsWithChildren) {
  return (
    <AppProvider>
      <div className="suite__app">
        <Header />
        <main>{children}</main>
        <AppHelpMenu />
      </div>
    </AppProvider>
  );
}

'use client';
import { PropsWithChildren } from 'react';

import { AppHelpMenu } from '@/components/AppHelpMenu/AppHelpMenu';
import { Header } from '@/components/Header/Header';
import { AppProvider } from '@/core/AppProvider';

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

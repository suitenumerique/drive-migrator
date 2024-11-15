'use client';
import { PropsWithChildren } from 'react';

import { Footer } from '@/components/Footer/Footer';
import { Header } from '@/components/Header/Header';
import { AppProvider } from '@/core/AppProvider';
import { useSupport } from '@/hooks/useSupport';

export default function SubLayout({ children }: PropsWithChildren) {
  useSupport();

  return (
    <AppProvider>
      <div className="suite__app">
        <Header />
        <main>{children}</main>
        <Footer />
      </div>
    </AppProvider>
  );
}

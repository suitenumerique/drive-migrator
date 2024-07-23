'use client';
import { PropsWithChildren } from 'react';

import { Header } from '@/components/Header/Header';
import { AppProvider } from '@/core/AppProvider';
import { Footer } from '@/components/Footer/Footer';

export default function SubLayout({ children }: PropsWithChildren) {
  return (
    <AppProvider>
      <div className="suite__app">
        <Header />
        <main>{children}</main>
        <Footer/>
      </div>
    </AppProvider>
  );
}

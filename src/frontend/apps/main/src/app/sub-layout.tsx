'use client';
import { PropsWithChildren } from 'react';

import { AppProvider } from '@/core/AppProvider';

export default function SubLayout({ children }: PropsWithChildren) {
  return <AppProvider>{children}</AppProvider>;
}

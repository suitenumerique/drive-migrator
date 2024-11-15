import { PropsWithChildren } from 'react';

import { useSupport } from '@/hooks/useSupport';

export const Support = ({ children }: PropsWithChildren) => {
  useSupport();
  return <>{children}</>;
};

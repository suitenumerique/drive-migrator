import { Crisp } from 'crisp-sdk-web';
import { useEffect } from 'react';

import { useAuth } from '@/core/auth/Auth';

export function useSupport() {
  const { user } = useAuth();

  useEffect(() => {
    if (!user) {
      return;
    }

    Crisp.configure('f77a2160-eda9-4a62-b1cb-b73b031c3da3');
    Crisp.session.setSegments(['adm:migration'], true);
    Crisp.setTokenId(`migration-tool-${user.id}`);
    Crisp.user.setEmail(user.email);
  }, [user]);
}

export const terminateSupportSession = () => {
  if (!Crisp.isCrispInjected()) {
    return;
  }
  Crisp.setTokenId();
  Crisp.session.reset();
};

import { useCallback, useState } from 'react';

import { fetchAPI } from '@/api/fetchApi';

interface ResanaAuthStatus {
  connected: boolean;
  expires_at: string | null;
}

export const useResanaAuthStatus = () => {
  const [connected, setConnected] = useState<boolean | null>(null);

  const check = useCallback(async () => {
    const response = await fetchAPI('resana/auth/status', undefined, {
      logoutOn401: false,
    });

    if (!response.ok) {
      setConnected(false);
      return;
    }

    const data = (await response.json()) as ResanaAuthStatus;
    setConnected(data.connected);
  }, []);

  const markConnected = useCallback(() => setConnected(true), []);

  const reset = useCallback(() => setConnected(null), []);

  return { connected, check, markConnected, reset };
};

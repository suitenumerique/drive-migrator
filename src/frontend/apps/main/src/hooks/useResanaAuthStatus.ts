import { useCallback, useState } from 'react';

import { fetchAPI } from '@/api/fetchApi';

interface ResanaAuthStatus {
  connected: boolean;
  expires_at: string | null;
}

export const useResanaAuthStatus = () => {
  const [connected, setConnected] = useState<boolean | null>(null);

  const check = useCallback(async () => {
    try {
      const response = await fetchAPI('resana/auth/status', undefined, {
        logoutOn401: false,
      });

      if (!response.ok) {
        setConnected(false);
        return false;
      }

      const data = (await response.json()) as ResanaAuthStatus;
      setConnected(data.connected);
      return data.connected;
    } catch (error) {
      console.error(error);
      setConnected(false);
      return false;
    }
  }, []);

  const markConnected = useCallback(() => setConnected(true), []);

  const reset = useCallback(() => setConnected(null), []);

  return { connected, check, markConnected, reset };
};

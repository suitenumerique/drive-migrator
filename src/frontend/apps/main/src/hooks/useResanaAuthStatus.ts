import { useState } from 'react';

import { fetchAPI } from '@/api/fetchApi';

interface ResanaAuthStatus {
  connected: boolean;
  expires_at: string | null;
}

export const useResanaAuthStatus = () => {
  const [connected, setConnected] = useState<boolean | null>(null);

  const check = async () => {
    const response = await fetchAPI('resana/auth/status', undefined, {
      logoutOn401: false,
    });
    if (response.ok) {
      const data = (await response.json()) as ResanaAuthStatus;
      setConnected(data.connected);
    }
  };

  const markConnected = () => setConnected(true);

  return { connected, check, markConnected };
};

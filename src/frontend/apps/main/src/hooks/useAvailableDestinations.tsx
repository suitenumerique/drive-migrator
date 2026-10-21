import { useEffect, useState } from 'react';

import { useApi } from '@/hooks/useApi';

export interface Destination {
  name: string;
  label: string;
  account_policy: 'none' | 'auto_created' | 'user_created';
  account_creation_url: string | null;
}

export const useAvailableDestinations = (enabled = true) => {
  const { fetchApi } = useApi();
  const [destinations, setDestinations] = useState<Destination[] | undefined>();
  const [hasFailed, setHasFailed] = useState(false);

  const fetchDestinations = async () => {
    try {
      const response = await fetchApi('available-destinations/');
      const data = (await response.json()) as Destination[];
      setDestinations(data);
      setHasFailed(false);
    } catch {
      // fetchApi already shows the error.
      setHasFailed(true);
    }
  };

  useEffect(() => {
    if (!enabled) {
      return;
    }
    void fetchDestinations();
  }, [enabled]);

  return { destinations, hasFailed, refetch: fetchDestinations };
};

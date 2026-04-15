import { useEffect, useState } from 'react';

import { useApi } from '@/hooks/useApi';

export interface Destination {
  name: string;
  label: string;
}

export const useAvailableDestinations = () => {
  const { fetchApi } = useApi();
  const [destinations, setDestinations] = useState<Destination[] | undefined>();

  const fetchDestinations = async () => {
    const response = await fetchApi('available-destinations/');
    const data = (await response.json()) as Destination[];
    setDestinations(data);
  };

  useEffect(() => {
    void fetchDestinations();
  }, []);

  return { destinations };
};

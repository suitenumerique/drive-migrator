import { useEffect, useState } from 'react';

import { useApi } from '@/hooks/useApi';

export enum FeatureFlags {
  ALLOW_NEW_TASKS = 'allow-new-tasks',
  READ_ONLY_MODE = 'read-only-mode',
}

export const useFeatureFlags = () => {
  const { fetchApi } = useApi();
  const [flags, setFlags] = useState<
    Record<FeatureFlags, boolean> | undefined
  >();

  const fetchFeatureFlags = async () => {
    const response = await fetchApi('feature-flags');
    const data = (await response.json()) as Record<FeatureFlags, boolean>;
    console.log('data', data);
    setFlags(data);
  };

  useEffect(() => {
    void fetchFeatureFlags();
  }, []);

  return {
    flags,
  };
};

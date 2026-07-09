import { useEffect, useState } from 'react';

import { useApi } from '@/hooks/useApi';

interface MigrationConfig {
  file_limit_per_workspace: number;
}

export const useMigrationConfig = () => {
  const { fetchApi } = useApi();
  const [config, setConfig] = useState<MigrationConfig | undefined>();

  const fetchMigrationConfig = async () => {
    const response = await fetchApi('migration-config');
    const data = (await response.json()) as MigrationConfig;
    setConfig(data);
  };

  useEffect(() => {
    void fetchMigrationConfig();
  }, []);

  return {
    fileLimitPerWorkspace: config?.file_limit_per_workspace ?? 0,
  };
};

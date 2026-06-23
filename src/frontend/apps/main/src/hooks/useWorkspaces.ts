import { useCallback, useState } from 'react';

import { Workspace, WorkspaceStatus } from '@/components/Workspace/Workspace';
import { useApi } from '@/hooks/useApi';

export const useWorkspaces = () => {
  const [workspaces, setWorkspaces] = useState<Workspace[]>();
  const [workspacesByStatus, setWorkspacesByStatus] =
    useState<Record<WorkspaceStatus, Workspace[]>>();

  const { fetchApi, hasError } = useApi();

  const fetchWorkspaces = useCallback(
    async (filters: { ids?: string[] } = {}) => {
      const searchParams = new URLSearchParams();
      if (filters.ids) {
        filters.ids.forEach((id) => searchParams.append('id', id));
      }

      const response = await fetchApi(
        'workspaces?' + searchParams.toString(),
        undefined,
        {
          closableError: false,
        },
      );
      const data = (await response.json()) as { results: Workspace[] };
      return data.results;
    },
    [fetchApi],
  );

  const applyWorkspaces = useCallback((nextWorkspaces: Workspace[]) => {
    setWorkspaces(nextWorkspaces);
    setWorkspacesByStatus(getWorkspacesByStatus(nextWorkspaces));
  }, []);

  const synchronize = useCallback(async () => {
    await fetchApi('synchronize/', undefined, {
      closableError: true,
    });
  }, [fetchApi]);

  const fetch = useCallback(
    async (
      filters: { ids?: string[] } = {},
      options: { syncIfEmpty?: boolean } = {},
    ) => {
      let nextWorkspaces = await fetchWorkspaces(filters);

      if (options.syncIfEmpty && nextWorkspaces.length === 0) {
        try {
          await synchronize();
          nextWorkspaces = await fetchWorkspaces(filters);
        } catch {
          // Keep the empty list; useApi already surfaced the error.
        }
      }

      applyWorkspaces(nextWorkspaces);
    },
    [applyWorkspaces, fetchWorkspaces, synchronize],
  );

  return { workspaces, workspacesByStatus, fetch, synchronize, hasError };
};

const getWorkspacesByStatus = (workspaces: Workspace[]) => {
  return {
    [WorkspaceStatus.NONE]: workspaces.filter(
      (workspace) => workspace.status === WorkspaceStatus.NONE,
    ),
    [WorkspaceStatus.PENDING]: workspaces.filter(
      (workspace) => workspace.status === WorkspaceStatus.PENDING,
    ),
    [WorkspaceStatus.SUCCESS]: workspaces.filter(
      (workspace) => workspace.status === WorkspaceStatus.SUCCESS,
    ),
    [WorkspaceStatus.FAILURE]: workspaces.filter(
      (workspace) => workspace.status === WorkspaceStatus.FAILURE,
    ),
  };
};

import { useState } from 'react';

import { Workspace, WorkspaceStatus } from '@/components/Workspace/Workspace';
import { useApi } from '@/hooks/useApi';

export const useWorkspaces = () => {
  const [workspaces, setWorkspaces] = useState<Workspace[]>();
  const [workspacesByStatus, setWorkspacesByStatus] =
    useState<Record<WorkspaceStatus, Workspace[]>>();

  const { fetchApi, hasError } = useApi();

  const fetch = async (filters: { ids?: string[] } = {}) => {
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
    const workspaces = data.results;
    setWorkspaces(workspaces);
    setWorkspacesByStatus(getWorkspacesByStatus(workspaces));
  };

  return { workspaces, workspacesByStatus, fetch, hasError };
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

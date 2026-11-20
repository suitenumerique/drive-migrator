export type WorkspaceRow = {
  id: string;
  title: string;
  status: string;
  queue: 'running' | 'queued' | null;
  target: 'drive' | 'archive' | null;
  connected_count: number;
  managers_count: number;
  source_files_count: number | null;
  migrated_files_count: number | null;
  migrated_at: string | null;
};

export type WorkspacePage = {
  count: number;
  results: WorkspaceRow[];
};

export type OrganizationStats = {
  last_sync: string | null;
  resana: number;
  connected: number;
  not_migrated: number;
  locked: number;
  drive: number;
  archive: number;
  pending: number;
  failure: number;
};

export type KpiFilter = {
  status?: string;
  target?: string;
  connected?: string;
};

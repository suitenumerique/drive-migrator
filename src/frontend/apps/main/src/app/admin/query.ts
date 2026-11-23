export type ListFilters = {
  search: string;
  status: string | null;
  target: string | null;
  connected: string | null;
  ordering: string;
  page: number;
  pageSize: number;
};

export const EMPTY_FILTERS: ListFilters = {
  search: '',
  status: null,
  target: null,
  connected: null,
  ordering: 'title',
  page: 1,
  pageSize: 50,
};

export const PAGE_SIZES = [25, 50, 100];

export const STATUS_OPTIONS = [
  { value: 'not_migrated', label: 'Non migré' },
  { value: 'NONE', label: 'À migrer' },
  { value: 'PENDING', label: 'En cours' },
  { value: 'migrated', label: 'Migré' },
  { value: 'GAP', label: 'Migré avec écarts' },
  { value: 'FAILURE', label: 'Échec' },
];

const STATUS_VIEWS: Record<
  string,
  { label: string; icon: string; tone: string }
> = {
  NONE: {
    label: 'À migrer',
    icon: 'radio_button_unchecked',
    tone: 'gray',
  },
  SUCCESS: { label: 'Migré', icon: 'check_circle', tone: 'green' },
  GAP: {
    label: 'Migré avec écarts',
    icon: 'warning_amber',
    tone: 'orange',
  },
  FAILURE: { label: 'Échec', icon: 'cancel', tone: 'red' },
  LOCKED: {
    label: 'Verrouillé dans Resana',
    icon: 'inventory_2',
    tone: 'purple',
  },
};

export function statusView(
  status: string,
  queue: string | null,
): { label: string; icon: string; tone: string } {
  if (status === 'PENDING' && queue === 'queued') {
    return {
      label: "En file d'attente",
      icon: 'hourglass_empty',
      tone: 'blue-1',
    };
  }
  if (status === 'PENDING') {
    return { label: 'En cours', icon: 'schedule', tone: 'blue-2' };
  }
  return (
    STATUS_VIEWS[status] ?? {
      label: status,
      icon: 'help',
      tone: 'gray',
    }
  );
}

export function showsTarget(status: string): boolean {
  return ['SUCCESS', 'GAP', 'PENDING', 'FAILURE'].includes(status);
}

export function isListFiltered(filters: ListFilters): boolean {
  return Boolean(
    filters.search || filters.status || filters.target || filters.connected,
  );
}

export function listQuery(organization: string, filters: ListFilters): string {
  const params = new URLSearchParams();
  params.set('organization', organization);
  params.set('ordering', filters.ordering);
  params.set('page', String(filters.page));
  params.set('page_size', String(filters.pageSize));
  if (filters.search) {
    params.set('search', filters.search);
  }
  if (filters.status) {
    params.set('status', filters.status);
  }
  if (filters.target) {
    params.set('target', filters.target);
  }
  if (filters.connected) {
    params.set('connected', filters.connected);
  }
  return `admin/workspaces?${params.toString()}`;
}

export function nextOrdering(current: string, field: string): string {
  if (current === field) {
    return `-${field}`;
  }
  return field;
}

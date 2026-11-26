import { render, screen, waitFor } from '@testing-library/react';

import { fetchAPI } from '@/api/fetchApi';
import { AdminOrganization } from '@/hooks/useAdminContext';

import { AdminBoard } from './AdminBoard';

jest.mock('@/api/fetchApi', () => ({ fetchAPI: jest.fn() }));
jest.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'fr' },
  }),
}));
jest.mock('@gouvfr-lasuite/cunningham-react', () => ({
  Alert: ({ children }: { children: React.ReactNode }) => (
    <div role="alert">{children}</div>
  ),
  Button: ({
    children,
    onClick,
  }: {
    children: React.ReactNode;
    onClick?: () => void;
  }) => (
    <button type="button" onClick={onClick}>
      {children}
    </button>
  ),
  Input: () => <input />,
  Loader: () => <div>loading</div>,
  Pagination: () => null,
  Tooltip: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  VariantType: { ERROR: 'error' },
}));
jest.mock('@gouvfr-lasuite/ui-kit', () => ({
  Filter: () => null,
}));
jest.mock('./ManagersModal', () => ({
  ManagersModal: () => null,
}));

const mockFetchAPI = fetchAPI as jest.MockedFunction<typeof fetchAPI>;

const organization = (id: string): AdminOrganization => ({
  id,
  name: id,
  last_sync: null,
});

const stats = {
  last_sync: null,
  resana: 1,
  connected: 0,
  not_migrated: 1,
  locked: 0,
  drive: 0,
  archive: 0,
  pending: 0,
  failure: 0,
};

const row = (title: string) => ({
  id: title,
  title,
  status: 'NONE',
  queue: null,
  target: null,
  connected_count: 0,
  managers_count: 1,
  source_files_count: 1,
  migrated_files_count: null,
  migrated_at: null,
});

const response = (status: number, body: object) =>
  ({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  }) as Response;

const board = (organizationId: string) => (
  <AdminBoard
    organizations={[organization('org-1'), organization('org-2')]}
    organizationId={organizationId}
    onOrganizationChange={jest.fn()}
    onSyncRequest={jest.fn()}
    syncing={false}
    syncFeedback={{ kind: 'idle' }}
    onDismissSyncFeedback={jest.fn()}
    dataRevision={0}
  />
);

describe('AdminBoard', () => {
  beforeEach(() => {
    mockFetchAPI.mockReset();
  });

  it('shows an error instead of an empty list when the workspaces call fails', async () => {
    mockFetchAPI.mockImplementation((url) => {
      if (String(url).startsWith('admin/stats')) {
        return Promise.resolve(response(200, stats));
      }
      return Promise.resolve(response(500, {}));
    });

    render(board('org-1'));

    expect(
      await screen.findByText(
        'Le service est momentanément indisponible, veuillez réessayer dans quelques instants.',
      ),
    ).toBeInTheDocument();
    expect(
      screen.queryByText('Aucun espace ne correspond à ces critères.'),
    ).not.toBeInTheDocument();
  });

  it('hides the previous organization rows before the next list arrives', async () => {
    let resolveNext: ((value: Response) => void) | undefined;
    mockFetchAPI.mockImplementation((url) => {
      const path = String(url);
      if (path.startsWith('admin/stats')) {
        return Promise.resolve(response(200, stats));
      }
      if (path.includes('organization=org-1')) {
        return Promise.resolve(
          response(200, { count: 1, results: [row('Ancien')] }),
        );
      }
      return new Promise<Response>((resolve) => {
        resolveNext = resolve;
      });
    });

    const { rerender } = render(board('org-1'));
    expect(await screen.findByText('Ancien')).toBeInTheDocument();

    rerender(board('org-2'));

    await waitFor(() => {
      expect(screen.queryByText('Ancien')).not.toBeInTheDocument();
    });
    expect(resolveNext).toBeDefined();
  });
});

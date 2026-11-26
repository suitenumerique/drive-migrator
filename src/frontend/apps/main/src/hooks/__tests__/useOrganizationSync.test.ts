import { act, renderHook } from '@testing-library/react';

import { fetchAPI } from '@/api/fetchApi';
import { AdminOrganization } from '@/hooks/useAdminContext';

import { useOrganizationSync } from '../useOrganizationSync';

jest.mock('@/api/fetchApi', () => ({ fetchAPI: jest.fn() }));
jest.mock('@/core/conf', () => ({ adminOrgSyncTimeoutMs: () => 900_000 }));

const mockFetchAPI = fetchAPI as jest.MockedFunction<typeof fetchAPI>;

const organizations: AdminOrganization[] = [
  { id: 'org-a', name: 'Alpha', last_sync: null },
  { id: 'org-b', name: 'Beta', last_sync: null },
];

const ready = {
  kind: 'ready' as const,
  data: {
    is_organization_admin: true,
    features: { admin_reporting: true },
    user: { name: null, email: 'a@example.com' },
    organizations,
  },
};

describe('useOrganizationSync', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    mockFetchAPI.mockReset();
    mockFetchAPI.mockResolvedValue({
      ok: true,
      status: 202,
      json: () => Promise.resolve({ task_id: 'task-1' }),
    } as Response);
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('drops an in-flight sync when the organization changes', async () => {
    const refreshContext = jest.fn().mockResolvedValue(ready);
    const { result, rerender } = renderHook(
      ({ organizationId }) =>
        useOrganizationSync({
          organizationId,
          organizations,
          refreshContext,
          onSyncComplete: jest.fn(),
        }),
      { initialProps: { organizationId: 'org-a' } },
    );

    await act(async () => {
      await result.current.startSync();
    });
    expect(result.current.feedback.kind).toBe('syncing');

    rerender({ organizationId: 'org-b' });

    expect(result.current.feedback.kind).toBe('idle');

    await act(async () => {
      await result.current.startSync();
    });

    expect(mockFetchAPI).toHaveBeenCalledTimes(2);
    expect(String(mockFetchAPI.mock.calls[1][0])).toContain(
      'admin/organizations/org-b/synchronize',
    );
  });
});

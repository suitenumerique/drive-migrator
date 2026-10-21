import { act, renderHook } from '@testing-library/react';

import { fetchAPI } from '@/api/fetchApi';

import { useDestinationAccountStatus } from '../useDestinationAccountStatus';

jest.mock('@/api/fetchApi', () => ({ fetchAPI: jest.fn() }));
jest.mock('@/core/auth/Auth', () => ({ login: jest.fn() }));

const mockFetchAPI = fetchAPI as jest.MockedFunction<typeof fetchAPI>;

const mockResponse = (status: number, body: object) =>
  ({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  }) as Response;

describe('useDestinationAccountStatus', () => {
  beforeEach(() => {
    mockFetchAPI.mockReset();
    sessionStorage.clear();
  });

  it.each([true, false])(
    'forgets the last account status (%s) once the session has expired',
    async (lastExists) => {
      const { result } = renderHook(() => useDestinationAccountStatus('drive'));

      mockFetchAPI.mockResolvedValueOnce(
        mockResponse(200, { exists: lastExists }),
      );
      await act(() => result.current.check());
      expect(result.current.exists).toBe(lastExists);

      // A relogin was just tried, so the hook gives up with session_expired.
      sessionStorage.setItem('destination_account_relogin', String(Date.now()));
      mockFetchAPI.mockResolvedValueOnce(mockResponse(401, {}));
      await act(() => result.current.check());

      expect(result.current.error).toBe('session_expired');
      expect(result.current.exists).toBeNull();
    },
  );
});

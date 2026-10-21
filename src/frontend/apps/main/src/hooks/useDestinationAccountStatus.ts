import { useCallback, useRef, useState } from 'react';

import { fetchAPI } from '@/api/fetchApi';
import { login } from '@/core/auth/Auth';

const DRIVE_TOKEN_REQUIRED = 'DriveTokenRequired';
const RELOGIN_STORAGE_KEY = 'destination_account_relogin';
const RELOGIN_DELAY_MS = 5 * 60 * 1000;

export type AccountStatusError = 'unavailable' | 'session_expired';

interface DestinationAccountStatus {
  exists?: boolean;
  error_name?: string;
}

export const useDestinationAccountStatus = (destination: string) => {
  const [exists, setExists] = useState<boolean | null>(null);
  const [error, setError] = useState<AccountStatusError | null>(null);
  // Only the latest check counts, and the one in flight is shared by focus and
  // visibility changes, which fire together.
  const lastCheck = useRef(0);
  const inFlight = useRef<Promise<void> | null>(null);

  const relogin = useCallback(() => {
    // At most once every few minutes: a token logging in again cannot renew
    // would loop.
    const lastRelogin = Number(sessionStorage.getItem(RELOGIN_STORAGE_KEY));
    if (lastRelogin && Date.now() - lastRelogin < RELOGIN_DELAY_MS) {
      // The last status no longer holds and must not hide the logout action.
      setExists(null);
      setError('session_expired');
      return;
    }
    sessionStorage.setItem(RELOGIN_STORAGE_KEY, String(Date.now()));
    login(`${window.location.pathname}${window.location.search}`);
  }, []);

  const runCheck = useCallback(
    async (checkId: number) => {
      try {
        const response = await fetchAPI(
          `available-destinations/${destination}/account-status/`,
          undefined,
          { logoutOn401: false },
        );
        const data = (await response.json()) as DestinationAccountStatus;
        if (checkId !== lastCheck.current) {
          return;
        }

        if (response.ok) {
          sessionStorage.removeItem(RELOGIN_STORAGE_KEY);
          setExists(Boolean(data.exists));
          setError(null);
          return;
        }
        // Not a missing account: the session or the token must be renewed.
        if (
          response.status === 401 ||
          data.error_name === DRIVE_TOKEN_REQUIRED
        ) {
          relogin();
          return;
        }
        setError('unavailable');
      } catch (fetchError) {
        console.error(fetchError);
        if (checkId === lastCheck.current) {
          setError('unavailable');
        }
      }
    },
    [destination, relogin],
  );

  const check = useCallback(() => {
    if (!inFlight.current) {
      const current = runCheck(++lastCheck.current).finally(() => {
        if (inFlight.current === current) {
          inFlight.current = null;
        }
      });
      inFlight.current = current;
    }
    return inFlight.current;
  }, [runCheck]);

  const reset = useCallback(() => {
    lastCheck.current += 1;
    inFlight.current = null;
    setExists(null);
    setError(null);
  }, []);

  return { exists, error, check, reset };
};

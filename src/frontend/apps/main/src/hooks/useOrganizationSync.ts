import { useCallback, useEffect, useRef, useState } from 'react';

import { fetchAPI } from '@/api/fetchApi';
import { lastSyncAdvanced } from '@/app/admin/sync';
import { AdminContextResult, AdminOrganization } from '@/hooks/useAdminContext';

const POLL_MS = 5000;
const TIMEOUT_MS = 120000;

export type SyncFeedback =
  | { kind: 'idle' }
  | { kind: 'syncing' }
  | { kind: 'success' }
  | { kind: 'timeout' }
  | { kind: 'error'; messageKey: string };

type Options = {
  organizationId: string;
  organizations: AdminOrganization[];
  refreshContext: () => Promise<AdminContextResult>;
  onSyncComplete: () => void;
};

export function useOrganizationSync({
  organizationId,
  organizations,
  refreshContext,
  onSyncComplete,
}: Options) {
  const [feedback, setFeedback] = useState<SyncFeedback>({ kind: 'idle' });
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inFlightRef = useRef(false);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
  }, []);

  useEffect(() => () => stopPolling(), [stopPolling]);

  const pollContext = useCallback(
    async (baseline: string | null) => {
      const next = await refreshContext();
      if (next.kind !== 'ready') {
        return;
      }
      const current = next.data.organizations.find(
        (item) => item.id === organizationId,
      );
      if (lastSyncAdvanced(baseline, current?.last_sync)) {
        stopPolling();
        inFlightRef.current = false;
        setFeedback({ kind: 'success' });
        onSyncComplete();
      }
    },
    [onSyncComplete, organizationId, refreshContext, stopPolling],
  );

  const startSync = useCallback(async () => {
    if (inFlightRef.current) {
      return;
    }
    inFlightRef.current = true;
    const org = organizations.find((item) => item.id === organizationId);
    const baseline = org?.last_sync ?? null;
    setFeedback({ kind: 'syncing' });
    const response = await fetchAPI(
      `admin/organizations/${encodeURIComponent(organizationId)}/synchronize`,
      { method: 'POST' },
      { logoutOn401: false },
    );
    if (response.status === 403) {
      const body = (await response.json()) as { error_name?: string };
      const messageKey =
        body.error_name === 'FeatureNotEnabled'
          ? "L'administration n'est pas disponible."
          : body.error_name === 'NotOrganizationAdmin'
            ? 'Vous n’administrez pas cette organisation.'
            : 'Le service est momentanément indisponible, veuillez réessayer dans quelques instants.';
      inFlightRef.current = false;
      setFeedback({ kind: 'error', messageKey });
      return;
    }
    if (!response.ok) {
      inFlightRef.current = false;
      setFeedback({
        kind: 'error',
        messageKey:
          'Le service est momentanément indisponible, veuillez réessayer dans quelques instants.',
      });
      return;
    }
    stopPolling();
    pollRef.current = setInterval(() => {
      void pollContext(baseline);
    }, POLL_MS);
    timeoutRef.current = setTimeout(() => {
      stopPolling();
      inFlightRef.current = false;
      setFeedback({ kind: 'timeout' });
    }, TIMEOUT_MS);
    void pollContext(baseline);
  }, [organizationId, organizations, pollContext, stopPolling]);

  return {
    syncing: feedback.kind === 'syncing',
    feedback,
    dismissFeedback: () => setFeedback({ kind: 'idle' }),
    startSync,
  };
}

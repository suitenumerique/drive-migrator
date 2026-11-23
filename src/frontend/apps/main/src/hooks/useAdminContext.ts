import { useCallback, useEffect, useState } from 'react';

import { fetchAPI } from '@/api/fetchApi';
import { useAuth } from '@/core/auth/Auth';

export type AdminOrganization = {
  id: string;
  name: string;
  last_sync: string | null;
};

export type AdminContextData = {
  is_organization_admin: boolean;
  features: { admin_reporting: boolean };
  user: { name: string | null; email: string };
  organizations: AdminOrganization[];
};

export type AdminContextResult =
  | { kind: 'ready'; data: AdminContextData }
  | { kind: 'disabled' }
  | { kind: 'error' };

let cached: AdminContextResult | null = null;

export function resetAdminContext(): void {
  cached = null;
}

export async function loadAdminContext(options?: {
  force?: boolean;
}): Promise<AdminContextResult> {
  if (!options?.force && cached) {
    return cached;
  }
  const response = await fetchAPI('admin/context', undefined, {
    logoutOn401: false,
  });
  if (response.status === 403) {
    const body = (await response.json()) as { error_name?: string };
    const result: AdminContextResult =
      body.error_name === 'FeatureNotEnabled'
        ? { kind: 'disabled' }
        : { kind: 'error' };
    if (result.kind === 'disabled') {
      cached = result;
    }
    return result;
  }
  if (!response.ok) {
    return { kind: 'error' };
  }
  const data = (await response.json()) as AdminContextData;
  cached = { kind: 'ready', data };
  return cached;
}

export function useAdminContext() {
  const { isAuthenticated, isAuthPending } = useAuth();
  const [result, setResult] = useState<AdminContextResult | null>(cached);
  const [loading, setLoading] = useState(!cached);

  useEffect(() => {
    if (isAuthPending) {
      return;
    }
    if (!isAuthenticated) {
      resetAdminContext();
      setResult(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(cached === null);
    void loadAdminContext().then((next) => {
      if (!cancelled) {
        setResult(next);
        setLoading(false);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [isAuthenticated, isAuthPending]);

  const refresh = useCallback(async () => {
    resetAdminContext();
    setLoading(true);
    const next = await loadAdminContext({ force: true });
    setResult(next);
    setLoading(false);
    return next;
  }, []);

  return { result, loading: isAuthPending || loading, refresh };
}

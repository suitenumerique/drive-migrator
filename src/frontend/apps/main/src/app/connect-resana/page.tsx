'use client';

import { Loader } from '@gouvfr-lasuite/cunningham-react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useEffect } from 'react';

import {
  MIGRATION_TARGET_STORAGE_KEY,
  getConnectPath,
  isMigrationTarget,
} from '@/core/migrationTarget';

import './page.scss';

/**
 * Pure transit page: this is where the backend redirects the browser back to
 * after the Keycloak/bridge round-trip (RESANA_MIGRATOR_REDIRECT_URL_SUCCESS
 * / _FAILURE), since that redirect target is a fixed URL with no target
 * query param. Forwards straight back to /connect with the stored target.
 */
function ConnectResanaContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  useEffect(() => {
    const storedTarget = sessionStorage.getItem(MIGRATION_TARGET_STORAGE_KEY);
    const target = isMigrationTarget(storedTarget) ? storedTarget : null;
    if (!target) {
      router.replace('/');
      return;
    }

    const flag =
      searchParams.get('resana_connected') === '1'
        ? 'resana_connected=1'
        : searchParams.get('resana_error') === '1'
          ? 'resana_error=1'
          : null;

    router.replace(`${getConnectPath(target)}${flag ? `&${flag}` : ''}`);
  }, [router, searchParams]);

  return (
    <div className="resana-connect-page container">
      <Loader />
    </div>
  );
}

export default function ConnectResana() {
  return (
    <Suspense fallback={null}>
      <ConnectResanaContent />
    </Suspense>
  );
}

'use client';

import { Button, Loader } from '@gouvfr-lasuite/cunningham-react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';

import ZipIcon from '@/assets/icons/icon-zip.svg';
import { useApi } from '@/hooks/useApi';
import { useWorkspaces } from '@/hooks/useWorkspaces';

function DownloadArchiveContent() {
  const { t } = useTranslation();
  const router = useRouter();
  const searchParams = useSearchParams();
  const workspaceId = searchParams.get('workspaceId') ?? '';
  const { workspaces, fetch } = useWorkspaces();
  const { fetchApi } = useApi();
  const hasFetched = useRef(false);

  useEffect(() => {
    if (hasFetched.current || !workspaceId) {
      return;
    }
    hasFetched.current = true;
    void fetch({ ids: [workspaceId] });
  }, [fetch, workspaceId]);

  const downloadArchive = async () => {
    const response = await fetchApi(
      'workspaces/' + workspaceId + '/download_archive',
    );
    const data = (await response.json()) as { url: string };
    window.open(data.url);
  };

  const workspace = workspaces?.[0];

  return (
    <div className="container">
      <div className="container--center">
        <ZipIcon aria-hidden />

        <h1>{t("Télécharger l'archive")}</h1>

        {workspace ? (
          <p>
            {t("L'espace Resana")} <strong>{workspace.title}</strong>{' '}
            {t('est disponible en téléchargement.')}
          </p>
        ) : (
          <Loader />
        )}

        <Button
          fullWidth
          color="brand"
          disabled={!workspace}
          onClick={() => void downloadArchive()}
        >
          {t('Télécharger')}
        </Button>

        <Button
          variant="tertiary"
          fullWidth
          color="neutral"
          onClick={() => router.push('/')}
        >
          {t('Ouvrir le migrateur')}
        </Button>
      </div>
    </div>
  );
}

export default function DownloadArchive() {
  return (
    <Suspense fallback={null}>
      <DownloadArchiveContent />
    </Suspense>
  );
}

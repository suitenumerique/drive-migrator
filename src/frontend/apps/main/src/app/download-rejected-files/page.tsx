'use client';

import { Button, Loader } from '@gouvfr-lasuite/cunningham-react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';

import ZipIcon from '@/assets/icons/icon-zip.svg';
import { useApi } from '@/hooks/useApi';
import { useAvailableDestinations } from '@/hooks/useAvailableDestinations';
import { useWorkspaces } from '@/hooks/useWorkspaces';

import './page.scss';

function DownloadRejectedFilesContent() {
  const { t } = useTranslation();
  const router = useRouter();
  const searchParams = useSearchParams();
  const workspaceId = searchParams.get('workspaceId') ?? '';
  const destination = searchParams.get('destination') ?? '';
  const { workspaces, fetch } = useWorkspaces();
  const { fetchApi } = useApi();
  const { destinations } = useAvailableDestinations();
  const hasFetched = useRef(false);

  useEffect(() => {
    if (hasFetched.current || !workspaceId) {
      return;
    }
    hasFetched.current = true;
    void fetch({ ids: [workspaceId] });
  }, [fetch, workspaceId]);

  const downloadRejectedFiles = async () => {
    const response = await fetchApi(
      'workspaces/' +
        workspaceId +
        '/download_rejected_files/?' +
        new URLSearchParams({ destination }).toString(),
    );
    const data = (await response.json()) as { url: string };
    // Not window.open: called after an await, browsers may block it as a popup.
    window.location.assign(data.url);
  };

  const isLoading = !!workspaceId && workspaces === undefined;
  const workspace = workspaces?.[0];
  // A newer migration of the workspace deletes the zip a mail may still link.
  const isAvailable =
    !!workspace?.destination_metadata?.[destination]?.['rejected_files_s3_key'];
  const destinationLabel =
    destinations?.find((dest) => dest.name === destination)?.label ??
    destination;

  return (
    <div className="container page-download-rejected-files">
      <div className="container--center page-download-rejected-files__content">
        <ZipIcon aria-hidden />

        <h1>
          {t('Fichiers refusés par {{destination}}', {
            destination: destinationLabel,
          })}
        </h1>

        {isLoading && <Loader />}

        {!isLoading && !isAvailable && (
          <p>{t("Cette archive n'est plus disponible.")}</p>
        )}

        {workspace && isAvailable && (
          <>
            <p>
              {t("Lors de la migration de l'espace")}{' '}
              <strong>{workspace.title}</strong>,{' '}
              {t(
                '{{destination}} a refusé certains fichiers (format non autorisé, taille trop importante…). Nous les avons mis de côté pour que vous puissiez les récupérer.',
                { destination: destinationLabel },
              )}
            </p>
            <p className="page-download-rejected-files__note">
              {t(
                "Les fichiers qui n'ont pas pu être récupérés depuis l'espace d'origine ne sont pas dans cette archive. Ils sont seulement listés, avec ceux refusés, dans le fichier « fichiers-non-migres.csv » qu'elle contient.",
              )}
            </p>
            <Button
              fullWidth
              color="brand"
              onClick={() => void downloadRejectedFiles()}
            >
              {t("Télécharger l'archive")}
            </Button>
          </>
        )}

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

export default function DownloadRejectedFiles() {
  return (
    <Suspense fallback={null}>
      <DownloadRejectedFilesContent />
    </Suspense>
  );
}

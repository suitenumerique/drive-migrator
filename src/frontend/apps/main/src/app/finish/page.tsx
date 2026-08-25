'use client';

import { Button } from '@gouvfr-lasuite/cunningham-react';
import Image from 'next/image';
import { useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import migrationResanaIllustration from '@/assets/images/migrator-illustration.svg?url';
import { ArrowLeftIcon } from '@/components/icons/ArrowLeftIcon';
import {
  MIGRATION_TARGET_STORAGE_KEY,
  MigrationTarget,
  isMigrationTarget,
} from '@/core/migrationTarget';
import { useMigrationConfig } from '@/hooks/useMigrationConfig';

import './page.scss';

enum MigrationTargetKind {
  ArchiveZip = 'archive-zip',
}

const LASUITE_URL = 'https://lasuite.numerique.gouv.fr';

export default function Finish() {
  const { t } = useTranslation();
  const router = useRouter();
  const [migrationTarget, setMigrationTarget] =
    useState<MigrationTarget>('lasuite-fichiers');

  useEffect(() => {
    const stored = sessionStorage.getItem(MIGRATION_TARGET_STORAGE_KEY);
    if (isMigrationTarget(stored)) {
      setMigrationTarget(stored);
    }
  }, []);

  const isArchiveZipTarget = migrationTarget === MigrationTargetKind.ArchiveZip;
  const { driveFrontendUrl } = useMigrationConfig();

  return (
    <div className="container">
      <Button
        variant="tertiary"
        color="neutral"
        icon={<ArrowLeftIcon width={16} height={16} aria-hidden />}
        onClick={() => router.push('/dashboard')}
      >
        {t('Retour')}
      </Button>
      <div className="container--center page-finish">
        <div className="page-finish__content">
          <Image
            src={migrationResanaIllustration}
            alt=""
            className="page-finish__illustration"
            width={400}
            height={142}
            priority
          />

          <h1 className="page-finish__title">
            {isArchiveZipTarget
              ? t('Téléchargement en préparation')
              : t('Migration en cours')}
          </h1>

          <p className="page-finish__description">
            {isArchiveZipTarget
              ? t(
                  'Vos espaces sont en cours de préparation pour être migrés. Vous recevrez un e-mail quand tout sera prêt. Vous pouvez fermer cette page.',
                )
              : t(
                  'Vos espaces sont en cours de migration. Vous recevrez un e-mail quand tout sera prêt.',
                )}
          </p>

          <div className="page-finish__actions">
            {isArchiveZipTarget ? (
              <Button
                variant="tertiary"
                fullWidth
                color="neutral"
                href={LASUITE_URL}
                target="_blank"
                rel="noopener noreferrer"
              >
                {t('Démarrer avec LaSuite')}
              </Button>
            ) : (
              <>
                <Button
                  variant="secondary"
                  fullWidth
                  color="brand"
                  href={driveFrontendUrl}
                  disabled={!driveFrontendUrl}
                  target="_blank"
                >
                  {t('Accéder à Fichiers')}
                </Button>

                <Button
                  variant="tertiary"
                  fullWidth
                  color="neutral"
                  href={LASUITE_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {t('Démarrer avec LaSuite')}
                </Button>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

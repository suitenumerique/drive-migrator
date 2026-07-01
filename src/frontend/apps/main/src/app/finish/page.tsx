'use client';

import { Button } from '@gouvfr-lasuite/cunningham-react';
import Image from 'next/image';
import { useRouter } from 'next/navigation';
import { useTranslation } from 'react-i18next';

import migrationResanaIllustration from '@/assets/images/migration-resana.svg?url';
import { ArrowLeftIcon } from '@/components/icons/ArrowLeftIcon';

import './page.scss';

const FICHIERS_URL = 'https://fichiers.numerique.gouv.fr';
const LASUITE_URL = 'https://lasuite.numerique.gouv.fr';

export default function Finish() {
  const { t } = useTranslation();
  const router = useRouter();

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

          <h1 className="page-finish__title">{t('Migration en cours')}</h1>

          <p className="page-finish__description">
            {t(
              'Vos espaces sont en cours de migration. Vous recevrez un e-mail quand tout sera prêt.',
            )}
          </p>

          <div className="page-finish__actions">
            <Button
              variant="secondary"
              fullWidth
              color="brand"
              href={FICHIERS_URL}
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
          </div>
        </div>
      </div>
    </div>
  );
}

'use client';

import Image from 'next/image';
import { useTranslation } from 'react-i18next';

import migrationResanaIllustration from '@/assets/images/migration-resana.svg?url';
import { Button } from '@/components/Button/Button';

import './page.scss';

const FICHIERS_URL = 'https://fichiers.numerique.gouv.fr';
const LASUITE_URL = 'https://lasuite.numerique.gouv.fr';

export default function Finish() {
  const { t } = useTranslation();

  return (
    <div className="container container--center page-finish">
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
  );
}

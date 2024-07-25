'use client';

import Image from 'next/image';
import { useTranslation } from 'react-i18next';

import { default as SystemImage } from '@/assets/images/system.svg?url';
import { Button } from '@/components/Button/Button';

import './page.scss';

export default function Finish() {
  const { t } = useTranslation();
  return (
    <div className="container">
      <div className="page-finish__logo">
        <Image src={SystemImage} alt={t('Chargement en cours')} width={200} />
        <p className="clr-greyscale-900">
          {t(
            "Vos exports sont en cours de traitement, cela peut prendre un certain temps. Vous pourrez retrouver l'état d'avancement des exports sur l'écran principal. Dans chacun des cas nous vous enverrons un mail lorsque l'export sera terminé afin de vous avertir.",
          )}
        </p>

        <Button className="mt-b" href="/dashboard">
          {t('Suivre les exports')}
        </Button>
      </div>
    </div>
  );
}

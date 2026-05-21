'use client';

import { useRouter } from 'next/navigation';
import { useTranslation } from 'react-i18next';

import { ResanaConnectSection } from '@/components/ResanaConnect/ResanaConnectSection';

export default function ConnectResana() {
  const router = useRouter();
  const { t } = useTranslation();

  return (
    <div className="container container--center">
      <h1>{t('Connexion à Resana requise')}</h1>
      <p>
        {t(
          'Veuillez vous connecter à votre compte Resana pour accéder à vos espaces de travail.',
        )}
      </p>
      <ResanaConnectSection onConnected={() => router.replace('/')} />
    </div>
  );
}

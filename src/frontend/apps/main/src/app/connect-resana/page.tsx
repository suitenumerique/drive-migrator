'use client';

import { Button, Loader } from '@gouvfr-lasuite/cunningham-react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import ResanaLogo from '@/assets/icons/resana-logo.svg';
import { ResanaConnectSection } from '@/components/ResanaConnect/ResanaConnectSection';
import { ArrowLeftIcon } from '@/components/icons/ArrowLeftIcon';
import { login, useAuth } from '@/core/auth/Auth';
import {
  MigrationTarget,
  getConnectPath,
  getResanaConnectPath,
  isMigrationTarget,
} from '@/core/migrationTarget';

import './page.scss';

function ConnectResanaContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { t } = useTranslation();
  const { isAuthenticated, isAuthPending } = useAuth();
  const [authStep, setAuthStep] = useState<'password' | 'otp'>('password');
  const target = isMigrationTarget(searchParams.get('target'))
    ? (searchParams.get('target') as MigrationTarget)
    : 'lasuite-fichiers';

  const resanaConnectPath = getResanaConnectPath(target);

  useEffect(() => {
    if (isAuthPending || isAuthenticated) {
      return;
    }

    router.replace(getConnectPath(target));
  }, [isAuthPending, isAuthenticated, router, target]);

  if (isAuthPending || !isAuthenticated) {
    return (
      <div className="resana-connect-page container">
        <Loader />
      </div>
    );
  }

  return (
    <div className="resana-connect-page container">
      <Button
        variant="tertiary"
        color="neutral"
        icon={<ArrowLeftIcon width={16} height={16} aria-hidden />}
        onClick={() => router.push(getConnectPath(target))}
      >
        {t('Retour')}
      </Button>

      <ResanaLogo className="resana-connect-page__logo" aria-hidden />

      <h1 className="resana-connect-page__title">{t('Connexion à Resana')}</h1>

      <p className="resana-connect-page__description">
        {authStep === 'otp'
          ? t(
              'Entrez le code reçu par e-mail ou provenant de votre app d’authentification.',
            )
          : t('Connectez-vous aux outils pour commencer la migration.')}
      </p>

      <ResanaConnectSection
        onStepChange={setAuthStep}
        onAuthRequired={() => login(resanaConnectPath)}
        onConnected={() =>
          router.replace(`${getConnectPath(target)}&resana_connected=1`)
        }
      />
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

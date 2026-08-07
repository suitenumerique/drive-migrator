'use client';

import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import {
  ReactNode,
  Suspense,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useTranslation } from 'react-i18next';

import ResanaLogoFull from '@/assets/icons/logo-resana-full.svg';
import ProConnectLogo from '@/assets/icons/proconnect-logo.svg';
import { Button } from '@/components/Button/Button';
import { ArrowLeftIcon } from '@/components/icons/ArrowLeftIcon';
import { login, logout, useAuth } from '@/core/auth/Auth';
import {
  MIGRATION_TARGET_STORAGE_KEY,
  MigrationTarget,
  getConnectPath,
  isMigrationTarget,
} from '@/core/migrationTarget';
import { useApi } from '@/hooks/useApi';
import { useResanaAuthStatus } from '@/hooks/useResanaAuthStatus';

import './page.scss';

const ConnectStatusDoneIcon = () => (
  <svg
    xmlns="http://www.w3.org/2000/svg"
    width="22"
    height="22"
    viewBox="0 0 22 22"
    fill="none"
    aria-hidden
  >
    <path
      fillRule="evenodd"
      clipRule="evenodd"
      d="M11 0C17.0751 0 22 4.92487 22 11C22 17.0751 17.0751 22 11 22C4.92487 22 0 17.0751 0 11C0 4.92487 4.92487 0 11 0ZM15.0777 8.02227C14.6482 7.59271 13.9518 7.59276 13.5223 8.02227L9.9 11.6445L8.47773 10.2223C8.04818 9.79271 7.35185 9.79276 6.92227 10.2223C6.49269 10.6518 6.49269 11.3482 6.92227 11.7777L9.12227 13.9777C9.55184 14.4073 10.2482 14.4073 10.6777 13.9777L15.0777 9.57773C15.5072 9.14816 15.5073 8.45182 15.0777 8.02227Z"
      fill="var(--c--contextuals--content--semantic--success--tertiary)"
    />
  </svg>
);

type ConnectToolRowProps = {
  name: string;
  logo: ReactNode;
  isDone: boolean;
  actionLabel: string;
  onAction: () => void;
  actionDisabled?: boolean;
  actionVariant?: 'primary' | 'secondary';
  showActionWhenDone?: boolean;
};

const ConnectToolRow = ({
  name,
  logo,
  isDone,
  actionLabel,
  onAction,
  actionDisabled = false,
  actionVariant = 'primary',
  showActionWhenDone = false,
}: ConnectToolRowProps) => {
  const showAction = !isDone || showActionWhenDone;

  return (
    <div className="migration-connect__row">
      <div
        className={[
          'migration-connect__status',
          isDone && 'migration-connect__status--done',
        ]
          .filter(Boolean)
          .join(' ')}
        aria-hidden
      >
        {isDone && <ConnectStatusDoneIcon />}
      </div>

      <div className="migration-connect__card">
        <div
          className="migration-connect__tool-logo"
          role="img"
          aria-label={name}
        >
          {logo}
        </div>

        {showAction && (
          <Button
            className="migration-connect__action"
            variant={actionVariant}
            color={actionVariant === 'primary' ? 'brand' : 'neutral'}
            onClick={onAction}
            disabled={actionDisabled}
          >
            {actionLabel}
          </Button>
        )}
      </div>
    </div>
  );
};

function ConnectPageContent() {
  const { t } = useTranslation();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { isAuthenticated, isAuthPending } = useAuth();
  const {
    connected: resanaConnected,
    check: checkResana,
    reset: resetResanaStatus,
  } = useResanaAuthStatus();
  const { fetchApi } = useApi();
  const [isContinuing, setIsContinuing] = useState(false);
  const [isResanaConnecting, setIsResanaConnecting] = useState(false);
  const hasAutoContinued = useRef(false);

  const target = useMemo<MigrationTarget | null>(() => {
    const fromQuery = searchParams.get('target');
    if (isMigrationTarget(fromQuery)) {
      return fromQuery;
    }
    const fromStorage = sessionStorage.getItem(MIGRATION_TARGET_STORAGE_KEY);
    return isMigrationTarget(fromStorage) ? fromStorage : null;
  }, [searchParams]);

  useEffect(() => {
    if (!target) {
      router.replace('/');
      return;
    }
    sessionStorage.setItem(MIGRATION_TARGET_STORAGE_KEY, target);
  }, [target, router]);

  useEffect(() => {
    if (!isAuthenticated) {
      resetResanaStatus();
      return;
    }
    void checkResana();
  }, [isAuthenticated, pathname, searchParams, checkResana, resetResanaStatus]);

  useEffect(() => {
    if (!isAuthenticated || searchParams.get('resana_connected') !== '1') {
      return;
    }
    void checkResana();
  }, [isAuthenticated, searchParams, checkResana]);

  useEffect(() => {
    if (!isAuthenticated) {
      return;
    }

    const refreshResanaStatus = () => {
      void checkResana();
    };

    window.addEventListener('focus', refreshResanaStatus);
    document.addEventListener('visibilitychange', refreshResanaStatus);

    return () => {
      window.removeEventListener('focus', refreshResanaStatus);
      document.removeEventListener('visibilitychange', refreshResanaStatus);
    };
  }, [isAuthenticated, checkResana]);

  const proConnectDone = isAuthenticated;
  const resanaDone = isAuthenticated && resanaConnected === true;
  const resanaStatusPending = isAuthenticated && resanaConnected === null;
  const resanaError =
    !resanaDone &&
    !isResanaConnecting &&
    searchParams.get('resana_error') === '1';

  const canContinue =
    Boolean(target) && isAuthenticated && resanaConnected === true;

  const handleProConnectLogin = () => {
    if (!target || isAuthenticated) {
      return;
    }
    login(getConnectPath(target));
  };

  const handleProConnectLogout = () => {
    if (!isAuthenticated) {
      return;
    }
    logout();
  };

  const handleResanaConnect = async () => {
    if (!target || !isAuthenticated || isResanaConnecting) {
      return;
    }
    setIsResanaConnecting(true);
    try {
      const response = await fetchApi('resana/auth/connect', undefined, {
        closableError: true,
      });
      const data = (await response.json()) as { authorize_url: string };
      window.location.replace(data.authorize_url);
    } catch {
      setIsResanaConnecting(false);
    }
  };

  const continueToDashboard = async () => {
    if (!target || !canContinue || isContinuing) {
      return;
    }

    setIsContinuing(true);
    try {
      await fetchApi('synchronize/', undefined, {
        closableError: true,
      });
      router.push('/dashboard');
    } catch {
      hasAutoContinued.current = false;
      void checkResana();
    } finally {
      setIsContinuing(false);
    }
  };

  useEffect(() => {
    if (!canContinue || hasAutoContinued.current || isContinuing) {
      return;
    }
    hasAutoContinued.current = true;
    void continueToDashboard();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- auto-continue once when all tools are connected
  }, [canContinue, isContinuing]);

  if (!target || isAuthPending) {
    return null;
  }

  return (
    <div className="migration-connect container">
      <Button
        variant="tertiary"
        color="neutral"
        icon={<ArrowLeftIcon width={16} height={16} aria-hidden />}
        onClick={() => router.push('/')}
      >
        {t('Retour')}
      </Button>

      <h1 className="migration-connect__title">{t('Connexion aux outils')}</h1>

      <p className="migration-connect__description">
        {t('Connectez-vous aux outils pour commencer la migration.')}
      </p>

      <div className="migration-connect__tools">
        <ConnectToolRow
          name="ProConnect"
          logo={
            <ProConnectLogo
              className="migration-connect__logo-svg"
              aria-hidden
            />
          }
          isDone={proConnectDone}
          actionLabel={proConnectDone ? t('Se déconnecter') : t('Se connecter')}
          onAction={
            proConnectDone ? handleProConnectLogout : handleProConnectLogin
          }
          actionVariant={proConnectDone ? 'secondary' : 'primary'}
          showActionWhenDone
        />

        <ConnectToolRow
          name="Resana"
          logo={
            <ResanaLogoFull
              className="migration-connect__logo-svg"
              aria-hidden
            />
          }
          isDone={resanaDone}
          actionLabel={t('Se connecter')}
          onAction={() => void handleResanaConnect()}
          actionDisabled={
            !isAuthenticated || resanaStatusPending || isResanaConnecting
          }
          actionVariant="primary"
        />
      </div>

      {resanaError && (
        <p className="migration-connect__error" role="alert">
          {t('La connexion à Resana a échoué, veuillez réessayer.')}
        </p>
      )}
    </div>
  );
}

export default function ConnectPage() {
  return (
    <Suspense fallback={null}>
      <ConnectPageContent />
    </Suspense>
  );
}

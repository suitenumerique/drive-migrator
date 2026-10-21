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

import FichiersLogo from '@/assets/icons/logo-fichiers.svg';
import ResanaLogoFull from '@/assets/icons/logo-resana-full.svg';
import ProConnectLogo from '@/assets/icons/proconnect-logo.svg';
import { Button } from '@/components/Button/Button';
import { ArrowLeftIcon } from '@/components/icons/ArrowLeftIcon';
import { login, logout, useAuth } from '@/core/auth/Auth';
import {
  MIGRATION_TARGET_STORAGE_KEY,
  MigrationTarget,
  getConnectPath,
  getMigrationDestination,
  isMigrationTarget,
} from '@/core/migrationTarget';
import { useApi } from '@/hooks/useApi';
import { useAvailableDestinations } from '@/hooks/useAvailableDestinations';
import { useDestinationAccountStatus } from '@/hooks/useDestinationAccountStatus';
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

  const destination = target ? getMigrationDestination(target) : 'archive';
  // /connect is public: the destinations can only be listed once logged in.
  const {
    destinations,
    hasFailed: destinationsFailed,
    refetch: refetchDestinations,
  } = useAvailableDestinations(isAuthenticated);
  const targetDestination = destinations?.find(
    (dest) => dest.name === destination,
  );
  const accountCreationUrl = targetDestination?.account_creation_url;
  // Only a destination the user must create their account on is asked about it.
  const accountRequired = targetDestination?.account_policy === 'user_created';
  const {
    exists: accountExists,
    error: accountError,
    check: checkAccount,
    reset: resetAccountStatus,
  } = useDestinationAccountStatus(destination);

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
    if (!isAuthenticated || !accountRequired) {
      resetAccountStatus();
      return;
    }
    void checkAccount();
  }, [isAuthenticated, accountRequired, checkAccount, resetAccountStatus]);

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

    // Also catches the account the user just created in another tab.
    const refreshStatuses = () => {
      if (document.visibilityState !== 'visible') {
        return;
      }
      void checkResana();
      if (destinationsFailed) {
        void refetchDestinations();
      }
      if (accountRequired) {
        void checkAccount();
      }
    };

    window.addEventListener('focus', refreshStatuses);
    document.addEventListener('visibilitychange', refreshStatuses);

    return () => {
      window.removeEventListener('focus', refreshStatuses);
      document.removeEventListener('visibilitychange', refreshStatuses);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refetchDestinations is recreated on each render
  }, [
    isAuthenticated,
    accountRequired,
    destinationsFailed,
    checkResana,
    checkAccount,
  ]);

  // Unknown until the destinations are listed, then only checked if required.
  let accountStatus: boolean | null = null;
  if (destinations) {
    accountStatus = accountRequired ? accountExists : true;
  }
  // Logging in to the destination once creates the account it requires.
  const accountMissing = isAuthenticated && accountStatus === false;
  const proConnectDone = isAuthenticated && accountStatus === true;
  const canLogout = isAuthenticated && !accountMissing;
  const accountPending =
    isAuthenticated &&
    accountStatus === null &&
    accountError === null &&
    !destinationsFailed;
  const resanaDone = isAuthenticated && resanaConnected === true;
  const resanaStatusPending = isAuthenticated && resanaConnected === null;
  const resanaError =
    !resanaDone &&
    !isResanaConnecting &&
    searchParams.get('resana_error') === '1';

  const canContinue =
    Boolean(target) && proConnectDone && resanaConnected === true;

  const handleProConnectLogin = () => {
    if (!target || isAuthenticated) {
      return;
    }
    login(getConnectPath(target));
  };

  const handleAccountCreation = () => {
    if (!accountCreationUrl) {
      return;
    }
    window.open(accountCreationUrl, '_blank', 'noopener,noreferrer');
  };

  const handleProConnectLogout = () => {
    if (!isAuthenticated) {
      return;
    }
    logout();
  };

  const handleAuthToolAction = () => {
    if (accountMissing) {
      handleAccountCreation();
    } else if (isAuthenticated) {
      handleProConnectLogout();
    } else {
      handleProConnectLogin();
    }
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
      const stillConnected = await checkResana();
      if (!stillConnected) {
        // Resana got disconnected, allow auto-continue to retry once the
        // user reconnects it.
        hasAutoContinued.current = false;
      }
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

  const isArchiveZipTarget = target === 'archive-zip';
  const authToolName = isArchiveZipTarget ? 'ProConnect' : 'Fichiers';
  const AuthToolLogo = isArchiveZipTarget ? ProConnectLogo : FichiersLogo;

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
          name={authToolName}
          logo={
            <AuthToolLogo className="migration-connect__logo-svg" aria-hidden />
          }
          isDone={proConnectDone}
          actionLabel={canLogout ? t('Se déconnecter') : t('Se connecter')}
          onAction={handleAuthToolAction}
          actionDisabled={
            accountPending || (accountMissing && !accountCreationUrl)
          }
          actionVariant={canLogout ? 'secondary' : 'primary'}
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

      {destinationsFailed && (
        <p className="migration-connect__error" role="alert">
          {t(
            'Le service est momentanément indisponible, veuillez réessayer dans quelques instants.',
          )}
        </p>
      )}

      {accountError === 'unavailable' && !proConnectDone && (
        <p className="migration-connect__error" role="alert">
          {t(
            'Fichiers est momentanément indisponible, veuillez réessayer dans quelques instants.',
          )}
        </p>
      )}

      {accountError === 'session_expired' && !proConnectDone && (
        <p className="migration-connect__error" role="alert">
          {t(
            'Votre session a expiré, veuillez vous déconnecter puis vous reconnecter.',
          )}
        </p>
      )}

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

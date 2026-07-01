'use client';

import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import {
  FC,
  ReactNode,
  SVGProps,
  Suspense,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { useTranslation } from 'react-i18next';

import FichiersLogo from '@/assets/icons/fichier-mono.svg';
import ResanaLogo from '@/assets/icons/resana-mono.svg';
import { Button } from '@/components/Button/Button';
import { ArrowLeftIcon } from '@/components/icons/ArrowLeftIcon';
import { login, useAuth } from '@/core/auth/Auth';
import {
  MIGRATION_RETURN_STORAGE_KEY,
  MIGRATION_TARGET_STORAGE_KEY,
  MigrationTarget,
  getConnectPath,
  getResanaConnectPath,
  isMigrationTarget,
  needsProConnect,
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
      fill="#2B695A"
    />
  </svg>
);

type ConnectToolRowProps = {
  label: string;
  icon: ReactNode;
  isDone: boolean;
  isActive: boolean;
  onClick: () => void;
  disabled?: boolean;
};

const ConnectToolRow = ({
  label,
  icon,
  isDone,
  isActive,
  onClick,
  disabled = false,
}: ConnectToolRowProps) => (
  <div className="migration-connect__row">
    <Button
      fullWidth
      variant={isActive ? 'primary' : 'secondary'}
      color={isActive ? 'brand' : 'neutral'}
      icon={icon}
      onClick={onClick}
      disabled={disabled || isDone}
    >
      {label}
    </Button>
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
  </div>
);

const ToolIcon = ({
  Icon,
  active,
}: {
  Icon: FC<SVGProps<SVGSVGElement>>;
  active: boolean;
}) => (
  <Icon
    width={24}
    height={24}
    className={
      active
        ? 'migration-connect__tool-icon migration-connect__tool-icon--active'
        : 'migration-connect__tool-icon'
    }
  />
);

function ConnectPageContent() {
  const { t } = useTranslation();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { isAuthenticated } = useAuth();
  const {
    connected: resanaConnected,
    check: checkResana,
    reset: resetResanaStatus,
  } = useResanaAuthStatus();
  const { fetchApi } = useApi();
  const [isContinuing, setIsContinuing] = useState(false);
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
    const returnPath = sessionStorage.getItem(MIGRATION_RETURN_STORAGE_KEY);
    if (isAuthenticated && returnPath) {
      sessionStorage.removeItem(MIGRATION_RETURN_STORAGE_KEY);
      router.replace(returnPath);
    }
  }, [isAuthenticated, router]);

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

  const requiresProConnect = target ? needsProConnect(target) : false;
  const proConnectDone = requiresProConnect && isAuthenticated;
  const resanaDone = isAuthenticated && resanaConnected === true;
  const resanaStatusPending = isAuthenticated && resanaConnected === null;

  const canContinue =
    Boolean(target) &&
    isAuthenticated &&
    resanaConnected === true &&
    (!requiresProConnect || proConnectDone);

  const fichiersActive = requiresProConnect && !proConnectDone;
  const resanaActive = !resanaDone && !resanaStatusPending;

  const handleProConnect = () => {
    if (!target || isAuthenticated) {
      return;
    }
    login(getConnectPath(target));
  };

  const handleResanaConnect = () => {
    if (!target) {
      return;
    }
    router.push(getResanaConnectPath(target));
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

  if (!target) {
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
          label={t('Se connecter à Resana')}
          icon={<ToolIcon Icon={ResanaLogo} active={resanaActive} />}
          isDone={resanaDone}
          isActive={resanaActive}
          onClick={handleResanaConnect}
          disabled={resanaStatusPending}
        />

        {requiresProConnect && (
          <ConnectToolRow
            label={t('Se connecter à LaSuite Fichiers')}
            icon={<ToolIcon Icon={FichiersLogo} active={fichiersActive} />}
            isDone={proConnectDone}
            isActive={fichiersActive}
            onClick={handleProConnect}
            disabled={proConnectDone}
          />
        )}
      </div>
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

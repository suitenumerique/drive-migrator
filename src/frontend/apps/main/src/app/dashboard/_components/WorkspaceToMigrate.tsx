'use client';

import {
  Alert,
  Button,
  Loader,
  VariantType,
  useToastProvider,
} from '@gouvfr-lasuite/cunningham-react';
import { useRouter } from 'next/navigation';
import { useEffect, useMemo, useState } from 'react';
import { FormProvider, useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';

import { MigrationConfirmModal } from '@/app/dashboard/_components/MigrationConfirmModal';
import { WorkspaceSelectCard } from '@/app/dashboard/_components/WorkspaceSelectCard';
import { WorkspaceByStatus } from '@/app/dashboard/page';
import { WorkspaceStatus } from '@/components/Workspace/Workspace';
import { ArrowLeftIcon } from '@/components/icons/ArrowLeftIcon';
import {
  MIGRATION_TARGET_STORAGE_KEY,
  MigrationTarget,
  getMigrationDestination,
  isMigrationTarget,
} from '@/core/migrationTarget';
import { useApi } from '@/hooks/useApi';
import { FeatureFlags, useFeatureFlags } from '@/hooks/useFeatureFlags';

import './WorkspaceToMigrate.scss';

type IForm = Record<string, boolean>;

export const WorkspacesToMigrate = ({
  workspaces,
}: {
  workspaces: WorkspaceByStatus;
}) => {
  const { flags } = useFeatureFlags();
  const router = useRouter();
  const { t } = useTranslation();
  const { fetchApi } = useApi();
  const { toast } = useToastProvider();
  const [isDownloading, setIsDownloading] = useState(false);
  const [isMigrating, setIsMigrating] = useState(false);
  const [isConfirmModalOpen, setIsConfirmModalOpen] = useState(false);
  const [pendingFormData, setPendingFormData] = useState<IForm | null>(null);
  const [migrationTarget, setMigrationTarget] =
    useState<MigrationTarget>('lasuite-fichiers');

  useEffect(() => {
    const stored = sessionStorage.getItem(MIGRATION_TARGET_STORAGE_KEY);
    if (isMigrationTarget(stored)) {
      setMigrationTarget(stored);
    }
  }, []);

  const isArchiveZipTarget = migrationTarget === 'archive-zip';

  const selectableWorkspaces = workspaces[WorkspaceStatus.NONE];
  const migratedWorkspaces = workspaces[WorkspaceStatus.SUCCESS];
  const listingWorkspaces = useMemo(
    () => [...selectableWorkspaces, ...migratedWorkspaces],
    [selectableWorkspaces, migratedWorkspaces],
  );

  const defaultValues = useMemo(
    () =>
      Object.fromEntries(
        selectableWorkspaces.map((workspace) => [workspace.id, false]),
      ),
    [selectableWorkspaces],
  );

  const methods = useForm<IForm>({
    defaultValues,
    values: defaultValues,
    resolver: (data) => {
      const oneChecked = Object.entries(data).some(([, value]) => value);

      return {
        values: data,
        errors: oneChecked
          ? {}
          : {
              globalError: 'error',
            },
      };
    },
  });

  const selectAll = () => {
    selectableWorkspaces.forEach((workspace) => {
      methods.setValue(workspace.id, true, { shouldValidate: true });
    });
  };

  const deselectAll = () => {
    selectableWorkspaces.forEach((workspace) => {
      methods.setValue(workspace.id, false, { shouldValidate: true });
    });
  };

  const downloadSelection = async () => {
    const selectedIds = Object.entries(methods.getValues())
      .filter(([, value]) => value)
      .map(([id]) => id);

    if (selectedIds.length === 0) {
      return;
    }

    setIsDownloading(true);
    try {
      await fetchApi(
        'workspaces/process',
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            workspaces: Object.fromEntries(
              selectedIds.map((id) => [id, ['archive']]),
            ),
          }),
        },
        { closableError: true },
      );

      toast(
        t(
          'Demande de téléchargement prise en compte. Un e-mail vous sera envoyé lorsque votre téléchargement sera prêt.',
        ),
        VariantType.INFO,
        {
          primaryLabel: t('OK'),
          icon: <span className="material-icons">mail</span>,
        },
      );
    } finally {
      setIsDownloading(false);
    }
  };

  const openConfirmModal = (data: IForm) => {
    setPendingFormData(data);
    setIsConfirmModalOpen(true);
  };

  const confirmMigration = async () => {
    if (!pendingFormData) {
      return;
    }

    const selectedIds = Object.entries(pendingFormData)
      .filter(([, value]) => value)
      .map(([id]) => id);

    if (selectedIds.length === 0) {
      return;
    }

    const destination = getMigrationDestination(migrationTarget);

    setIsMigrating(true);
    try {
      await fetchApi(
        'workspaces/process',
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            workspaces: Object.fromEntries(
              selectedIds.map((id) => [id, [destination]]),
            ),
          }),
        },
        { closableError: true },
      );

      setIsConfirmModalOpen(false);
      router.push('/finish');
    } finally {
      setIsMigrating(false);
    }
  };

  const canMigrate = flags?.[FeatureFlags.ALLOW_NEW_TASKS];
  const watchedValues = methods.watch();
  const isSomethingSelected = selectableWorkspaces.some(
    (workspace) => watchedValues[workspace.id],
  );
  const selectedCount = Object.values(pendingFormData ?? watchedValues).filter(
    Boolean,
  ).length;

  if (flags === undefined) {
    return (
      <div className="container__loader">
        <Loader />
      </div>
    );
  }

  return (
    <div className="workspaces-to-migrate">
      <Button
        variant="tertiary"
        color="neutral"
        icon={<ArrowLeftIcon width={16} height={16} aria-hidden />}
        onClick={() => router.push('/')}
      >
        {t('Retour')}
      </Button>
      <div className="workspaces-to-migrate__header">
        <div className="workspaces-to-migrate__intro">
          <h2 className="workspaces-to-migrate__title">
            {t('Sélectionnez les espaces à migrer')}
          </h2>
          {flags?.[FeatureFlags.READ_ONLY_MODE] && !isArchiveZipTarget ? (
            <Alert
              className="workspaces-to-migrate__alert"
              type={VariantType.ERROR}
            >
              {t('Vous ne possédez pas les droits pour migrer les espaces')}
            </Alert>
          ) : (
            <p className="workspaces-to-migrate__description">
              {t(
                'Pour chaque espace sélectionné, un dossier partagé reprenant les mêmes membres sera créé dans Fichiers.',
              )}
            </p>
          )}
        </div>

        {selectableWorkspaces.length > 0 && (
          <Button
            variant="tertiary"
            color="brand"
            size="medium"
            onClick={isSomethingSelected ? deselectAll : selectAll}
          >
            {isSomethingSelected
              ? t('Tout désélectionner')
              : t('Tout sélectionner')}
          </Button>
        )}
      </div>

      {listingWorkspaces.length === 0 ? (
        <Alert className="workspaces-to-migrate__alert" type={VariantType.INFO}>
          {t('Aucun espace à migrer')}
        </Alert>
      ) : (
        <FormProvider {...methods}>
          {!canMigrate && !isArchiveZipTarget && (
            <Alert
              type={VariantType.WARNING}
              className="workspaces-to-migrate__alert"
            >
              {flags?.[FeatureFlags.READ_ONLY_MODE]
                ? t(
                    'Les migrations ne sont désormais plus possibles, la plateforme Resana est désormais décomissionnée.',
                  )
                : t(
                    'Les migrations sont temporairement suspendues pour cause de maintenance, veuillez réessayer plus tard.',
                  )}
            </Alert>
          )}

          <form
            className="workspaces-to-migrate__form"
            onSubmit={(event) => {
              void methods.handleSubmit(openConfirmModal)(event);
            }}
          >
            <div className="workspaces-to-migrate__grid">
              {listingWorkspaces.map((workspace) => (
                <WorkspaceSelectCard
                  key={workspace.id}
                  workspace={workspace}
                  migrated={workspace.status === WorkspaceStatus.SUCCESS}
                />
              ))}
            </div>

            <div className="workspaces-to-migrate__footer">
              <Button
                type="button"
                variant={isArchiveZipTarget ? 'primary' : 'bordered'}
                color="brand"
                disabled={!isSomethingSelected || isDownloading}
                onClick={() => void downloadSelection()}
              >
                {t('Télécharger la sélection')}
              </Button>
              {!isArchiveZipTarget && (
                <Button
                  type="submit"
                  variant="primary"
                  color="brand"
                  disabled={!methods.formState.isValid || !canMigrate}
                >
                  {t('Migrer')}
                </Button>
              )}
            </div>
          </form>

          <MigrationConfirmModal
            isOpen={isConfirmModalOpen}
            onClose={() => setIsConfirmModalOpen(false)}
            onConfirm={() => void confirmMigration()}
            selectedCount={selectedCount}
            isConfirming={isMigrating}
          />
        </FormProvider>
      )}
    </div>
  );
};

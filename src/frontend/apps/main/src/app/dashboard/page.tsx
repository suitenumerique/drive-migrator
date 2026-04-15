'use client';
import { Alert, Loader, VariantType } from '@openfun/cunningham-react';
import { useEffect } from 'react';
import { useTranslation } from 'react-i18next';

import { WorkspacesToMigrate } from '@/app/dashboard/_components/WorkspaceToMigrate';
import {
  Workspace,
  WorkspaceExporting,
  WorkspaceStatus,
} from '@/components/Workspace/Workspace';
import { useWorkspaces } from '@/hooks/useWorkspaces';

export type WorkspaceByStatus = Record<WorkspaceStatus, Workspace[]>;

export default function Dashboard() {
  const { workspacesByStatus, fetch, hasError } = useWorkspaces();

  useEffect(() => {
    void fetch();
  }, []);

  if (hasError) {
    return null;
  }

  return (
    <div className="container">
      {workspacesByStatus ? (
        <>
          <FailureWorkspaces workspaces={workspacesByStatus} />
          <PendingWorkspaces workspaces={workspacesByStatus} />
          <WorkspacesToMigrate workspaces={workspacesByStatus} />
          <SuccessWorkspaces workspaces={workspacesByStatus} />
        </>
      ) : (
        <div className="container__loader">
          <Loader size="medium" />
        </div>
      )}
    </div>
  );
}

const PendingWorkspaces = ({
  workspaces,
}: {
  workspaces: WorkspaceByStatus;
}) => {
  const { t } = useTranslation();
  if (workspaces[WorkspaceStatus.PENDING].length === 0) {
    return null;
  }
  return (
    <div>
      <h2>{t('Communautés en cours de migration')}</h2>
      <div className="suite__workspaces">
        {workspaces[WorkspaceStatus.PENDING].map((workspace) => (
          <WorkspaceExporting workspace={workspace} key={workspace.id} />
        ))}
      </div>
    </div>
  );
};

const SuccessWorkspaces = ({
  workspaces,
}: {
  workspaces: WorkspaceByStatus;
}) => {
  const { t } = useTranslation();
  if (workspaces[WorkspaceStatus.SUCCESS].length === 0) {
    return null;
  }
  return (
    <div>
      <h2>{t('Communautés migrées')}</h2>
      <Alert type={VariantType.SUCCESS}>
        <div>
          {t(
            'Les communautés suivantes ont été migrées vers les destinations sélectionnées. Vous avez reçu un mail de confirmation.',
          )}
        </div>
      </Alert>
      <div className="suite__workspaces mt-s">
        {workspaces[WorkspaceStatus.SUCCESS].map((workspace) => (
          <WorkspaceExporting workspace={workspace} key={workspace.id} />
        ))}
      </div>
    </div>
  );
};

const FailureWorkspaces = ({
  workspaces,
}: {
  workspaces: WorkspaceByStatus;
}) => {
  const { t } = useTranslation();
  if (workspaces[WorkspaceStatus.FAILURE].length === 0) {
    return null;
  }
  return (
    <div>
      <h2>{t('Communautés en erreur')}</h2>
      <Alert type={VariantType.ERROR}>
        <div>
          {t(
            "Si une communauté est en erreur, c'est qu'il y a eu un problème inattendu lors de la migration. Contactez le support pour obtenir de l'aide.",
          )}
        </div>
      </Alert>
      <div className="suite__workspaces mt-s">
        {workspaces[WorkspaceStatus.FAILURE].map((workspace) => (
          <WorkspaceExporting workspace={workspace} key={workspace.id} />
        ))}
      </div>
    </div>
  );
};

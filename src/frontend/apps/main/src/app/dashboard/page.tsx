'use client';
import { Alert, Loader, VariantType } from '@gouvfr-lasuite/cunningham-react';
import { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';

import { WorkspacesToMigrate } from '@/app/dashboard/_components/WorkspaceToMigrate';
import {
  Workspace,
  WorkspaceExporting,
  WorkspaceStatus,
} from '@/components/Workspace/Workspace';
import { useMigrationConfig } from '@/hooks/useMigrationConfig';
import { useWorkspaces } from '@/hooks/useWorkspaces';

export type WorkspaceByStatus = Record<WorkspaceStatus, Workspace[]>;

export default function Dashboard() {
  const { workspacesByStatus, fetch, hasError } = useWorkspaces();
  const { fileLimitPerWorkspace } = useMigrationConfig();
  const hasFetched = useRef(false);

  useEffect(() => {
    if (hasFetched.current) {
      return;
    }
    hasFetched.current = true;
    void fetch({}, { syncIfEmpty: true });
  }, [fetch]);

  if (hasError && !workspacesByStatus) {
    return null;
  }

  return (
    <div className="container">
      <MigrationLimitNotice fileLimitPerWorkspace={fileLimitPerWorkspace} />
      {workspacesByStatus ? (
        <>
          <FailureWorkspaces workspaces={workspacesByStatus} />
          <WorkspacesToMigrate workspaces={workspacesByStatus} />
        </>
      ) : (
        <div className="container__loader">
          <Loader size="medium" />
        </div>
      )}
    </div>
  );
}

const MigrationLimitNotice = ({
  fileLimitPerWorkspace,
}: {
  fileLimitPerWorkspace: number;
}) => {
  const { t } = useTranslation();
  if (fileLimitPerWorkspace <= 0) {
    return null;
  }
  return (
    <Alert type={VariantType.INFO} className="mb-s">
      {t(
        'Phase de test : la migration est actuellement limitée à {{limit}} fichiers par espace.',
        { limit: fileLimitPerWorkspace },
      )}
    </Alert>
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

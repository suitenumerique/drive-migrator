import './Workspace.scss';

import { Checkbox, Switch, VariantType } from '@openfun/cunningham-react';
import React, { PropsWithChildren, ReactNode } from 'react';
import { useController, useFormContext } from 'react-hook-form';
import { useTranslation } from 'react-i18next';

import { Badge } from '@/components/Badge/Badge';

export enum WorkspaceStatus {
  NONE = 'NONE',
  PENDING = 'PENDING',
  SUCCESS = 'SUCCESS',
  FAILURE = 'FAILURE',
}

export interface Workspace {
  id: string;
  title: string;
  status: WorkspaceStatus;
  status_archive: WorkspaceStatus;
  status_resana: WorkspaceStatus;
}

const WorkspaceStatusBadge = ({
  children,
  status,
}: PropsWithChildren & { status: WorkspaceStatus }) => {
  const { t } = useTranslation();
  if (status === WorkspaceStatus.NONE) {
    return null;
  }
  return (
    <div className="suite__workspace__status__unit">
      <p>{children}</p>
      {status === WorkspaceStatus.PENDING && (
        <Badge variant={VariantType.INFO}>{t('En cours')}</Badge>
      )}
      {status === WorkspaceStatus.SUCCESS && (
        <Badge variant={VariantType.SUCCESS}>{t('Terminé')}</Badge>
      )}
      {status === WorkspaceStatus.FAILURE && (
        <Badge variant={VariantType.ERROR}>{t('Échoué')}</Badge>
      )}
    </div>
  );
};

export const WorkspaceExporting = ({ workspace }: { workspace: Workspace }) => {
  const methods = useFormContext();
  const fieldName = `${workspace.id}`;
  const { t } = useTranslation();
  return (
    <GenericWorkspace workspace={workspace}>
      <div className="suite__workspace__status">
        <WorkspaceStatusBadge status={workspace.status_archive}>
          {t('Archive')}
        </WorkspaceStatusBadge>
        <WorkspaceStatusBadge status={workspace.status_resana}>
          {t('Resana')}
        </WorkspaceStatusBadge>
      </div>
    </GenericWorkspace>
  );
};

export const Workspace = ({ workspace }: { workspace: Workspace }) => {
  const { t } = useTranslation();

  const methods = useFormContext();
  const fieldName = `${workspace.id}`;

  const { field } = useController({ name: fieldName });

  return (
    <GenericWorkspace
      onClick={() => {
        methods.setValue(fieldName, !methods.getValues(fieldName), {
          shouldValidate: true,
        });
      }}
      workspace={workspace}
      childrenBefore={
        <Checkbox
          aria-label={t('Selectionner le workspace ' + workspace.title)}
          checked={field.value as boolean}
          name={fieldName}
        />
      }
    />
  );
};

export const WorkspacePreExport = ({ workspace }: { workspace: Workspace }) => {
  const { t } = useTranslation();
  const { register, ...methods } = useFormContext();
  const fieldPrefix = `workspaces.${workspace.id}`;
  const error = (methods.formState.errors as any).workspaces?.[workspace.id];

  return (
    <div>
      <GenericWorkspace
        workspace={workspace}
        className="suite__workspace--pre-export"
      >
        <div className="suite__workspace__switches">
          <Switch
            label={t('Archive')}
            {...register(fieldPrefix)}
            value="archive"
          />
          <Switch
            label={t('Resana')}
            {...register(fieldPrefix)}
            value="resana"
          />
        </div>
      </GenericWorkspace>
      {error && (
        <div className="clr-danger-text suite__workspace__error">{error}</div>
      )}
    </div>
  );
};

export const GenericWorkspace = ({
  workspace,
  children,
  childrenBefore,
  className,
  ...props
}: { workspace: Workspace } & React.HTMLAttributes<HTMLDivElement> &
  PropsWithChildren & { childrenBefore?: ReactNode }) => {
  return (
    // eslint-disable-next-line jsx-a11y/no-static-element-interactions,jsx-a11y/click-events-have-key-events
    <div className={'suite__workspace ' + className} {...props}>
      {childrenBefore}
      <div className="clr-primary-text">{workspace.title}</div>
      {children}
    </div>
  );
};

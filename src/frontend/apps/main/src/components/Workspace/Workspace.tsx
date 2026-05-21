import {
  Alert,
  Checkbox,
  Loader,
  Modal,
  ModalSize,
  Switch,
  VariantType,
  useModal,
} from '@openfun/cunningham-react';
import React, { PropsWithChildren, ReactNode, useState } from 'react';
import { useController, useFormContext } from 'react-hook-form';
import { useTranslation } from 'react-i18next';

import { Badge } from '@/components/Badge/Badge';
import { Button } from '@/components/Button/Button';
import { DropButton } from '@/components/DropButton/DropButton';
import { useApi } from '@/hooks/useApi';

import './Workspace.scss';

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
  source_type?: string;
  destination_statuses: Record<string, WorkspaceStatus>;
  destination_metadata: Record<string, Record<string, unknown>>;
}

const WorkspaceStatusBadge = ({
  children,
  status,
  variantOverride,
}: PropsWithChildren & {
  status: WorkspaceStatus;
  variantOverride?: VariantType;
}) => {
  const { t } = useTranslation();
  if (status === WorkspaceStatus.NONE) {
    return null;
  }
  const map = {
    [WorkspaceStatus.PENDING]: VariantType.INFO,
    [WorkspaceStatus.SUCCESS]: VariantType.SUCCESS,
    [WorkspaceStatus.FAILURE]: VariantType.ERROR,
  };
  const variant = variantOverride ? variantOverride : map[status];
  return (
    <div className="suite__workspace__status__unit">
      <p>{children}</p>
      {status === WorkspaceStatus.PENDING && (
        <Badge variant={variant}>{t('En cours')}</Badge>
      )}
      {status === WorkspaceStatus.SUCCESS && (
        <Badge variant={variant}>{t('Terminé')}</Badge>
      )}
      {status === WorkspaceStatus.FAILURE && (
        <Badge variant={variant}>{t('Échoué')}</Badge>
      )}
    </div>
  );
};

export const WorkspaceExporting = ({ workspace }: { workspace: Workspace }) => {
  const { t } = useTranslation();
  const { fetchApi } = useApi();
  const modal = useModal();
  const [resanaErrorDetails, setResanaErrorDetails] = useState<any>();

  const archiveStatus = workspace.destination_statuses['archive'];
  const resanaStatus = workspace.destination_statuses['resana'];

  const showDetails = () => {
    return !!(
      archiveStatus === WorkspaceStatus.SUCCESS ||
      resanaStatus === WorkspaceStatus.FAILURE
    );
  };

  const downloadArchive = async () => {
    const response = await fetchApi(
      'workspaces/' + workspace.id + '/download_archive',
    );
    const data = (await response.json()) as { url: string };
    window.open(data.url);
  };

  const showResanaErrorDetails = async () => {
    modal.open();
    const response = await fetchApi(
      'workspaces/' + workspace.id + '/resana_error_details/',
    );
    const data = await response.json();
    setResanaErrorDetails(data);
  };

  const retry = async () => {
    modal.open();
    const response = await fetchApi(
      'workspaces/' + workspace.id + '/resana_retry/',
    );
    await response.json();
    window.location.reload();
  };

  const options = (
    <DropButton
      aria-label={t('My account')}
      button={
        <Button
          color="tertiary-text"
          icon={<span className="material-icons">more_horiz</span>}
        />
      }
    >
      <ul>
        {archiveStatus === WorkspaceStatus.SUCCESS && (
          <li>
            <Button
              color="primary-text"
              icon={<span className="material-icons">sync</span>}
              onClick={() => void downloadArchive()}
            >
              {t('Télécharger archive')}
            </Button>
          </li>
        )}
        {resanaStatus === WorkspaceStatus.FAILURE && (
          <li>
            <Button
              color="primary-text"
              icon={<span className="material-icons">question_mark</span>}
              onClick={() => void showResanaErrorDetails()}
            >
              {t('Détails erreur Resana')}
            </Button>
          </li>
        )}
      </ul>
    </DropButton>
  );

  return (
    <GenericWorkspace
      workspace={workspace}
      className="suite__workspace--exporting"
    >
      <div className="suite__workspace__status">
        {Object.entries(workspace.destination_statuses).map(
          ([name, status]) => (
            <WorkspaceStatusBadge key={name} status={status}>
              {name}
            </WorkspaceStatusBadge>
          ),
        )}
      </div>
      {showDetails() && options}
      <Modal
        title={t('Détails erreurs Resana')}
        size={ModalSize.EXTRA_LARGE}
        {...modal}
      >
        <Alert>
          <div>
            <div>
              {t(
                "Ceci est la liste des fichiers dont l'importation a échoué sur Resana, ceci peut être dû par exemple à un refus de la part dans l'anti-virus.",
              )}
            </div>
            <br />
            <div>
              {t(
                '⚠️ Important ⚠️ Les fichiers non présents dans cette liste ont bien été importés sur Resana.',
              )}
            </div>
          </div>
        </Alert>
        <div className="suite__workspace__resana-error__details">
          {resanaErrorDetails ? (
            <>
              <div>
                {t('Fichiers en erreur: ')}
                {resanaErrorDetails['job']['numberOfFilesError']}
              </div>

              {new URLSearchParams(window.location.search).get('debug') ===
                'true' && (
                <div>
                  <Button onClick={() => void retry()}>{t('Relancer')}</Button>
                </div>
              )}
              {/* eslint-disable-next-line @typescript-eslint/no-unsafe-call */}
              {resanaErrorDetails['details']['hydra:member'].map(
                (entry: any) => {
                  return (
                    <div
                      key={entry.key}
                      className="suite__workspace__resana-error__details__row"
                    >
                      <div className="suite__workspace__resana-error__details__row__name">
                        {entry.key}
                      </div>
                      <div className="suite__workspace__resana-error__details__row__logs">
                        {JSON.stringify(entry.logMessage)}
                      </div>
                    </div>
                  );
                },
              )}
            </>
          ) : (
            <div className="container__loader">
              <Loader />
            </div>
          )}
        </div>
      </Modal>
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

export const WorkspacePreExport = ({
  workspace,
  destinations,
}: {
  workspace: Workspace;
  destinations: { name: string; label: string }[];
}) => {
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
          {destinations.map((dest) => (
            <Switch
              key={dest.name}
              label={t(dest.label)}
              {...register(fieldPrefix)}
              value={dest.name}
            />
          ))}
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

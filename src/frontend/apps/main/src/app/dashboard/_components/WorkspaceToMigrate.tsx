import { Alert, Button, Loader, VariantType } from '@openfun/cunningham-react';
import { useRouter } from 'next/navigation';
import { FormProvider, useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';

import { WorkspaceByStatus } from '@/app/page';
import { Workspace, WorkspaceStatus } from '@/components/Workspace/Workspace';
import { FeatureFlags, useFeatureFlags } from '@/hooks/useFeatureFlags';

type IForm = Record<string, boolean>;

export const WorkspacesToMigrate = ({
  workspaces,
}: {
  workspaces: WorkspaceByStatus;
}) => {
  const { flags } = useFeatureFlags();
  console.log('featureFlags', flags?.[FeatureFlags.ALLOW_NEW_TASKS]);
  const router = useRouter();
  const { t } = useTranslation();

  const methods = useForm<IForm>({
    defaultValues: {},
    resolver: (data) => {
      // At least one checkbox should be checked.
      let oneChecked = false;
      Object.entries(data).forEach(([, value]) => {
        if (value) {
          oneChecked = true;
        }
      });

      return {
        values: data,
        errors:
          oneChecked || false
            ? {}
            : {
                // isValid flag is based on the fact that there is no error, so by setting this error, the form is invalid.
                globalError: 'error',
              },
      };
    },
  });

  const submit = (data: IForm) => {
    const url = new URL('/prepare', window.location.origin);
    url.searchParams.append(
      'workspaces_ids',
      Object.entries(data)
        .filter(([, value]) => value)
        .map(([id]) => id)
        .join(','),
    );
    router.push(url.href);
  };

  if (flags === undefined) {
    return (
      <div className="container__loader">
        <Loader />
      </div>
    );
  }

  return (
    <div>
      <h2>{t('Communautés pouvant être migrées')}</h2>
      {workspaces[WorkspaceStatus.NONE].length === 0 ? (
        <Alert type={VariantType.INFO}>{t('Aucune communauté à migrer')}</Alert>
      ) : (
        <FormProvider {...methods}>
          {!flags?.[FeatureFlags.ALLOW_NEW_TASKS] && (
            <Alert type={VariantType.WARNING} className="mb-s">
              {t(
                'Les migrations sont temporairement suspendues pour cause de maintenance, veuillez réessayer plus tard.',
              )}
            </Alert>
          )}
          {/* eslint-disable-next-line @typescript-eslint/no-misused-promises */}
          <form onSubmit={methods.handleSubmit(submit)}>
            <div className="suite__workspaces">
              {workspaces[WorkspaceStatus.NONE].map((workspace) => (
                <Workspace workspace={workspace} key={workspace.id} />
              ))}
            </div>
            <div className="suite__workspaces__footer">
              <Button
                disabled={
                  !methods.formState.isValid ||
                  !flags?.[FeatureFlags.ALLOW_NEW_TASKS]
                }
              >
                {t('Migrer les communautés selectionnées')}
              </Button>
            </div>
          </form>
        </FormProvider>
      )}
    </div>
  );
};

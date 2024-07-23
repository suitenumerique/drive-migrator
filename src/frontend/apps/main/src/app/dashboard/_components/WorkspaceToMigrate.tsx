import { Alert, Button, VariantType } from '@openfun/cunningham-react';
import { useRouter } from 'next/navigation';
import { FormProvider, useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';

import { WorkspaceByStatus } from '@/app/page';
import { Workspace, WorkspaceStatus } from '@/components/Workspace/Workspace';

type IForm = Record<string, boolean>;

export const WorkspacesToMigrate = ({
  workspaces,
}: {
  workspaces: WorkspaceByStatus;
}) => {
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

  return (
    <div>
      <h2>{t('Communautés pouvant être migrées')}</h2>
      {workspaces[WorkspaceStatus.NONE].length === 0 ? (
        <Alert type={VariantType.INFO}>{t('Aucune communauté à migrer')}</Alert>
      ) : (
        <FormProvider {...methods}>
          {/* eslint-disable-next-line @typescript-eslint/no-misused-promises */}
          <form onSubmit={methods.handleSubmit(submit)}>
            <div className="suite__workspaces">
              {workspaces[WorkspaceStatus.NONE].map((workspace) => (
                <Workspace workspace={workspace} key={workspace.id} />
              ))}
            </div>
            <div className="suite__workspaces__footer">
              <Button disabled={!methods.formState.isValid}>
                {t('Migrer les communautés selectionnées')}
              </Button>
            </div>
          </form>
        </FormProvider>
      )}
    </div>
  );
};

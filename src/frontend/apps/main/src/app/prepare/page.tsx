'use client';
import { Alert, Button, Loader } from '@openfun/cunningham-react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useEffect, useState } from 'react';
import { FormProvider, useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';

import { ResanaConnectSection } from '@/components/ResanaConnect/ResanaConnectSection';
import { WorkspacePreExport } from '@/components/Workspace/Workspace';
import { useApi } from '@/hooks/useApi';
import { useAvailableDestinations } from '@/hooks/useAvailableDestinations';
import { useResanaAuthStatus } from '@/hooks/useResanaAuthStatus';
import { useWorkspaces } from '@/hooks/useWorkspaces';

export interface IForm {
  workspaces: Record<string, string[]>;
}

export default function Prepare() {
  const router = useRouter();
  const [isLoading, setIsLoading] = useState(false);
  const { workspaces, fetch, hasError } = useWorkspaces();
  const { fetchApi } = useApi();
  const { destinations } = useAvailableDestinations();
  const {
    connected: resanaConnected,
    check: checkResana,
    markConnected: markResanaConnected,
  } = useResanaAuthStatus();
  const searchParams = useSearchParams();

  useEffect(() => {
    try {
      const ids = searchParams.get('workspaces_ids')!.split(',');
      // "".split(",") === [""]
      if (ids.length === 0 || (ids.length === 1 && !ids[0])) {
        throw new Error('No workspaces ids provided');
      }
      void fetch({ ids });
    } catch (e) {
      router.replace('/');
    }
  }, []);

  useEffect(() => {
    if (workspaces?.some((ws) => ws.source_type === 'resana')) {
      void checkResana();
    }
  }, [workspaces]);

  const { t } = useTranslation();

  const validate = (data: IForm) => {
    const errors: any = {};

    if (data.workspaces) {
      Object.entries(data.workspaces).forEach(([id, values]) => {
        const isValid = Array.isArray(values) ? values.length > 0 : !!values;
        if (isValid) {
          return;
        }
        if (!errors.workspaces) {
          errors.workspaces = {};
        }
        errors.workspaces[id] = t("Au moins un type d'export doit être coché");
      });
    }

    return {
      values: data,
      errors,
    };
  };

  const submit = async (data: IForm) => {
    setIsLoading(true);
    try {
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      const response = await fetchApi('workspaces/process', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          workspaces: Object.fromEntries(
            Object.entries(data.workspaces).map(([id, values]) => [
              id,
              Array.isArray(values) ? values : [values],
            ]),
          ),
        }),
      });
      router.replace('/finish');
    } catch (e) {
      setIsLoading(false);
    }
  };

  const methods = useForm<IForm>({
    mode: 'onSubmit',
    reValidateMode: 'onChange',
    defaultValues: {},
    resolver: validate,
  });

  if (hasError) {
    return null;
  }

  const hasResanaSource =
    workspaces?.some((ws) => ws.source_type === 'resana') ?? false;
  const needsResanaConnect = hasResanaSource && resanaConnected === false;
  const displayLoader =
    isLoading ||
    !workspaces ||
    !destinations ||
    (hasResanaSource && resanaConnected === null);

  return (
    <div className="container">
      {displayLoader ? (
        <div className="container__loader">
          <Loader size="medium" />
        </div>
      ) : needsResanaConnect ? (
        <ResanaConnectSection onConnected={markResanaConnected} />
      ) : (
        <FormProvider {...methods}>
          {/* eslint-disable-next-line @typescript-eslint/no-misused-promises */}
          <form onSubmit={methods.handleSubmit(submit)}>
            <Alert>
              <div>
                {t(
                  "Sélectionnez au moins une destination d'export pour chaque communauté. Vous recevrez un mail de confirmation une fois la migration terminée.",
                )}
              </div>
            </Alert>
            <div className="suite__workspaces mt-s">
              {workspaces.map((workspace) => (
                <WorkspacePreExport
                  workspace={workspace}
                  key={workspace.id}
                  destinations={destinations!}
                />
              ))}
            </div>
            <div className="suite__workspaces__footer">
              <Button>{t('Lancer les exports')}</Button>
            </div>
          </form>
        </FormProvider>
      )}
    </div>
  );
}

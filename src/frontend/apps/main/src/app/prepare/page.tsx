'use client';
import { Alert, Button, Loader } from '@openfun/cunningham-react';
import { useRouter } from 'next/navigation';
import { useEffect } from 'react';
import { FormProvider, useForm } from 'react-hook-form';
import { useTranslation } from 'react-i18next';

import { WorkspacePreExport } from '@/components/Workspace/Workspace';
import { useApi } from '@/hooks/useApi';
import { useWorkspaces } from '@/hooks/useWorkspaces';

export interface IForm {
  workspaces: Record<string, string[]>;
}

export default function Prepare({
  searchParams,
}: {
  searchParams: { workspaces_ids: string };
}) {
  const router = useRouter();
  const { workspaces, fetch, hasError } = useWorkspaces();
  const { fetchApi } = useApi();

  useEffect(() => {
    try {
      const ids = searchParams.workspaces_ids.split(',');
      // "".split(",") === [""]
      if (ids.length === 0 || (ids.length === 1 && !ids[0])) {
        throw new Error('No workspaces ids provided');
      }
      void fetch({ ids });
    } catch (e) {
      router.replace('/');
    }
  }, []);

  const { t } = useTranslation();

  const validate = (data: IForm) => {
    const errors: any = {};

    if (data.workspaces) {
      Object.entries(data.workspaces).forEach(([id, values]) => {
        const isValid = Array.isArray(values) && values.length > 0;
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
    const response = await fetchApi('workspaces/process', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        workspaces: data.workspaces,
      }),
    });
    router.replace('/finish');
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

  return (
    <div className="container">
      {workspaces ? (
        <FormProvider {...methods}>
          <form onSubmit={methods.handleSubmit(submit)}>
            <Alert>
              <div>
                {t("Vous pouvez choisir jusqu'à deux types d'exports pour chaque communauté: ")}
                <ul>
                  <li>{t("Un export de type 'Archive' qui vous permettra de récupérer l'ensemble des données de votre communauté dans un fichier compressé. Un lien de téléchargement vous sera envoyé par mail une fois que l'achive sera prête.")}</li>
                  <li className="mt-s">{t("Un export de type 'Resana' qui vous permettra de migrer votre communauté vers Resana, l'espace de travail sera créé automatiquement sur Resana. Vous pourrez retrouver votre communauté sur la plateforme Resana une fois la migration terminée, nous vous enverrons un mail pour vous avertir.")}</li>
                </ul>
              </div>
            </Alert>
            <div className="suite__workspaces mt-s">
              {workspaces.map((workspace) => (
                <WorkspacePreExport workspace={workspace} key={workspace.id} />
              ))}
            </div>
            <div className="suite__workspaces__footer">
              <Button>{t('Lancer les exports')}</Button>
            </div>
          </form>
        </FormProvider>
      ) : (
        <div className="container__loader">
          <Loader size="medium" />
        </div>
      )}
    </div>
  );
}

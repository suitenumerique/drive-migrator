'use client';

import { Button, Loader } from '@openfun/cunningham-react';
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Workspace, WorkspaceStatus } from '@/components/Workspace/Workspace';

import './page.scss';

import { useApi } from '@/hooks/useApi';

export type WorkspaceByStatus = Record<WorkspaceStatus, Workspace[]>;

const IS_LONG_TIME = 5000;
const FAKE_DELAY = 0;

export default function Home() {
  const { t } = useTranslation();
  const [isSynchronizing, setIsSynchronizing] = useState(true);
  const { fetchApi, hasError } = useApi();
  const [isLong, setIsLong] = useState(false);

  const synchronize = async () => {
    const startTime = new Date();
    const timeElapsedInterval = setInterval(() => {
      const timeElapsed = new Date().getTime() - startTime.getTime();
      if (timeElapsed > IS_LONG_TIME) {
        setIsLong(true);
      }
    }, 1000);

    await fetchApi('synchronize/', undefined, {
      closableError: false,
    });
    setTimeout(() => {
      setIsSynchronizing(false);
      clearInterval(timeElapsedInterval);
    }, FAKE_DELAY);
  };

  useEffect(() => {
    void synchronize();
  }, []);

  if (hasError) {
    return null;
  }

  return (
    <div className="container container--center">
      {isSynchronizing ? (
        <>
          <Loader size="medium" />
          <h1>{t('Synchronisation en cours ...')}</h1>
          <p>
            {t('Nous récuperons les informations de votre compte Osmose ...')}
          </p>
          {isLong && (
            <p>
              {t(
                'Cela prend un peu plus de temps avec votre compte, merci de patienter cela peut prendre plusieurs minutes ...',
              )}
            </p>
          )}
        </>
      ) : (
        <>
          <h1>{t('Synchronisation réussie !')}</h1>
          <p>
            {t(
              'Nous avons bien récupéré les informations liées à votre compte Osmose !',
            )}
          </p>
          <Button href="/dashboard" className="mt-s">
            {t('Consulter mes communautés')}
          </Button>
        </>
      )}
    </div>
  );
}

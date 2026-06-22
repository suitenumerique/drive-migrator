import {
  ModalSize,
  VariantType,
  useModals,
} from '@gouvfr-lasuite/cunningham-react';
import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { fetchAPI, fetchAPIOptions } from '@/api/fetchApi';
import { baseApiUrl } from '@/core/conf';
import {
  MIGRATION_TARGET_STORAGE_KEY,
  getConnectPath,
  isMigrationTarget,
} from '@/core/migrationTarget';

const GENERIC_API_ERROR = 'generic_api_error';
const DRIVE_TOKEN_REQUIRED = 'DriveTokenRequired';
const RESANA_TOKEN_REQUIRED = 'ResanaTokenRequired';

class APIError extends Error {
  data: any;

  constructor(data: any) {
    super();
    this.data = data;
  }
}

const fetchWithException = async (...args: Parameters<typeof fetchAPI>) => {
  const response = await fetchAPI(...args);
  if (response.ok) {
    return response;
  }
  try {
    const data = await response.json();
    throw new APIError(data);
  } catch (e) {
    if (e instanceof APIError) {
      throw e;
    }
    throw new APIError({});
  }
};

export const useApi = () => {
  const modals = useModals();
  const { t } = useTranslation();
  const [hasError, setHasError] = useState(false);

  const fetchAPIProxy = useCallback(
    async (
      input: string,
      init?: RequestInit,
      options?: fetchAPIOptions & { closableError?: boolean },
    ) => {
      const showError = (errorName: string, closable = true) => {
        // The following comment are used by i18next-parser ( i18n:extract )
        // t('api_errors.generic_api_error')
        // t('api_errors.WorkspaceAlreadyExporting')
        // t('api_errors.DriveTokenRequired')
        // t('api_errors.ResanaTokenRequired')
        if (errorName === DRIVE_TOKEN_REQUIRED) {
          window.location.replace(new URL('authenticate/', baseApiUrl()).href);
          return;
        }
        if (errorName === RESANA_TOKEN_REQUIRED) {
          const stored = sessionStorage.getItem(MIGRATION_TARGET_STORAGE_KEY);
          const target = isMigrationTarget(stored)
            ? stored
            : 'lasuite-fichiers';
          window.location.replace(getConnectPath(target));
          return;
        }
        const errorMessage = t('api_errors.' + errorName);
        void modals.messageModal({
          messageType: VariantType.ERROR,
          title: t('Oups ... erreur 😕'),
          children: (
            <>
              <p>{errorMessage}</p>
              <p>
                {t(
                  "N'hésitez pas à contacter le support si le problème persiste",
                )}
              </p>
            </>
          ),
          size: ModalSize.MEDIUM,
          ...(closable
            ? {}
            : {
                closeOnEsc: false,
                closeOnClickOutside: false,
                hideCloseButton: true,
                actions: null,
              }),
        });
      };

      try {
        const response = await fetchWithException(input, init, options);
        setHasError(false);
        return response;
      } catch (error) {
        setHasError(true);
        console.error(error);
        let errorName = GENERIC_API_ERROR;
        if (error instanceof APIError) {
          const apiErrorName =
            error.data?.detail?.error_name ?? error.data?.error_name;
          if (apiErrorName) {
            errorName = apiErrorName;
          }
        }
        showError(errorName, options?.closableError);
        throw error;
      }
    },
    [modals, t],
  );

  return { fetchApi: fetchAPIProxy, hasError };
};

import { ModalSize, VariantType, useModals } from '@openfun/cunningham-react';
import { useState } from 'react';
import { useTranslation } from 'react-i18next';

import { fetchAPI, fetchAPIOptions } from '@/api/fetchApi';

const GENERIC_API_ERROR = 'generic_api_error';

class APIError extends Error {
  data: any;

  constructor(data: any) {
    super();
    this.data = data;
  }
}

export const useApi = () => {
  const modals = useModals();
  const { t } = useTranslation();
  const [hasError, setHasError] = useState(false);

  const showError = (errorName: string, closable = true) => {
    // The following comment are used by i18next-parser ( i18n:extract )
    // t('api_errors.generic_api_error')
    // t('api_errors.WorkspaceAlreadyExporting')
    const errorMessage = t('api_errors.' + errorName);
    void modals.messageModal({
      messageType: VariantType.ERROR,
      title: t('Oups ... erreur 😕'),
      children: (
        <>
          <p>{errorMessage}</p>
          <p>
            {t("N'hésitez pas à contacter le support si le problème persiste")}
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

  const fetchWithException = async (...args: Parameters<typeof fetchAPI>) => {
    const response = await fetchAPI(...args);
    if (response.ok) {
      return response;
    }
    const data = await response.json();
    throw new APIError(data);
  };

  const fetchAPIProxy = async (
    input: string,
    init?: RequestInit,
    options?: fetchAPIOptions & { closableError?: boolean },
  ) => {
    try {
      const response = await fetchWithException(input, init, options);
      setHasError(false);
      return response;
    } catch (error) {
      setHasError(true);
      console.error(error);
      let errorName = GENERIC_API_ERROR;
      if (error instanceof APIError) {
        if (error.data.error_name) {
          errorName = error.data.error_name;
        }
      }
      showError(errorName, options?.closableError);
      throw error;
    }
  };

  return { fetchApi: fetchAPIProxy, hasError };
};

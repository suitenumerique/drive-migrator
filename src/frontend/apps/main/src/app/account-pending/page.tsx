'use client';

import { useTranslation } from 'react-i18next';

export default function AccountPending() {
  const { t } = useTranslation();

  return (
    <div className="container">
      <h1>{t('Votre compte est en attente de validation')}</h1>
      <p>
        {t(
          'Votre compte a bien été créé. Un administrateur doit valider votre accès avant que vous puissiez utiliser cet outil.',
        )}
      </p>
    </div>
  );
}

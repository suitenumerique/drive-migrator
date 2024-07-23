'use client';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/Button/Button';

export default function NotFound() {
  const { t } = useTranslation();

  return (
    <div className="container">
      <h1 className="clr-greyscale-900 fs-h1">{t('Page non trouvée')}</h1>
      <h3 className="clr-greyscale-700 fw-light fs-l">{t('Erreur 404')}</h3>
      <p className="fs-h4 fw-regular">
        {t(
          'La page que vous cherchez est introuvable. Excusez-nous pour la gêne occasionnée.',
        )}
      </p>
      <Button href="/">{t("Page d'accueil")}</Button>
    </div>
  );
}

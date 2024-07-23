import React from 'react';
import { useTranslation } from 'react-i18next';

import { DropButton } from '@/components/DropButton/DropButton';
import { logout } from '@/core/auth/Auth';

import { Button } from '../Button/Button';

export const AccountDropdown = () => {
  const { t } = useTranslation();

  return (
    <DropButton
      aria-label={t('My account')}
      button={
        <div className="suite__acount-dropdown__inner clr-primary-text">
          <div>{t('My account')}</div>
          <div className="material-icons">arrow_drop_down</div>
        </div>
      }
    >
      <ul>
        <li>
          <Button
            href="/"
            color="primary-text"
            icon={<span className="material-icons">sync</span>}
            aria-label={t('Synchroniser Osmose')}
          >
            {t('Synchroniser Osmose')}
          </Button>
        </li>
        <li>
          <Button
            onClick={logout}
            color="primary-text"
            icon={<span className="material-icons">logout</span>}
            aria-label={t('Déconnexion')}
          >
            {t('Déconnexion')}
          </Button>
        </li>
      </ul>
    </DropButton>
  );
};

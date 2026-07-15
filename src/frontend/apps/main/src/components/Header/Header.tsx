'use client';

import Link from 'next/link';
import { useTranslation } from 'react-i18next';

import MigratorLogo from '@/assets/images/logo.svg';

import './Header.scss';
import { LaGaufre } from './LaGaufre';

export const Header = () => {
  const { t } = useTranslation();

  return (
    <header className="suite__header">
      <div className="suite__header__inner">
        <Link
          href="/"
          className="suite__header__logo"
          aria-label={t('Migrateur - Outil de migration')}
        >
          <MigratorLogo className="suite__header__logo-img" aria-hidden />
        </Link>
        <div className="suite__header__actions">
          <LaGaufre />
        </div>
      </div>
    </header>
  );
};

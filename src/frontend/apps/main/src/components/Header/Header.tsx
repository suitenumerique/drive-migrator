'use client';

import Link from 'next/link';
import { useTranslation } from 'react-i18next';

import MigratorLogoHeader from '@/assets/images/logo-header.svg';

import './Header.scss';
import { LaGaufre } from './LaGaufre';

export const Header = () => {
  const { t } = useTranslation();

  return (
    <header className="header">
      <div className="header__inner">
        <Link
          href="/"
          className="header__logo"
          aria-label={t('Migrateur - Outil de migration')}
        >
          <MigratorLogoHeader className="header__logo-img" aria-hidden />
        </Link>
        <div className="header__actions">
          <LaGaufre />
        </div>
      </div>
    </header>
  );
};

'use client';

import { Button } from '@gouvfr-lasuite/cunningham-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useTranslation } from 'react-i18next';

import MigratorLogoHeader from '@/assets/images/logo-header.svg';
import { normalizePath } from '@/core/auth/publicRoutes';
import { useAdminContext } from '@/hooks/useAdminContext';

import './Header.scss';
import { LaGaufre } from './LaGaufre';

export const Header = () => {
  const { t } = useTranslation();
  const pathname = normalizePath(usePathname());
  const { result } = useAdminContext();
  const isAdmin = result?.kind === 'ready' && result.data.is_organization_admin;

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
          {isAdmin && (
            <nav className="header__views" aria-label={t('Vues')}>
              <Button
                href="/dashboard/"
                className="header__view"
                variant="tertiary"
                aria-current={pathname === '/dashboard' ? 'page' : undefined}
                icon={<span className="material-icons">person</span>}
              >
                {t('Mes espaces')}
              </Button>
              <Button
                href="/admin/"
                className="header__view"
                variant="tertiary"
                aria-current={pathname === '/admin' ? 'page' : undefined}
                icon={
                  <span className="material-icons">admin_panel_settings</span>
                }
              >
                {t('Administration')}
              </Button>
            </nav>
          )}
          <LaGaufre />
        </div>
      </div>
    </header>
  );
};

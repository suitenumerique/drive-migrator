'use client';

import { Button, Option, Select } from '@gouvfr-lasuite/cunningham-react';
import Image from 'next/image';
import { useRouter } from 'next/navigation';
import { FC, SVGProps, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

import FichiersLogo from '@/assets/icons/fichier-mono.svg';
import ZipLogo from '@/assets/icons/icon-zip.svg';
import ResanaLogo from '@/assets/icons/resana-mono.svg';
import MigratorLogo from '@/assets/images/logo.svg';
import migrationHero from '@/assets/images/migration.png';
import { ArrowRightIcon } from '@/components/icons/ArrowRightIcon';
import { login, useAuth } from '@/core/auth/Auth';
import {
  MIGRATION_TARGET_STORAGE_KEY,
  MigrationTarget,
  getConnectPath,
} from '@/core/migrationTarget';

import './page.scss';

const DOCUMENTATION_URL =
  'https://docs.numerique.gouv.fr/docs/9e4cebe6-4138-4d02-a4ce-c10108995156/';

const SOURCE_TOOL = 'resana';

const toolOptionRender = (Icon: FC<SVGProps<SVGSVGElement>>, label: string) => {
  const ToolOption = () => (
    <span className="migration-landing__select-option">
      <Icon width={24} height={24} aria-hidden />
      {label}
    </span>
  );
  ToolOption.displayName = 'ToolOption';
  return ToolOption;
};

const SOURCE_OPTIONS: Option[] = [
  {
    value: SOURCE_TOOL,
    label: 'Resana',
    render: toolOptionRender(ResanaLogo, 'Resana'),
  },
];

export default function Home() {
  const { t } = useTranslation();
  const router = useRouter();
  const { isAuthenticated, isAuthPending } = useAuth();
  const [targetTool, setTargetTool] =
    useState<MigrationTarget>('lasuite-fichiers');

  const targetOptions = useMemo<Option[]>(
    () => [
      {
        value: 'lasuite-fichiers',
        label: 'LaSuite Fichiers',
        render: toolOptionRender(FichiersLogo, 'LaSuite Fichiers'),
      },
      {
        value: 'archive-zip',
        label: t('Archive zip'),
        render: toolOptionRender(ZipLogo, t('Archive zip')),
      },
    ],
    [t],
  );

  const handleStartMigration = () => {
    sessionStorage.setItem(MIGRATION_TARGET_STORAGE_KEY, targetTool);
    const connectPath = getConnectPath(targetTool);

    if (isAuthenticated) {
      router.push(connectPath);
      return;
    }

    login(connectPath);
  };

  return (
    <div className="migration-landing container">
      <div className="migration-landing__hero" aria-hidden>
        <Image
          src={migrationHero}
          alt=""
          className="migration-landing__hero-img"
          priority
        />
      </div>

      <h1 className="migration-landing__title">
        <MigratorLogo
          className="migration-landing__title-logo"
          width={224}
          height={60}
          role="img"
          aria-label={t('Migrateur - Outil de migration')}
        />
      </h1>

      <p className="migration-landing__description">
        {t('Migrez vos données d’un outil vers un autre.')}
        <br />
        {t('D’autres outils seront ajoutés progressivement.')}
      </p>

      <div className="migration-landing__selectors">
        <Select
          className="migration-landing__field"
          label={t('Depuis')}
          variant="classic"
          options={SOURCE_OPTIONS}
          value={SOURCE_TOOL}
          disabled
          clearable={false}
          showLabelWhenSelected={false}
        />

        <ArrowRightIcon className="migration-landing__arrow" />

        <Select
          className="migration-landing__field"
          label={t('Vers')}
          variant="classic"
          options={targetOptions}
          value={targetTool}
          onChange={(event) =>
            setTargetTool(event.target.value as MigrationTarget)
          }
          clearable={false}
          showLabelWhenSelected={false}
        />
      </div>

      <Button
        className="migration-landing__cta"
        fullWidth
        onClick={handleStartMigration}
        variant="primary"
        color="brand"
        disabled={isAuthPending}
      >
        {t('Démarrer la migration')}
      </Button>

      <Button
        fullWidth
        href={DOCUMENTATION_URL}
        target="_blank"
        rel="noopener noreferrer"
        variant="tertiary"
        color="neutral"
      >
        {t('Voir la documentation')}
      </Button>
    </div>
  );
}

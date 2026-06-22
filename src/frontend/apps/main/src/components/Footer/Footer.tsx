'use client';

import Link from 'next/link';
import React from 'react';
import { useTranslation } from 'react-i18next';
import styled from 'styled-components';

import { AppHelpMenu } from '@/components/AppHelpMenu/AppHelpMenu';
import LogoGouv from '@/components/LogoGouv/LogoGouv';

import './Footer.scss';
import IconLink from './assets/external-link.svg';

const BlueStripe = styled.div`
  position: absolute;
  height: 2px;
  width: 100%;
  background: var(--c--theme--colors--primary-600);
  top: 0;
`;

interface LinkProps {
  $css?: string;
}

const StyledLink = styled(Link)<LinkProps>`
  text-decoration: none;
  display: flex;
  ${({ $css }) => $css && `${$css};`}
`;

export const Footer = () => {
  const { t } = useTranslation();

  return (
    <footer className="suite__footer">
      <div className="suite__footer__help">
        <AppHelpMenu />
      </div>
      <BlueStripe />
      <div className="suite__footer__content">
        <div className="suite__footer__content__top">
          <div>
            <div className="suite__footer__content__top__logo">
              <LogoGouv imagesWidth={70} />
            </div>
          </div>
          <div className="suite__footer__content__top__links">
            {[
              {
                label: 'legifrance.gouv.fr',
                href: 'https://legifrance.gouv.fr/',
              },
              {
                label: 'info.gouv.fr',
                href: 'https://info.gouv.fr/',
              },
              {
                label: 'service-public.fr',
                href: 'https://service-public.fr/',
              },
              {
                label: 'data.gouv.fr',
                href: 'https://data.gouv.fr/',
              },
            ].map(({ label, href }) => (
              <Link key={label} href={href} target="__blank">
                <span>{label}</span>
                <IconLink width={18} />
              </Link>
            ))}
          </div>
        </div>
        <div className="suite__footer__content__middle">
          {[
            {
              label: t('Mentions Légales'),
              href: 'https://resana.numerique.gouv.fr/public/information/consulterAccessUrl?cle_url=1670797648AmgPbwQIBjpRPABmUT9QcAY4DjMLKgduDGdXagFgWGFQZ1RjVjJXOgA2UGUANg==',
            },
            {
              label: t('Données personnelles et cookies'),
              href: '/personal-data-cookies',
            },
            {
              label: t('Accessibilité'),
              href: '/accessibility',
            },
            {
              label: t('CGU'),
              href: 'https://resana.numerique.gouv.fr/public/information/consulterAccessUrl?cle_url=149823049AmhQMFRYAz8BbAdhAmxVdQQ6XGFSc1M6DGdTbgdmW2JXYAQ0WzEIZVNmU2BRaQ==',
            },
          ].map(({ label, href }) => (
            <Link key={label} href={href}>
              <span>{label}</span>
            </Link>
          ))}
        </div>
        <p className="suite__footer__content__mention">
          {t('Sauf mention contraire, tout le contenu de ce site est sous')}{' '}
          <StyledLink
            href="https://github.com/etalab/licence-ouverte/blob/master/LO.md"
            target="__blank"
          >
            <span>licence etalab-2.0</span>
            <IconLink width={18} />
          </StyledLink>
        </p>
      </div>
    </footer>
  );
};

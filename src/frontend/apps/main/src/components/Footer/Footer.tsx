import React from 'react';
import { useTranslation } from 'react-i18next';
import styled from 'styled-components';
import Link from 'next/link';

import IconLink from './assets/external-link.svg';
import LogoGouv from '@/components/LogoGouv/LogoGouv';

import "./Footer.scss"

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
          <BlueStripe />
          <div className="suite__footer__content">
              <div className="suite__footer__content__top">
                  <div>
                      <div className="suite__footer__content__top__logo">
                          <LogoGouv
                            imagesWidth={70}
                          />
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
                        <Link
                          key={label}
                          href={href}
                          target="__blank"
                        >
                            <span>{label}</span>
                            <IconLink width={18} />
                        </Link>
                      ))}
                  </div>
              </div>
              <div className="suite__footer__content__middle">
                  {[
                      {
                          label: t('Legal Notice'),
                          href: '/legal-notice',
                      },
                      {
                          label: t('Personal data and cookies'),
                          href: '/personal-data-cookies',
                      },
                      {
                          label: t('Accessibility'),
                          href: '/accessibility',
                      },
                  ].map(({ label, href }) => (
                    <Link
                      key={label}
                      href={href}
                    >
                        <span>{label}</span>
                    </Link>
                  ))}
              </div>
              <p className="suite__footer__content__mention">
                  {t('Unless otherwise stated, all content on this site is under')}{' '}
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

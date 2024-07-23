import Image from 'next/image';
import React from 'react';
import { useTranslation } from 'react-i18next';
import styled from 'styled-components';

import LogoGouv from '@/components/LogoGouv/LogoGouv';

import { AccountDropdown } from './AccountDropdown';
import { LaGaufre } from './LaGaufre';
import { default as IconDocs } from './assets/icon-docs.svg?url';

export const HEADER_HEIGHT = '100px';

import './Header.scss';

const RedStripe = styled.div`
  position: absolute;
  height: 5px;
  width: 100%;
  background: var(--c--theme--colors--danger-500);
  top: 0;
`;

export const Header = () => {
  const { t } = useTranslation();

  return (
    <header className="suite__header">
      <RedStripe />
      <div className="suite__header__inner">
        <div className="suite__header__left">
          <LogoGouv />
          <a href="/">
            <div className="suite__header__title">
              <h2 className="clr-primary-text">Osmose Migration Tool</h2>
            </div>
          </a>
        </div>
        <div className="suite__header__right">
          <AccountDropdown />
          <LaGaufre />
        </div>
      </div>
    </header>
  );
};

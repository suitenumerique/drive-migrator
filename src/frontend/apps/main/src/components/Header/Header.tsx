import React from 'react';
import styled from 'styled-components';

import LogoGouv from '@/components/LogoGouv/LogoGouv';

import { AccountDropdown } from './AccountDropdown';
import './Header.scss';
import { LaGaufre } from './LaGaufre';
export const HEADER_HEIGHT = '100px';

const RedStripe = styled.div`
  position: absolute;
  height: 5px;
  width: 100%;
  background: var(--c--theme--colors--danger-500);
  top: 0;
`;

export const Header = () => {
  return (
    <header className="suite__header">
      <RedStripe />
      <div className="suite__header__inner">
        <div className="suite__header__left">
          <LogoGouv />
          <a href="/">
            <div className="suite__header__title">
              <h2 className="clr-primary-text">Migration Osmose</h2>
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

'use client';

import Image from 'next/image';
import Link from 'next/link';

import './Header.scss';
import { LaGaufre } from './LaGaufre';

const LA_SUITE_LOGO_URL =
  'https://lasuite.numerique.gouv.fr/assets/lasuite.svg';

export const Header = () => (
  <header className="suite__header">
    <div className="suite__header__inner">
      <Link href="/" className="suite__header__logo" title="LaSuite">
        <Image
          src={LA_SUITE_LOGO_URL}
          alt="LaSuite"
          width={120}
          height={32}
          priority
        />
      </Link>
      <div className="suite__header__actions">
        <LaGaufre />
      </div>
    </div>
  </header>
);

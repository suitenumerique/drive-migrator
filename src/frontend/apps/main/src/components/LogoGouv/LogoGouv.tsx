import Image from 'next/image';
import React from 'react';
import { useTranslation } from 'react-i18next';

import './LogoGouv.scss';

import { default as IconDevise } from '@/assets/icons/icon-devise.svg?url';
import { default as IconMarianne } from '@/assets/icons/icon-marianne.svg?url';

interface LogoGouvProps {
  imagesWidth?: number;
}

const LogoGouv = ({ imagesWidth }: LogoGouvProps) => {
  const { t } = useTranslation();

  return (
    <div className="suite__logo-gouv">
      <div>
        <Image
          priority
          src={IconMarianne}
          alt={t('Marianne Logo')}
          width={imagesWidth}
        />
      </div>
      <div className="fs-t fw-bold">Gouvernement</div>
      <Image
        width={imagesWidth}
        priority
        src={IconDevise}
        alt={t('Freedom Equality Fraternity Logo')}
      />
    </div>
  );
};

export default LogoGouv;

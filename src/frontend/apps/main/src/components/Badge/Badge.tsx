import { VariantType } from '@openfun/cunningham-react';
import { PropsWithChildren } from 'react';

import './Badge.scss';

export const Badge = ({
  variant,
  children,
}: PropsWithChildren & {
  variant: VariantType;
}) => {
  return (
    <div className={'suite__badge suite__badge--' + variant}>{children}</div>
  );
};

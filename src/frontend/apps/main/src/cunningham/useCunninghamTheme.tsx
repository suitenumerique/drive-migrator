import { tokens } from './cunningham-tokens';

export type Theme = keyof typeof tokens.themes;

const DEFAULT_THEME: Theme = 'default';

const isTheme = (value: string): value is Theme =>
  Object.prototype.hasOwnProperty.call(tokens.themes, value);

/** Thème Cunningham lu depuis la config d'environnement (NEXT_PUBLIC_FRONTEND_THEME). */
export const getFrontendTheme = (): Theme => {
  const configured = process.env.NEXT_PUBLIC_FRONTEND_THEME ?? DEFAULT_THEME;

  return isTheme(configured) ? configured : DEFAULT_THEME;
};

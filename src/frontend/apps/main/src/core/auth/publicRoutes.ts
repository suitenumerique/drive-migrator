const normalizePath = (pathname: string) => {
  const trimmed = pathname.replace(/\/$/, '');
  return trimmed || '/';
};

const PUBLIC_PATHS = new Set([
  '/',
  '/connect',
  '/connect-resana',
  '/legal-notice',
  '/accessibility',
  '/personal-data-cookies',
  '/account-pending',
]);

export const isPublicPath = (pathname: string) =>
  PUBLIC_PATHS.has(normalizePath(pathname));

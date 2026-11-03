import { isPublicPath, normalizePath } from '../publicRoutes';

describe('normalizePath', () => {
  it('drops a trailing slash', () => {
    expect(normalizePath('/connect/')).toBe('/connect');
  });

  it('keeps the root path', () => {
    expect(normalizePath('/')).toBe('/');
    expect(normalizePath('')).toBe('/');
  });
});

describe('isPublicPath', () => {
  it.each([
    '/',
    '/connect',
    '/connect/',
    '/connect-resana',
    '/legal-notice',
    '/accessibility',
    '/personal-data-cookies',
    '/account-pending',
  ])('accepts %s', (pathname) => {
    expect(isPublicPath(pathname)).toBe(true);
  });

  it.each(['/dashboard', '/connect/extra', '/Connect', '/finish'])(
    'rejects %s',
    (pathname) => {
      expect(isPublicPath(pathname)).toBe(false);
    },
  );
});

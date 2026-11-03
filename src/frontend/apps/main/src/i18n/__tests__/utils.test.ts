import { BASE_LANGUAGE, LANGUAGE_LOCAL_STORAGE } from '../conf';
import { getLanguage, splitLocaleCode } from '../utils';

describe('splitLocaleCode', () => {
  it('reads a language without a region', () => {
    expect(splitLocaleCode('fr')).toEqual({
      language: 'fr',
      region: undefined,
    });
  });

  it.each([
    ['en-US', 'en', 'US'],
    ['fr_FR', 'fr', 'FR'],
  ])('splits %s into language and region', (code, language, region) => {
    expect(splitLocaleCode(code)).toEqual({ language, region });
  });

  it('ignores a region when the code has more than two parts', () => {
    expect(splitLocaleCode('zh-Hans-CN')).toEqual({
      language: 'zh',
      region: undefined,
    });
  });
});

describe('getLanguage', () => {
  const setNavigatorLanguage = (language: string) => {
    Object.defineProperty(window.navigator, 'language', {
      configurable: true,
      value: language,
    });
  };

  beforeEach(() => {
    localStorage.clear();
    setNavigatorLanguage('de');
  });

  afterAll(() => {
    // @ts-expect-error removing the own property restores jsdom's getter
    delete window.navigator.language;
  });

  it('uses a stored language when it is allowed', () => {
    localStorage.setItem(LANGUAGE_LOCAL_STORAGE, 'en');

    expect(getLanguage()).toBe('en');
  });

  it('keeps only the language part of a stored locale', () => {
    localStorage.setItem(LANGUAGE_LOCAL_STORAGE, 'en-GB');

    expect(getLanguage()).toBe('en');
  });

  it('falls back to the base language when the stored language is not allowed', () => {
    localStorage.setItem(LANGUAGE_LOCAL_STORAGE, 'de');

    expect(getLanguage()).toBe(BASE_LANGUAGE);
  });

  it('reads the browser language when nothing is stored', () => {
    setNavigatorLanguage('en-US');

    expect(getLanguage()).toBe('en');
  });

  it('prefers the stored language over the browser language', () => {
    localStorage.setItem(LANGUAGE_LOCAL_STORAGE, 'fr');
    setNavigatorLanguage('en-US');

    expect(getLanguage()).toBe('fr');
  });
});

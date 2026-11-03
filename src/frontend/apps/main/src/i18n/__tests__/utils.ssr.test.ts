/**
 * @jest-environment node
 *
 * jsdom's window is the global object, so a test running there cannot make
 * `typeof window` undefined. This file loads the helper where window is absent.
 */
import { BASE_LANGUAGE } from '../conf';
import { getLanguage } from '../utils';

describe('getLanguage without a window', () => {
  afterEach(() => {
    delete (global as { navigator?: unknown }).navigator;
    delete (global as { localStorage?: unknown }).localStorage;
  });

  it('returns the base language even when the browser language would be allowed', () => {
    // An allowed language with nothing stored: skipping the window guard
    // would return "en" instead of the base language.
    Object.defineProperty(global, 'navigator', {
      configurable: true,
      value: { language: 'en' },
    });
    Object.defineProperty(global, 'localStorage', {
      configurable: true,
      value: { getItem: () => null },
    });

    expect(getLanguage()).toBe(BASE_LANGUAGE);
  });
});

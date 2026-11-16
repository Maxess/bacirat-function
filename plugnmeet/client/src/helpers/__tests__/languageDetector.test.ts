import { afterEach, describe, expect, it, vi } from 'vitest';
import { createInstance } from 'i18next';
import languages from '../languages';
import {
  DEFAULT_LANGUAGE,
  LANGUAGE_DETECTION_ORDER,
  createLanguageDetector,
} from '../languageDetector';

/**
 * The language detector reads window / document / navigator. The suite runs in plain Node (no jsdom,
 * like the other helper tests), so minimal stand-ins are installed per test.
 */
const createStorage = () => {
  const data = new Map<string, string>();
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => void data.set(key, String(value)),
    removeItem: (key: string) => void data.delete(key),
  };
};

interface FakeBrowserOptions {
  /** the URL query string, e.g. '?lng=ar-SA' */
  search?: string;
  /** what `navigator.language` reports */
  browserLanguage?: string;
  /** a language the user picked on an earlier visit */
  storedLanguage?: string;
}

const installFakeBrowser = ({
  search = '',
  browserLanguage = 'en-US',
  storedLanguage,
}: FakeBrowserOptions = {}) => {
  const localStorage = createStorage();
  if (storedLanguage) {
    localStorage.setItem('i18nextLng', storedLanguage);
  }
  vi.stubGlobal('window', {
    location: { search, hash: '' },
    localStorage,
    sessionStorage: createStorage(),
  });
  // index.html ships with <html lang="en">, which the `htmlTag` detector would pick up
  vi.stubGlobal('document', {
    cookie: '',
    documentElement: { getAttribute: (name: string) => (name === 'lang' ? 'en' : null) },
  });
  vi.stubGlobal('navigator', { language: browserLanguage, languages: [browserLanguage] });
};

/** Same detector wiring as src/helpers/i18n.ts, minus the HTTP backend. */
const initI18n = async (configuredDefault?: string) => {
  const instance = createInstance();
  await instance.use(createLanguageDetector(configuredDefault)).init({
    fallbackLng: 'en',
    supportedLngs: languages.map((l) => l.code),
    detection: { order: LANGUAGE_DETECTION_ORDER },
    resources: {},
  });
  return instance;
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('UI language detection', () => {
  it('ships the default language', () => {
    expect(languages.map((l) => l.code)).toContain(DEFAULT_LANGUAGE);
  });

  // 'en-US' is what most English browsers report; 'en', 'de-DE', 'ar-SA' and 'tr' are exact shipped
  // locale codes, which is what makes a stray `navigator` lookup visible.
  it.each(['en', 'en-US', 'de-DE', 'ar-SA', 'tr'])(
    'shows Persian on the first visit with a %s browser',
    async (browserLanguage) => {
      installFakeBrowser({ browserLanguage });
      const instance = await initI18n();
      expect(instance.language).toBe('fa-IR');
    },
  );

  it('renders Persian right-to-left', async () => {
    installFakeBrowser();
    const instance = await initI18n();
    expect(instance.dir()).toBe('rtl');
  });

  it('never consults the browser language or the <html lang> attribute', () => {
    expect(LANGUAGE_DETECTION_ORDER).not.toContain('navigator');
    expect(LANGUAGE_DETECTION_ORDER).not.toContain('htmlTag');
  });

  it('keeps the language the user picked earlier', async () => {
    installFakeBrowser({ storedLanguage: 'de-DE' });
    const instance = await initI18n();
    expect(instance.language).toBe('de-DE');
  });

  it('lets ?lng= in the URL override the default', async () => {
    installFakeBrowser({ search: '?lng=ar-SA' });
    const instance = await initI18n();
    expect(instance.language).toBe('ar-SA');
  });

  it('honours a configured default language', async () => {
    installFakeBrowser({ browserLanguage: 'fa-IR' });
    const instance = await initI18n('en');
    expect(instance.language).toBe('en');
    expect(instance.dir()).toBe('ltr');
  });

  it('accepts a short configured code such as "fa"', async () => {
    installFakeBrowser();
    const instance = await initI18n('fa');
    expect(instance.language).toBe('fa-IR');
  });

  it('falls back to English when the configured default is not a shipped locale', async () => {
    installFakeBrowser();
    const instance = await initI18n('xx-YY');
    expect(instance.language).toBe('en');
  });
});

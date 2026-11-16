import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import HttpApi from 'i18next-http-backend';
import { getConfigValue } from './utils';
import languages from './languages';
import {
  DEFAULT_LANGUAGE,
  LANGUAGE_DETECTION_ORDER,
  createLanguageDetector,
} from './languageDetector';

declare const IS_PRODUCTION: boolean;
const assetPath = getConfigValue('staticAssetsPath', '/assets', 'STATIC_ASSETS_PATH');
// Persian by default; `defaultLanguage` in config.js overrides it (e.g. 'en').
const defaultLanguage = getConfigValue<string>('defaultLanguage', DEFAULT_LANGUAGE);

i18n
  .use(createLanguageDetector(defaultLanguage))
  .use(initReactI18next)
  .use(HttpApi)
  .init({
    debug: !IS_PRODUCTION,
    fallbackLng: 'en',
    detection: { order: LANGUAGE_DETECTION_ORDER },
    // Restrict resolution to locale folders we actually ship. Without this,
    // mobile browsers reporting region variants (e.g. en-US, de-DE vs de)
    // leave i18n.languages[0] as the raw variant, which matches no dropdown
    // option and renders the button blank on first open.
    supportedLngs: languages.map((l) => l.code),
    interpolation: {
      escapeValue: false, // not needed for react as it escapes by default
    },
    backend: {
      loadPath: assetPath + '/locales/{{lng}}/{{ns}}.json',
    },
  });

export default i18n;

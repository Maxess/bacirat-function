import LanguageDetector from 'i18next-browser-languagedetector';
import { resolveAvailableLanguageCode } from './languages';

/**
 * UI language used when the user has not chosen one yet.
 * Can be overridden with `defaultLanguage` in config.js (see config_sample.js).
 */
export const DEFAULT_LANGUAGE = 'fa-IR';

/**
 * Lookup order of the UI language, the first match wins:
 *  1. `?lng=xx-XX` in the URL
 *  2. the language the user picked earlier (cookie / localStorage / sessionStorage)
 *  3. the configured default language
 *
 * The browser language (`navigator`) and the `<html lang>` attribute are deliberately NOT consulted:
 * many Persian speakers use an English browser but still expect a Persian interface.
 */
export const LANGUAGE_DETECTION_ORDER = [
  'querystring',
  'cookie',
  'localStorage',
  'sessionStorage',
  'configDefault',
];

/**
 * Browser language detector whose last resort is the configured default language
 * (instead of i18next's `fallbackLng`, which must stay `en` because English is the one
 * locale that always contains every key).
 */
export const createLanguageDetector = (defaultLanguage: string = DEFAULT_LANGUAGE) => {
  // 'fa' -> 'fa-IR', unknown codes -> 'en'
  const resolved = resolveAvailableLanguageCode(defaultLanguage);
  const detector = new LanguageDetector();
  detector.addDetector({
    name: 'configDefault',
    lookup: () => resolved,
  });
  return detector;
};

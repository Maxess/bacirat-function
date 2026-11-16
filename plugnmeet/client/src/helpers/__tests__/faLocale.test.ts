import { describe, expect, it } from 'vitest';
import en from '../../assets/locales/en/translation.json';
import fa from '../../assets/locales/fa-IR/translation.json';

/**
 * Persian is the default UI language of this build and English is the i18next fallback, so a key
 * missing from fa-IR would show up as an English string inside a Persian interface. After merging a
 * newer upstream release this test tells exactly which keys still need a Persian translation.
 */
type Tree = { [key: string]: string | Tree };

const flatten = (tree: Tree, prefix = ''): Record<string, string> => {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(tree)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === 'string') {
      out[path] = value;
    } else {
      Object.assign(out, flatten(value, path));
    }
  }
  return out;
};

const placeholders = (text: string): string =>
  (text.match(/\{\{\s*[\w.]+\s*\}\}/g) ?? [])
    .map((p) => p.replace(/\s+/g, ''))
    .sort()
    .join(',');

const enFlat = flatten(en);
const faFlat = flatten(fa);

describe('fa-IR translation', () => {
  it('has a translation for every English key', () => {
    const missing = Object.keys(enFlat).filter((key) => !(key in faFlat));
    expect(missing).toEqual([]);
  });

  it('has no empty translations', () => {
    const empty = Object.keys(faFlat).filter((key) => faFlat[key].trim() === '');
    expect(empty).toEqual([]);
  });

  it('keeps the {{placeholders}} of the English text', () => {
    const broken = Object.keys(enFlat).filter(
      (key) => key in faFlat && placeholders(enFlat[key]) !== placeholders(faFlat[key]),
    );
    expect(broken).toEqual([]);
  });
});

import { getSdk } from '../sdk';
import { STRINGS } from './strings';

/** Translate a key (English fallback, then the key itself). */
export function t(key: string, vars?: Record<string, string | number>): string {
  return getSdk().t(key, vars);
}

export function registerAllStrings(): void {
  const en: Record<string, string> = {};
  const it: Record<string, string> = {};
  for (const [k, [e, i]] of Object.entries(STRINGS)) {
    en[k] = e;
    it[k] = i;
  }
  getSdk().registerStrings({ en, it });
}

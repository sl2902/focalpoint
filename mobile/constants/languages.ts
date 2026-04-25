export interface Language {
  code: string;
  label: string;
}

export const LANGUAGES: Language[] = [
  { code: 'en', label: 'English' },
  { code: 'ar', label: 'Arabic' },
  { code: 'fr', label: 'French' },
  { code: 'tr', label: 'Turkish' },
  { code: 'es', label: 'Spanish' },
];

export const DEFAULT_LANGUAGE = 'en';

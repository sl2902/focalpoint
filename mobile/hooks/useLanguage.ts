/**
 * Returns the journalist's preferred language code from the settings store.
 * Language is stored in SecureStore and synced into useSettingsStore on hydration.
 */

import { useSettingsStore } from '../store/useSettingsStore';

export function useLanguage(): string {
  return useSettingsStore((s) => s.language);
}

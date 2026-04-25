/**
 * Root layout — wraps every screen.
 * Hydrates settings from SecureStore on mount.
 * Renders DiscreetOverlay on top of all navigation content when active.
 * DiscreetOverlay must live here (not in individual screens) so it
 * persists across tab navigation.
 */

import { useEffect } from 'react';
import { Stack } from 'expo-router';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { DiscreetOverlay } from '../components/DiscreetOverlay';
import { useSettingsStore } from '../store/useSettingsStore';
import { useDiscreetStore } from '../store/useDiscreetStore';

export default function RootLayout() {
  const hydrate = useSettingsStore((s) => s.hydrate);
  const discreetMode = useSettingsStore((s) => s.discreetMode);
  const setDiscreetMode = useDiscreetStore((s) => s.setDiscreetMode);

  useEffect(() => {
    hydrate().then(() => {
      // Sync persisted discreet mode into the fast toggle store
      setDiscreetMode(useSettingsStore.getState().discreetMode);
    });
  }, []);

  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <SafeAreaProvider>
        <Stack screenOptions={{ headerShown: false }}>
          <Stack.Screen name="(tabs)" />
          <Stack.Screen
            name="alert/[id]"
            options={{ presentation: 'modal', headerShown: true, title: 'Alert Detail' }}
          />
        </Stack>
        <DiscreetOverlay />
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}

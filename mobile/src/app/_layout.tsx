import { Barlow_400Regular, Barlow_500Medium, Barlow_600SemiBold } from '@expo-google-fonts/barlow';
import { BarlowCondensed_600SemiBold, BarlowCondensed_700Bold } from '@expo-google-fonts/barlow-condensed';
import { useFonts } from 'expo-font';
import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { AuthProvider } from '@/lib/auth';
import { PlanningProvider } from '@/lib/planning/PlanningContext';
import { RepertoireProvider } from '@/lib/repertoire/RepertoireContext';
import { SettingsProvider } from '@/lib/settings';
import { colors } from '@/theme/tokens';

export default function RootLayout() {
  const [fontsLoaded] = useFonts({
    Barlow_400Regular,
    Barlow_500Medium,
    Barlow_600SemiBold,
    BarlowCondensed_600SemiBold,
    BarlowCondensed_700Bold,
  });
  if (!fontsLoaded) return null;

  return (
    <SafeAreaProvider>
      <SettingsProvider>
        <AuthProvider>
          <RepertoireProvider>
            <PlanningProvider>
              <StatusBar style="dark" />
              <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: colors.bg } }}>
                <Stack.Screen name="(tabs)" />
                <Stack.Screen name="parametres" options={{ presentation: 'modal' }} />
                <Stack.Screen name="ligne" options={{ presentation: 'modal' }} />
                <Stack.Screen name="tache" options={{ presentation: 'modal' }} />
                <Stack.Screen name="absence" options={{ presentation: 'modal' }} />
                <Stack.Screen name="saisie" options={{ presentation: 'modal' }} />
                <Stack.Screen name="dossier" options={{ presentation: 'modal' }} />
              </Stack>
            </PlanningProvider>
          </RepertoireProvider>
        </AuthProvider>
      </SettingsProvider>
    </SafeAreaProvider>
  );
}

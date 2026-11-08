import { router } from 'expo-router';
import { SlidersHorizontal } from 'lucide-react-native';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { useAuth } from '@/lib/auth';
import { useSettings } from '@/lib/settings';
import { colors, fonts, iconStroke, touch } from '@/theme/tokens';

/**
 * The desktop header, folded for a phone: brand on the left, then the Microsoft
 * connection state and the settings.
 */
export function AppHeader() {
  const { status, account } = useAuth();
  const { settings } = useSettings();
  const connected = status === 'signedIn';
  const label = settings.demo ? 'Mode démo' : connected ? 'Microsoft' : status === 'loading' ? 'Microsoft…' : 'Se connecter';
  const dot = settings.demo ? colors.accent : connected ? colors.ok : colors.err;
  return (
    <View style={styles.header}>
      <View style={styles.brand}>
        <Text style={styles.brandName}>ProjectFlow</Text>
        <Text style={styles.company}>Balz Métal SA</Text>
      </View>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={connected ? `Connecté à Microsoft${account ? ` : ${account.username}` : ''}` : 'Se connecter à Microsoft'}
        onPress={() => router.push('/parametres')}
        style={styles.status}
      >
        <View style={[styles.dot, { backgroundColor: dot }]} />
        <Text style={styles.statusText}>{label}</Text>
      </Pressable>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Paramètres"
        onPress={() => router.push('/parametres')}
        style={({ pressed }) => [styles.icon, pressed && styles.pressed]}
      >
        <SlidersHorizontal size={20} color={colors.accent700} strokeWidth={iconStroke} />
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingLeft: 20,
    paddingRight: 12,
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: colors.line,
  },
  brand: { flex: 1 },
  brandName: { fontFamily: fonts.headingBold, fontSize: 20, color: colors.text },
  company: { fontFamily: fonts.body, fontSize: 12, color: colors.muted },
  status: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    minHeight: touch,
    paddingHorizontal: 8,
  },
  dot: { width: 8, height: 8, borderRadius: 4 },
  statusText: {
    fontFamily: fonts.heading,
    fontSize: 12,
    letterSpacing: 1,
    textTransform: 'uppercase',
    color: colors.subtle,
  },
  icon: {
    width: touch,
    height: touch,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: colors.line,
  },
  pressed: { backgroundColor: colors.accent100 },
});

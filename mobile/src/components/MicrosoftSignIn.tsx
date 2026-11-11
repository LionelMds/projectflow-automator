import { useRef } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { WebView } from 'react-native-webview';

import { Kicker } from '@/components/Text';
import { isRedirect } from '@/lib/authRedirect';
import { colors, fonts, touch } from '@/theme/tokens';

type Props = {
  url: string;
  onRedirect: (url: string) => void;
  onCancel: () => void;
};

/**
 * Microsoft's sign-in page inside the app. Its reply goes to http://localhost, like the
 * desktop's: it is caught here before loading and handed to `onRedirect`.
 */
export function MicrosoftSignIn({ url, onRedirect, onCancel }: Props) {
  const handled = useRef(false);

  const caught = (target: string): boolean => {
    if (!isRedirect(target)) return false;
    if (!handled.current) {
      handled.current = true;
      onRedirect(target);
    }
    return true;
  };

  return (
    <View style={styles.page}>
      <View style={styles.bar}>
        <Pressable accessibilityRole="button" onPress={onCancel} style={styles.barButton}>
          <Text style={styles.barText}>Annuler</Text>
        </Pressable>
        <Kicker style={styles.barTitle}>Connexion Microsoft</Kicker>
        <View style={styles.barButton} />
      </View>
      <WebView
        source={{ uri: url }}
        onShouldStartLoadWithRequest={(request) => !caught(request.url)}
        // In case a platform loads the redirect without asking first.
        onNavigationStateChange={(state) => {
          caught(state.url);
        }}
        startInLoadingState
        style={styles.web}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  page: { flex: 1, backgroundColor: colors.bg },
  bar: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 8,
    borderBottomWidth: 1,
    borderBottomColor: colors.line,
  },
  barButton: { minWidth: 80, minHeight: touch, justifyContent: 'center', paddingHorizontal: 8 },
  barText: {
    fontFamily: fonts.heading,
    fontSize: 15,
    letterSpacing: 1.1,
    textTransform: 'uppercase',
    color: colors.accent700,
  },
  barTitle: { color: colors.text },
  web: { flex: 1 },
});

import { Tabs } from 'expo-router';
import { CalendarRange, FilePlus, FolderOpen, Table } from 'lucide-react-native';

import { useRestriction } from '@/lib/restriction/RestrictionContext';
import { colors, fonts, iconStroke } from '@/theme/tokens';

/**
 * The desktop modules kept on the phone, in the desktop's order (Sortie dossier stays on the poste),
 * and the dossiers' Explorer. « Accès limité » keeps only Explorer and Planning.
 */
export default function ModulesLayout() {
  const { restricted } = useRestriction();
  const hidden = restricted ? { href: null } : {};
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: colors.accent700,
        tabBarInactiveTintColor: colors.muted,
        // The default 49 px leaves the condensed uppercase labels no room under the icon
        // where there is no bottom inset (Android with buttons, the web).
        tabBarStyle: { backgroundColor: colors.bg, borderTopColor: colors.line, minHeight: 60, paddingTop: 4 },
        tabBarLabelStyle: {
          fontFamily: fonts.heading,
          fontSize: 12,
          lineHeight: 16,
          letterSpacing: 0.9,
          textTransform: 'uppercase',
        },
      }}
    >
      <Tabs.Screen
        name="index"
        options={{
          ...hidden,
          title: 'Création',
          tabBarIcon: ({ color, size }) => <FilePlus color={color} size={size} strokeWidth={iconStroke} />,
        }}
      />
      <Tabs.Screen
        name="repertoire"
        options={{
          ...hidden,
          title: 'Répertoire',
          tabBarIcon: ({ color, size }) => <Table color={color} size={size} strokeWidth={iconStroke} />,
        }}
      />
      <Tabs.Screen
        name="explorer"
        options={{
          title: 'Explorer',
          tabBarIcon: ({ color, size }) => <FolderOpen color={color} size={size} strokeWidth={iconStroke} />,
        }}
      />
      <Tabs.Screen
        name="planning"
        options={{
          title: 'Planning',
          tabBarIcon: ({ color, size }) => <CalendarRange color={color} size={size} strokeWidth={iconStroke} />,
        }}
      />
    </Tabs>
  );
}

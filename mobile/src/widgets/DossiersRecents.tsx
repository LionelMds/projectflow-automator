/**
 * « Dossiers récents » widget: the dossiers last opened on this phone. Small: three, the
 * widget opening the Explorer; medium: four and large: eight, each opening its folder in the
 * app; Lock Screen: the last two numbers.
 *
 * As the « Planning » widget, it runs outside the app (`'widget'`): everything it uses is
 * declared inside the function.
 */

import { HStack, Link, Spacer, Text, VStack } from '@expo/ui/swift-ui';
import {
  containerBackground,
  font,
  foregroundStyle,
  frame,
  kerning,
  lineLimit,
  textCase,
  widgetURL,
} from '@expo/ui/swift-ui/modifiers';
import { createWidget, type WidgetEnvironment } from 'expo-widgets';
import type { ReactElement } from 'react';

import type { RecentWidgetProps, WidgetDossier } from '@/widgets/types';

const DossiersRecents = (props: RecentWidgetProps, environment: WidgetEnvironment) => {
  'widget';
  const dark = environment.colorScheme === 'dark';
  const ink = dark ? '#f2f2f3' : '#1d1f20';
  const muted = dark ? '#a3a8ad' : '#575c62';
  const accent = dark ? '#9fc2e3' : '#416180';
  const ground = dark ? '#1d1f20' : '#ffffff';
  const family = environment.widgetFamily ?? 'systemMedium';

  const kicker = (
    <Text
      key="kicker"
      modifiers={[font({ size: 11, weight: 'semibold' }), kerning(0.6), textCase('uppercase'), foregroundStyle(accent), lineLimit(1)]}
    >
      Dossiers récents
    </Text>
  );

  /** Two lines: the number and the client, then the designation. */
  const tallRow = (item: WidgetDossier, key: string) => (
    <VStack key={key} alignment="leading" spacing={1} modifiers={[frame({ maxWidth: Infinity, alignment: 'leading' })]}>
      <HStack spacing={6}>
        <Text modifiers={[font({ size: 12, weight: 'bold' }), foregroundStyle(accent), lineLimit(1)]}>{item.number}</Text>
        <Text modifiers={[font({ size: 11 }), foregroundStyle(muted), lineLimit(1)]}>{item.client}</Text>
      </HStack>
      <Text modifiers={[font({ size: 13, weight: 'medium' }), foregroundStyle(ink), lineLimit(1)]}>{item.designation || '—'}</Text>
    </VStack>
  );

  /** One line: the number, then the designation. */
  const flatRow = (item: WidgetDossier, key: string) => (
    <HStack key={key} spacing={8} modifiers={[frame({ maxWidth: Infinity, alignment: 'leading' })]}>
      <Text modifiers={[font({ size: 12, weight: 'bold' }), foregroundStyle(accent), lineLimit(1)]}>{item.number}</Text>
      <Text modifiers={[font({ size: 12, weight: 'medium' }), foregroundStyle(ink), lineLimit(1)]}>{item.designation || item.client}</Text>
      <Spacer minLength={0} />
    </HStack>
  );

  /** At most `max` dossiers; each opens its folder, except in the small widget. */
  const rows = (max: number, row: (item: WidgetDossier, key: string) => ReactElement) =>
    props.items.slice(0, max).map((item) =>
      family === 'systemSmall' ? (
        row(item, item.number)
      ) : (
        <Link key={item.number} destination={item.url}>
          {row(item, 'row')}
        </Link>
      ),
    );

  if (family === 'accessoryRectangular') {
    const lines = props.items.slice(0, 2).map((item) => (
      <Text key={item.number} modifiers={[font({ size: 12 }), lineLimit(1)]}>
        {`${item.number} ${item.designation || item.client}`}
      </Text>
    ));
    if (!lines.length) {
      lines.push(
        <Text key="empty" modifiers={[font({ size: 12 }), lineLimit(2)]}>
          {props.message || 'Aucun dossier ouvert.'}
        </Text>,
      );
    }
    return (
      <VStack
        alignment="leading"
        spacing={1}
        modifiers={[frame({ maxWidth: Infinity, alignment: 'leading' }), containerBackground('clear', 'widget'), widgetURL(props.url)]}
      >
        <Text key="title" modifiers={[font({ size: 13, weight: 'semibold' })]}>
          Dossiers récents
        </Text>
        {lines}
      </VStack>
    );
  }

  const root = [
    frame({ maxWidth: Infinity, maxHeight: Infinity, alignment: 'topLeading' }),
    containerBackground(ground, 'widget'),
    widgetURL(props.url),
  ];

  if (props.message || !props.items.length) {
    return (
      <VStack alignment="leading" spacing={6} modifiers={root}>
        {kicker}
        <Text modifiers={[font({ size: 14, weight: 'medium' }), foregroundStyle(ink)]}>
          {props.message || 'Les dossiers ouverts dans ProjectFlow apparaîtront ici.'}
        </Text>
        <Spacer />
      </VStack>
    );
  }

  if (family === 'systemSmall') {
    return (
      <VStack alignment="leading" spacing={6} modifiers={root}>
        {kicker}
        {rows(3, tallRow)}
        <Spacer key="end" minLength={0} />
      </VStack>
    );
  }

  if (family === 'systemMedium') {
    return (
      <VStack alignment="leading" spacing={7} modifiers={root}>
        {kicker}
        {rows(4, flatRow)}
        <Spacer key="end" minLength={0} />
      </VStack>
    );
  }

  return (
    <VStack alignment="leading" spacing={8} modifiers={root}>
      {kicker}
      {rows(8, tallRow)}
      <Spacer key="end" minLength={0} />
    </VStack>
  );
};

export default createWidget('DossiersRecents', DossiersRecents, {
  message: 'Les dossiers ouverts dans ProjectFlow apparaîtront ici.',
  items: [],
  url: 'projectflow://explorer',
});

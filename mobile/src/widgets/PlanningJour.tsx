/**
 * « Planning » widget: the day of the people the widget is set to show (touch and hold it,
 * « Modifier le widget », « Afficher »: me, the people checked in Paramètres, a team, a person,
 * everybody), as their rows of the planning show it.
 *
 * One person: small, the day and two tasks; medium, four tasks; large, the day and the next
 * working day; touching a task opens it. Several people: one line each, their initials and
 * their tasks of the day, as the planning's day view. Touching elsewhere opens the planning on
 * that day. Lock Screen: the day in two lines.
 *
 * The widget runs outside the app (`'widget'`): no value from this module reaches it, so every
 * colour and helper is declared inside the function, and texts come worded by the app.
 */

import { HStack, Link, Rectangle, Spacer, Text, VStack } from '@expo/ui/swift-ui';
import {
  containerBackground,
  font,
  foregroundStyle,
  frame,
  kerning,
  lineLimit,
  opacity,
  padding,
  strikethrough,
  textCase,
  widgetURL,
} from '@expo/ui/swift-ui/modifiers';
import { createWidget, type WidgetEnvironment } from 'expo-widgets';
import type { ReactElement } from 'react';

import type { PlanningWidgetProps, WidgetDay, WidgetPerson, WidgetTask } from '@/widgets/types';

/** « Afficher », set on the widget (`ios.configuration` in app.json). */
type Configuration = { afficher?: string };

const PlanningJour = (props: PlanningWidgetProps, environment: WidgetEnvironment<Configuration>) => {
  'widget';
  const dark = environment.colorScheme === 'dark';
  const ink = dark ? '#f2f2f3' : '#1d1f20';
  const muted = dark ? '#a3a8ad' : '#575c62';
  const accent = dark ? '#9fc2e3' : '#416180';
  const away = dark ? '#ff8a5c' : '#a3330a';
  const ground = dark ? '#1d1f20' : '#ffffff';
  const family = environment.widgetFamily ?? 'systemMedium';
  const day: WidgetDay | undefined = props.day;
  const link = day ? day.url : props.url;

  // Who this widget shows: « Moi » until another choice is made on the widget.
  const chosen = (environment.configuration && environment.configuration.afficher) || 'moi';
  const choice = props.choices ? props.choices[chosen] : undefined;
  const peopleOf = (shown: WidgetDay | undefined): WidgetPerson[] => {
    const found: WidgetPerson[] = [];
    if (!shown || !choice) return found;
    for (const id of choice.ids) {
      const person = shown.people.find((candidate) => candidate.id === id);
      if (person) found.push(person);
    }
    return found;
  };
  const people = peopleOf(day);
  const message =
    props.message ||
    (!day
      ? 'Ouvrez ProjectFlow.'
      : !choice
        ? 'Ce choix n’existe plus : touchez longuement le widget, puis « Modifier le widget ».'
        : !people.length
          ? choice.empty || 'Personne à afficher.'
          : '');
  const title = choice ? choice.title : '';

  const plural = (count: number) => (count > 1 ? `${count} tâches` : count === 1 ? '1 tâche' : 'Rien de prévu');

  /** « 4998 Montage marquise · 5012 Soudure », or the absence when there is no task. */
  const summary = (person: WidgetPerson) => {
    const tasks = person.tasks.map((task) => [task.number, task.title].filter((part) => part !== '').join(' ')).join(' · ');
    if (!tasks) return person.note || '—';
    return person.note ? `${person.note} · ${tasks}` : tasks;
  };

  const caps = (text: string, color: string, key: string) => (
    <Text key={key} modifiers={[font({ size: 11, weight: 'semibold' }), kerning(0.6), textCase('uppercase'), foregroundStyle(color), lineLimit(1)]}>
      {text}
    </Text>
  );

  const bar = (task: WidgetTask, height: number, key: string) => (
    <Rectangle key={key} modifiers={[foregroundStyle(task.color), frame({ width: 3, height }), opacity(task.provisional ? 0.45 : 1)]} />
  );

  const titleStyle = (task: WidgetTask, size: number) => [
    font({ size, weight: 'medium' }),
    foregroundStyle(task.done ? muted : ink),
    strikethrough({ isActive: task.done, pattern: 'solid' }),
    lineLimit(1),
  ];

  const head = (task: WidgetTask) => [task.number, task.when].filter((part) => part !== '').join(' · ') + (task.urgent ? '  !' : '');

  /** Two lines: « 4998 · Matin », then the title. */
  const tallRow = (task: WidgetTask, key: string) => (
    <HStack key={key} spacing={6} modifiers={[frame({ maxWidth: Infinity, alignment: 'leading' })]}>
      {bar(task, 28, 'bar')}
      <VStack key="text" alignment="leading" spacing={1}>
        <Text modifiers={[font({ size: 11, weight: 'semibold' }), foregroundStyle(task.urgent ? away : accent), lineLimit(1)]}>
          {head(task)}
        </Text>
        <Text modifiers={titleStyle(task, 13)}>{task.title}</Text>
      </VStack>
      <Spacer key="end" minLength={0} />
    </HStack>
  );

  /** One line: number, title, then when. */
  const flatRow = (task: WidgetTask, key: string) => (
    <HStack key={key} spacing={6} modifiers={[frame({ maxWidth: Infinity, alignment: 'leading' })]}>
      {bar(task, 16, 'bar')}
      <Text key="number" modifiers={[font({ size: 12, weight: 'bold' }), foregroundStyle(task.urgent ? away : accent), lineLimit(1)]}>
        {task.number || '—'}
      </Text>
      <Text key="title" modifiers={titleStyle(task, 12)}>
        {task.title}
      </Text>
      <Spacer key="gap" minLength={4} />
      <Text key="when" modifiers={[font({ size: 11 }), foregroundStyle(muted), lineLimit(1)]}>
        {task.when}
      </Text>
    </HStack>
  );

  /** One person's tasks, at most `max`, then « +2 autres »; each task opens itself, except in the small widget. */
  const rows = (person: WidgetPerson, max: number, row: (task: WidgetTask, key: string) => ReactElement, key: string) => {
    const items: ReactElement[] = person.tasks.slice(0, max).map((task) =>
      family === 'systemSmall' ? (
        row(task, `${key}-${task.id}`)
      ) : (
        <Link key={`${key}-${task.id}`} destination={task.url}>
          {row(task, 'row')}
        </Link>
      ),
    );
    const more = person.tasks.length - max;
    if (more > 0) {
      items.push(
        <Text key={`${key}-more`} modifiers={[font({ size: 11 }), foregroundStyle(muted)]}>
          {more > 1 ? `+${more} autres` : '+1 autre'}
        </Text>,
      );
    }
    if (!person.tasks.length) {
      items.push(
        <Text key={`${key}-empty`} modifiers={[font({ size: 13, weight: 'medium' }), foregroundStyle(person.note ? away : muted), lineLimit(2)]}>
          {person.note || 'Rien de prévu'}
        </Text>,
      );
    } else if (person.note) {
      items.unshift(
        <Text key={`${key}-note`} modifiers={[font({ size: 11, weight: 'semibold' }), foregroundStyle(away), lineLimit(1)]}>
          {person.note}
        </Text>,
      );
    }
    return items;
  };

  /** Several people: one line each, « LM  ▌4998 Montage marquise · 5012 Soudure ». */
  const personRow = (person: WidgetPerson) => {
    const content: ReactElement[] = [];
    if (person.tasks.length) {
      content.push(bar(person.tasks[0], 14, 'bar'));
      content.push(
        <Text key="tasks" modifiers={[font({ size: 12, weight: 'medium' }), foregroundStyle(ink), lineLimit(1)]}>
          {summary(person)}
        </Text>,
      );
    } else {
      content.push(
        <Text key="none" modifiers={[font({ size: 12, weight: person.note ? 'semibold' : 'regular' }), foregroundStyle(person.note ? away : muted), lineLimit(1)]}>
          {summary(person)}
        </Text>,
      );
    }
    return (
      <HStack key={person.id} spacing={6} modifiers={[frame({ maxWidth: Infinity, alignment: 'leading' })]}>
        <Text key="who" modifiers={[font({ size: 12, weight: 'bold' }), foregroundStyle(accent), lineLimit(1), frame({ width: 32, alignment: 'leading' })]}>
          {person.who}
        </Text>
        {content}
        <Spacer key="end" minLength={0} />
      </HStack>
    );
  };

  /** At most `max` lines; when more people are shown, the last line says how many are left. */
  const board = (max: number) => {
    const shown = people.length > max ? people.slice(0, max - 1) : people;
    const items = shown.map(personRow);
    const left = people.length - shown.length;
    if (left > 0) {
      items.push(
        <Text key="more" modifiers={[font({ size: 11 }), foregroundStyle(muted)]}>
          {left > 1 ? `+${left} autres` : '+1 autre'}
        </Text>,
      );
    }
    return items;
  };

  // A task shared by two people counts once.
  const tasksCount = new Set(people.flatMap((person) => person.tasks.map((task) => task.id))).size;

  // Lock Screen: the system colours it, only the texts count.
  if (family === 'accessoryInline') {
    let text = 'ProjectFlow';
    if (!message && people.length === 1) {
      const person = people[0];
      const first = person.tasks.length ? ` · ${[person.tasks[0].number, person.tasks[0].title].filter((part) => part !== '').join(' ')}` : '';
      text = person.tasks.length ? `${plural(person.tasks.length)}${first}` : person.note || 'Rien de prévu';
    } else if (!message) {
      text = `${title} · ${plural(tasksCount)}`;
    }
    return <Text modifiers={[containerBackground('clear', 'widget'), widgetURL(link)]}>{text}</Text>;
  }
  if (family === 'accessoryRectangular') {
    const lock = [frame({ maxWidth: Infinity, alignment: 'leading' }), containerBackground('clear', 'widget'), widgetURL(link)];
    if (message || !day) {
      return (
        <VStack alignment="leading" spacing={1} modifiers={lock}>
          <Text key="title" modifiers={[font({ size: 13, weight: 'semibold' })]}>
            ProjectFlow
          </Text>
          <Text key="message" modifiers={[font({ size: 12 }), lineLimit(2)]}>
            {message}
          </Text>
        </VStack>
      );
    }
    const single = people.length === 1;
    const lines = single
      ? people[0].tasks.slice(0, 2).map((task) => (
          <Text key={task.id} modifiers={[font({ size: 12 }), lineLimit(1)]}>
            {`${task.number ? `${task.number} ` : ''}${task.title}`}
          </Text>
        ))
      : people.slice(0, 2).map((person) => (
          <Text key={person.id} modifiers={[font({ size: 12 }), lineLimit(1)]}>
            {`${person.who} ${summary(person)}`}
          </Text>
        ));
    if (!lines.length) {
      lines.push(
        <Text key="empty" modifiers={[font({ size: 12 }), lineLimit(1)]}>
          {people[0].note || 'Rien de prévu'}
        </Text>,
      );
    }
    const heading = single
      ? chosen === 'moi'
        ? `${day.label} · ${plural(people[0].tasks.length)}`
        : `${title} · ${plural(people[0].tasks.length)}`
      : `${title} · ${day.label}`;
    return (
      <VStack alignment="leading" spacing={1} modifiers={lock}>
        <Text key="title" modifiers={[font({ size: 13, weight: 'semibold' }), lineLimit(1)]}>
          {heading}
        </Text>
        {lines}
      </VStack>
    );
  }

  const root = [
    frame({ maxWidth: Infinity, maxHeight: Infinity, alignment: 'topLeading' }),
    containerBackground(ground, 'widget'),
    widgetURL(link),
  ];

  if (message || !day) {
    return (
      <VStack alignment="leading" spacing={6} modifiers={root}>
        {caps(title || 'Planning', accent, 'kicker')}
        <Text key="message" modifiers={[font({ size: 14, weight: 'medium' }), foregroundStyle(ink)]}>
          {message}
        </Text>
        <Spacer key="end" />
      </VStack>
    );
  }

  const header = (
    <HStack key="header" spacing={6} modifiers={[frame({ maxWidth: Infinity, alignment: 'leading' })]}>
      <VStack key="day" alignment="leading" spacing={0}>
        {caps(day.kicker, accent, 'kicker')}
        <Text key="label" modifiers={[font({ size: 17, weight: 'bold' }), foregroundStyle(ink), lineLimit(1)]}>
          {day.label}
        </Text>
      </VStack>
      <Spacer key="gap" minLength={4} />
      <VStack key="who" alignment="trailing" spacing={0}>
        {caps(title, muted, 'who')}
        <Text key="updated" modifiers={[font({ size: 10 }), foregroundStyle(muted), lineLimit(1)]}>
          {props.updated}
        </Text>
      </VStack>
    </HStack>
  );

  // Several people: the planning's day view, one line each.
  if (people.length > 1) {
    const max = family === 'systemSmall' || family === 'systemMedium' ? 4 : 12;
    return (
      <VStack alignment="leading" spacing={family === 'systemSmall' || family === 'systemMedium' ? 5 : 6} modifiers={root}>
        {header}
        {board(max)}
        <Spacer key="end" minLength={0} />
      </VStack>
    );
  }

  const person = people[0];

  if (family === 'systemSmall') {
    return (
      <VStack alignment="leading" spacing={7} modifiers={root}>
        {header}
        {rows(person, 2, tallRow, 'day')}
        <Spacer key="end" minLength={0} />
      </VStack>
    );
  }

  if (family === 'systemMedium') {
    return (
      <VStack alignment="leading" spacing={6} modifiers={root}>
        {header}
        {rows(person, 4, flatRow, 'day')}
        <Spacer key="end" minLength={0} />
      </VStack>
    );
  }

  // Large (and the iPad's extra large): the day, then the next working day.
  const next: WidgetDay | undefined = props.next;
  const later = next ? next.people.find((candidate) => candidate.id === person.id) : undefined;
  const after: ReactElement[] =
    next && later
      ? [
          <Rectangle key="rule" modifiers={[foregroundStyle(dark ? '#3a3e42' : '#e1e2e4'), frame({ height: 1 }), padding({ vertical: 2 })]} />,
          <HStack key="next-head" spacing={6}>
            {caps(next.kicker, accent, 'next-kicker')}
            <Text key="next-label" modifiers={[font({ size: 11 }), foregroundStyle(muted), lineLimit(1)]}>
              {next.label}
            </Text>
          </HStack>,
          ...rows(later, 3, flatRow, 'next'),
        ]
      : [];
  return (
    <VStack alignment="leading" spacing={7} modifiers={root}>
      {header}
      {rows(person, 5, tallRow, 'day')}
      {after}
      <Spacer key="end" minLength={0} />
    </VStack>
  );
};

// No `null` here either (see PlanningWidgetProps): iOS would close the app.
export default createWidget('PlanningJour', PlanningJour, {
  message: 'Ouvrez ProjectFlow pour charger votre planning.',
  updated: '',
  url: 'projectflow://planning',
  choices: {},
});

/**
 * What the app gives the home-screen widgets. The widgets run on their own, outside the
 * app: they only show these values, already worded, and open the app at the links given.
 */

/** A task of the day, as one line of the « Planning » widget. */
export type WidgetTask = {
  id: string;
  /** « 4998 », the dossier number without its year, as on the planning bars ('' for an internal task). */
  number: string;
  title: string;
  /** « Matin », « Après-midi », « Journée », « Nuit » or « 08:00–10:00 », then « · 2/3 » over several days. */
  when: string;
  /** The task type's colour, as on the planning. */
  color: string;
  provisional: boolean;
  done: boolean;
  urgent: boolean;
  /** Opens the task in the app. */
  url: string;
};

/** One person's row of the planning on one day. */
export type WidgetPerson = {
  /** The planning's id of the person. */
  id: string;
  /** « LM », as the planning labels the row (the name when there are no initials). */
  who: string;
  /** An absence, a holiday or a closure of that day (« Vacances », « Ascension »), else ''. */
  note: string;
  tasks: WidgetTask[];
};

/** One day of the planning: every person, in the planning's order (team by team). */
export type WidgetDay = {
  /** « Aujourd'hui », « Demain » or the weekday (« Lundi » shown on a Saturday). */
  kicker: string;
  /** « Lun 9 nov. » */
  label: string;
  people: WidgetPerson[];
  /** Opens the planning on that day. */
  url: string;
};

/**
 * Who one widget shows, picked on the iPhone: touch and hold the widget, « Modifier le
 * widget », « Afficher ». Keys: `moi`, `selection` (people checked in Paramètres), `tous`,
 * `equipe:<team>`, `personne:<id>`.
 */
export type WidgetChoice = {
  /** « LM », « Atelier », « Ma sélection », « Tout le monde ». */
  title: string;
  /** The people shown, in the planning's order. */
  ids: string[];
  /** Shown when nobody is to be shown (initials missing, nobody checked…). */
  empty: string;
};

/** The widget's « Afficher » options, as the iPhone lists them. */
export type WidgetChoiceOption = { name: string; value: string; subtitle?: string };

/**
 * iOS keeps these values in the settings it shares with the widget, which refuse `null`: an
 * app sending one is closed at once. A value with nothing to show is left out, never `null`.
 */
export type PlanningWidgetProps = {
  /** When set, the widget only shows this (sign-in, planning not read…). */
  message: string;
  day?: WidgetDay;
  /** The next working day, shown by the large widget for one person. */
  next?: WidgetDay;
  /** Every « Afficher » choice, by key. */
  choices: Record<string, WidgetChoice>;
  /** When the planning was read, « 14:32 » or « 8 nov. 14:32 ». */
  updated: string;
  /** Opens the planning. */
  url: string;
};

/** A dossier of the « Dossiers récents » widget. */
export type WidgetDossier = {
  /** « 2026-5245 » */
  number: string;
  designation: string;
  client: string;
  /** Opens the dossier's folder in the app. */
  url: string;
};

export type RecentWidgetProps = {
  /** When set, the widget only shows this. */
  message: string;
  items: WidgetDossier[];
  /** Opens the Explorer tab. */
  url: string;
};

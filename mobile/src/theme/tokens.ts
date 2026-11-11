/**
 * Industry theme: the same tokens as the desktop app (`src/projectflow/ui/theme.py`).
 * A light technical ground, one steel accent with its 100-900 ramp, Barlow Condensed
 * headings over Barlow, square corners and hairline borders.
 */

export const colors = {
  bg: '#f2f2f3',
  surface: '#ffffff',
  text: '#1d1f20',
  muted: '#575c62',
  subtle: '#2f3336',
  line: 'rgba(29,31,32,0.2)',
  lineSoft: 'rgba(29,31,32,0.12)',
  accent: '#5980a6',
  accent100: '#eef6ff',
  accent200: '#d6ebff',
  accent700: '#416180',
  accent800: '#2c455d',
  accent900: '#1d2d3d',
  ok: '#2e7d4f',
  err: '#b42318',
  errSoft: '#fdecea',
  absence: '#e0480f',
  absenceInk: '#a3330a',
  absenceSoft: '#fbe7de',
  dirty: '#e4f2e4',
  dirtyLine: '#7fb38a',
  closed: '#e6e7e9',
} as const;

export const fonts = {
  body: 'Barlow_400Regular',
  bodyMedium: 'Barlow_500Medium',
  bodySemiBold: 'Barlow_600SemiBold',
  heading: 'BarlowCondensed_600SemiBold',
  headingBold: 'BarlowCondensed_700Bold',
} as const;

export const space = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 20,
  xxl: 28,
} as const;

/** Minimum touch target, as on both platforms' guidelines. */
export const touch = 44;

/** Thin-stroke Lucide icons, as the design system asks. */
export const iconStroke = 1.5;

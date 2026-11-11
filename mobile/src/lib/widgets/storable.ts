/**
 * A value iOS can keep for the widgets: every `null` left out. iOS stores what the widgets show
 * in settings shared with them, which refuse `null` and close the app at once.
 */
export function storable<T>(value: T): T {
  if (Array.isArray(value)) {
    return value.filter((item) => item !== null && item !== undefined).map((item) => storable(item)) as T;
  }
  if (value !== null && typeof value === 'object' && !(value instanceof Date)) {
    return Object.fromEntries(
      Object.entries(value)
        .filter(([, item]) => item !== null && item !== undefined)
        .map(([key, item]) => [key, storable(item)]),
    ) as T;
  }
  return value;
}

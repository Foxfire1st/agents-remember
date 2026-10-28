export const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

export const text = (value: unknown): string | null => (typeof value === 'string' ? value : null);

export function firstText(values: readonly unknown[]): string | null {
  for (const val of values) {
    if (typeof val === 'string') return val;
  }
  return null;
}

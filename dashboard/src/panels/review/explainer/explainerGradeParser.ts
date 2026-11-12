import type {
  ExplainerCategory,
  ExplainerGrade,
  ExplainerScalePoint,
  ExplainerState,
} from './explainerApi';
import { firstText, isRecord, text } from './explainerValues';

function readScale(value: unknown): ExplainerScalePoint[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((point) => {
    if (typeof point === 'string' && point.trim()) return [{ grade: point.trim() }];
    if (!isRecord(point)) return [];
    const grade = firstText([point.grade, point.name]);
    if (!grade) return [];
    const meaning = text(point.meaning) ?? undefined;
    return [{ grade, ...(meaning ? { meaning } : {}) }];
  });
}

function parseCategoryRow(cat: unknown): ExplainerCategory[] {
  if (!isRecord(cat)) return [];
  const name = text(cat.name);
  const scale = readScale(cat.scale);
  if (!name || scale.length < 2) return [];
  const meaning = text(cat.meaning) ?? undefined;
  return [{ name, ...(meaning ? { meaning } : {}), scale }];
}

export function readCategories(answer: Record<string, unknown>): ExplainerCategory[] {
  const rawGrades = isRecord(answer.grades) ? answer.grades : {};
  const rawStale = isRecord(answer.stale) ? answer.stale : {};
  const rawExplainer = isRecord(answer.explainer) ? answer.explainer : {};
  const source =
    answer.categories ?? rawGrades.categories ?? rawStale.categories ?? rawExplainer.categories;
  if (!Array.isArray(source)) return [];
  return source.flatMap(parseCategoryRow);
}

function parseCarriedGradeRow(
  c: unknown,
  defaultCategory: string,
): NonNullable<ExplainerGrade['carried']>[number][] {
  if (!isRecord(c)) return [];
  const cNested = isRecord(c.grade) ? c.grade : {};
  const cCat = firstText([c.category, cNested.category]) ?? defaultCategory;
  const cGrade = firstText([c.grade, cNested.grade]);
  if (!cGrade) return [];
  const cReason = firstText([cNested.reason, c.reason]) ?? '';
  const reference = text(c.reference);
  return [
    {
      ...(reference ? { reference } : {}),
      category: cCat,
      grade: cGrade,
      reason: cReason,
      stale: c.stale !== false,
      staleReason: firstText([c.staleReason, c.stale_reason]),
    },
  ];
}

function readGradeStaleFlag(
  item: Record<string, unknown>,
  nested: Record<string, unknown>,
  reference: string,
  staleRefs: ReadonlySet<string>,
): boolean {
  return item.stale === true || nested.stale === true || staleRefs.has(reference);
}

function readGradeCarriedList(
  item: Record<string, unknown>,
  category: string,
): ExplainerGrade['carried'] {
  if (!Array.isArray(item.carried)) return undefined;
  const carried = item.carried.flatMap((c) => parseCarriedGradeRow(c, category));
  return carried.length ? carried : undefined;
}

function parseGradeRow(item: unknown, staleRefs: ReadonlySet<string>): ExplainerGrade[] {
  if (!isRecord(item)) return [];
  const nested = isRecord(item.grade) ? item.grade : {};
  const reference = firstText([item.reference, nested.reference]);
  const category = firstText([item.category, nested.category]);
  const grade = firstText([item.grade, nested.grade]);
  if (!reference || !category || !grade) return [];
  const staleReason = firstText([
    item.staleReason,
    item.stale_reason,
    nested.staleReason,
    nested.stale_reason,
    isRecord(item.grade) ? item.reason : null,
  ]);
  const carried = readGradeCarriedList(item, category);
  const row: ExplainerGrade = {
    reference,
    category,
    grade,
    reason: firstText([nested.reason, item.reason]) ?? '',
    stale: readGradeStaleFlag(item, nested, reference, staleRefs),
  };
  if (staleReason) row.staleReason = staleReason;
  if (carried) row.carried = carried;
  return [row];
}

function readGradeList(source: unknown, staleRefs: ReadonlySet<string>): ExplainerGrade[] {
  if (!Array.isArray(source)) return [];
  return source.flatMap((item) => parseGradeRow(item, staleRefs));
}

function mergeGradeWithStale(
  grade: ExplainerGrade,
  staleMap: ReadonlyMap<string, ExplainerGrade>,
): ExplainerGrade {
  const match = staleMap.get(`${grade.reference}\u0000${grade.category}\u0000${grade.grade}`);
  if (!match) return grade;
  const staleReason = match.staleReason ?? match.reason;
  return {
    ...grade,
    stale: grade.stale || match.stale,
    reason: grade.reason || match.reason,
    ...(staleReason ? { staleReason } : {}),
  };
}

export function readGrades(
  answer: Record<string, unknown>,
  stale: ExplainerState['stale'],
): ExplainerGrade[] {
  const staleRefs = new Set<string>(stale?.staleGrades ?? []);
  const rawGrades = answer.grades;
  const rawStale = isRecord(answer.stale) ? answer.stale : {};
  const primarySource = Array.isArray(rawGrades)
    ? rawGrades
    : isRecord(rawGrades)
      ? rawGrades.grades
      : null;
  const primary = readGradeList(primarySource, staleRefs);
  const fromStale = readGradeList(rawStale.grades, staleRefs);
  if (!primary.length) return fromStale;
  if (!fromStale.length) return primary;
  const staleMap = new Map(
    fromStale.map((g) => [`${g.reference}\u0000${g.category}\u0000${g.grade}`, g]),
  );
  return primary.map((g) => mergeGradeWithStale(g, staleMap));
}

/** Числа лендинга — только из публичного прайса, который заполняет админ.
 *  Ничего не выдумываем: нет прайса — нет чисел, секции показывают это
 *  честно. Здесь — выводимые величины: «во сколько раз дешевле», самый
 *  дешёвый класс, минимальная цена за токен. */
import type { GpuClass, PublicPricing } from "@looma/api";

export const kop = (k: number) => Math.round(k / 100);

/** Самый дешёвый конкурент по классу — по нему считается «−N%». */
export function cheapestRival(c: GpuClass): { key: string; kopecks: number } | null {
  const rows = Object.entries(c.competitors ?? {}).filter(([, v]) => v > 0);
  if (rows.length === 0) return null;
  const [key, kopecks] = rows.sort((a, b) => a[1] - b[1])[0];
  return { key, kopecks };
}
export const savePercent = (c: GpuClass) => {
  const r = cheapestRival(c);
  return r && c.rate_kopecks > 0 ? Math.round((1 - c.rate_kopecks / r.kopecks) * 100) : null;
};
/** «В 2,7 раза дешевле Selectel» по конкретному классу. */
export const timesCheaper = (c: GpuClass, key: string) => {
  const rival = c.competitors?.[key];
  return rival && c.rate_kopecks > 0 ? rival / c.rate_kopecks : null;
};

/** Класс для hero: помеченный featured, иначе с самой большой экономией. */
export function heroClass(p?: PublicPricing | null): GpuClass | null {
  const list = (p?.gpu_classes ?? []).filter((c) => c.rate_kopecks > 0);
  if (list.length === 0) return null;
  return list.find((c) => c.featured) ?? [...list].sort((a, b) => (savePercent(b) ?? 0) - (savePercent(a) ?? 0))[0];
}
export function cheapestClass(p?: PublicPricing | null): GpuClass | null {
  const list = (p?.gpu_classes ?? []).filter((c) => c.rate_kopecks > 0);
  return list.length ? [...list].sort((a, b) => a.rate_kopecks - b.rate_kopecks)[0] : null;
}
export function cheapestModel(p?: PublicPricing | null) {
  const list = (p?.models ?? []).filter((m) => m.price_in > 0);
  return list.length ? [...list].sort((a, b) => a.price_in - b.price_in)[0] : null;
}
/** Диапазон экономии по всем классам: «в 2–3 раза дешевле». */
export function savingsRange(p?: PublicPricing | null): [number, number] | null {
  const xs = (p?.gpu_classes ?? []).map((c) => { const r = cheapestRival(c); return r && c.rate_kopecks > 0 ? r.kopecks / c.rate_kopecks : null; })
    .filter((x): x is number => x !== null && x > 1);
  if (xs.length === 0) return null;
  return [Math.floor(Math.min(...xs) * 10) / 10, Math.round(Math.max(...xs) * 10) / 10];
}
export const fmtTimes = (x: number) => x.toLocaleString("ru-RU", { maximumFractionDigits: 1 });

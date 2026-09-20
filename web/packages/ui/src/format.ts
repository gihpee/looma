/** Форматирование чисел и дат — по-русски и одинаково на всех поверхностях. */
const ru = "ru-RU";

/** Копейки → «1 234,50 ₽». Деньги в API всегда в копейках, чтобы не спорить
 *  о плавающей точке. */
export const money = (kopecks: number, currency = "RUB", digits = 2) =>
  `${(kopecks / 100).toLocaleString(ru, { minimumFractionDigits: digits, maximumFractionDigits: digits })} ${currency === "RUB" ? "₽" : currency}`;
export const rubles = (rub: number, digits = 0) =>
  `${rub.toLocaleString(ru, { minimumFractionDigits: digits, maximumFractionDigits: digits })} ₽`;
export const num = (n: number, digits = 0) =>
  n.toLocaleString(ru, { minimumFractionDigits: digits, maximumFractionDigits: digits });
export const bytes = (b: number) => {
  const gb = b / 1024 ** 3;
  return gb >= 1000 ? `${(gb / 1024).toFixed(1)} TB` : `${gb.toFixed(1)} GB`;
};
export const tokensK = (n: number) => n >= 1_000_000 ? `${(n / 1_000_000).toLocaleString(ru, { maximumFractionDigits: 1 })} M`
  : n >= 1000 ? `${(n / 1000).toLocaleString(ru, { maximumFractionDigits: 1 })} K` : String(n);
/** Секунды → «4 ч 12 мин» / «18 мин» / «42 с». */
export const duration = (s: number) => {
  if (s < 60) return `${Math.round(s)} с`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m} мин`;
  const h = Math.floor(m / 60), mm = m % 60;
  return mm ? `${h} ч ${mm} мин` : `${h} ч`;
};
export const dateTime = (iso: string | number) => new Date(iso).toLocaleString(ru, { dateStyle: "short", timeStyle: "short" });
export const dateOnly = (iso: string | number) => new Date(iso).toLocaleDateString(ru);
/** «20 ч назад» — для списков, где важна давность, а не момент. */
export const ago = (iso: string | number) => {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "только что";
  if (s < 3600) return `${Math.round(s / 60)} мин назад`;
  if (s < 86400) return `${Math.round(s / 3600)} ч назад`;
  return `${Math.round(s / 86400)} дн назад`;
};
/** Склонение: plural(3, ["узел", "узла", "узлов"]) → «3 узла». */
export const plural = (n: number, forms: [string, string, string]) => {
  const a = Math.abs(n) % 100, b = a % 10;
  const f = a > 10 && a < 20 ? forms[2] : b > 1 && b < 5 ? forms[1] : b === 1 ? forms[0] : forms[2];
  return `${num(n)} ${f}`;
};

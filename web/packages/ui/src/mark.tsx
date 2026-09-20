/** Знак Looma: «L», собранная из переплетения. Две нити основы идут вниз, две
 *  нити утка́ уходят вправо, и в каждом пересечении одна проходит поверх другой —
 *  отсюда просветы. Нижняя кромка скруглена: там уто́к разворачивается обратно,
 *  как на настоящей кромке полотна.
 *
 *  Цвет — currentColor: знак живёт на трёх поверхностях и в двух темах, и
 *  заливать его намертво значило бы чинить отдельно при каждой смене темы. */
export function Mark({ size = 28, className = "" }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 100 100" fill="currentColor"
         aria-hidden="true" className={className}>
      <rect x="8" y="6" width="20" height="45" />
      <path d="M8 77 H28 V94 H25 A17 17 0 0 1 8 77 Z" />
      <rect x="34" y="6" width="20" height="19" />
      <rect x="34" y="51" width="20" height="43" />
      <rect x="31" y="28" width="61" height="20" />
      <rect x="8" y="54" width="23" height="20" />
      <rect x="57" y="54" width="35" height="20" />
    </svg>
  );
}

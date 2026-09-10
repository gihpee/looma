"""Значок для меню-бара: та же форма, что на сайте, но заданная АЛЬФОЙ.

Запускать при изменении фавикона:  python3 icons/make-tray.py

Почему не растеризатором. Quick Look (`qlmanage -t`) кладёт SVG на НЕПРОЗРАЧНЫЙ
фон, и значок из такого файла превращается в панели в залитый квадрат: macOS
берёт у template-иконки только альфу, а она там сплошная. Проверено на стенде
ровно этим способом. Здесь фигуры считаются напрямую, и прозрачное остаётся
прозрачным.

Фигуры взяты из фавикона (web/index.html) в его же системе координат: 100×100,
группа сдвинута на 10 и сжата до 0.8. Пересчитывать их вручную не нужно —
меняется только этот файл.
"""

from __future__ import annotations

import pathlib
import struct
import zlib

RECTS = [(8, 6, 20, 45), (34, 6, 20, 19), (34, 51, 20, 43),
         (31, 28, 61, 20), (8, 54, 23, 20), (57, 54, 35, 20)]
# Нижний хвост первой нити: полоса со скруглённым левым нижним углом. Дуга
# радиусом 17 с центром (25, 77) — то же, что в пути SVG.
TAIL = (8, 77, 28, 94)
TAIL_CENTER, TAIL_R = (25.0, 77.0), 17.0
# Ограничивающий прямоугольник знака. Поля фавикона в панели лишние: там своё.
BOX = (8, 6, 92, 94)

SIZE = 44          # 22 пункта при двойной плотности — размер строки меню
PAD = 5            # чтобы знак не лип к краям и к соседям
SUPERSAMPLE = 4    # сглаживание: в панели видна каждая ступенька


def covered(x: float, y: float) -> bool:
    for rx, ry, rw, rh in RECTS:
        if rx <= x <= rx + rw and ry <= y <= ry + rh:
            return True
    x0, y0, x1, y1 = TAIL
    if x0 <= x <= x1 and y0 <= y <= y1:
        if x >= TAIL_CENTER[0]:
            return True
        dx, dy = x - TAIL_CENTER[0], y - TAIL_CENTER[1]
        return dx * dx + dy * dy <= TAIL_R * TAIL_R
    return False


def draw() -> bytes:
    left, top, right, bottom = BOX
    span = max(right - left, bottom - top)
    inner = SIZE - 2 * PAD
    rows = bytearray()
    for row in range(SIZE):
        rows.append(0)                      # фильтр строки: без фильтра
        for col in range(SIZE):
            hits = 0
            for sy in range(SUPERSAMPLE):
                for sx in range(SUPERSAMPLE):
                    u = (col + (sx + 0.5) / SUPERSAMPLE - PAD) / inner
                    v = (row + (sy + 0.5) / SUPERSAMPLE - PAD) / inner
                    if covered(left + u * span, top + v * span):
                        hits += 1
            # Белым: template-иконку macOS перекрашивает сама — белой на тёмной
            # панели, чёрной на светлой, — и цвет здесь ни на что не влияет.
            rows += bytes((255, 255, 255, round(255 * hits / SUPERSAMPLE ** 2)))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
            + chunk(b"IEND", b""))


def dim(png: bytes, доля: float) -> bytes:
    """Тот же знак, но приглушённый: так выглядит остановленный узел.

    Приглушаем альфу, а не цвет: значок в панели template — цвет ему задаёт
    система, и любое наше значение она перекрасит. Видимой остаётся только
    прозрачность.
    """
    import zlib as _zlib

    raw = b""
    pos = 8
    while pos < len(png):
        ln = struct.unpack(">I", png[pos:pos + 4])[0]
        tag = png[pos + 4:pos + 8]
        if tag == b"IDAT":
            raw += png[pos + 8:pos + 8 + ln]
        pos += 12 + ln
    px = bytearray(_zlib.decompress(raw))
    stride = SIZE * 4
    for row in range(SIZE):
        start = row * (stride + 1) + 1
        for i in range(start + 3, start + stride, 4):
            px[i] = int(px[i] * доля)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(px), 9))
            + chunk(b"IEND", b""))


if __name__ == "__main__":
    здесь = pathlib.Path(__file__).parent
    живой = draw()
    (здесь / "tray.png").write_bytes(живой)
    (здесь / "tray-off.png").write_bytes(dim(живой, 0.35))
    for имя in ("tray.png", "tray-off.png"):
        print(f"{здесь / имя} — {(здесь / имя).stat().st_size} байт")

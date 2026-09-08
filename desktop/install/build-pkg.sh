#!/bin/bash
# Собрать установщик, который провайдер скачивает и открывает двойным щелчком.
#
# Запускается ОДИН РАЗ у нас, а не у каждого провайдера. На выходе — файл
# Looma-<версия>-<архитектура>.pkg, который кладёт на чужую машину три вещи:
#
#   /Applications/Looma.app                     панель
#   /Library/Application Support/Looma/runtime  свой питон с агентом внутри
#   /Library/LaunchDaemons/app.looma.agent.plist демон
#
# Питон свой, а не машинный: системный на macOS — 3.9 и таким останется, а
# агенту нужен 3.10+. Полагаться на то, что стоит у провайдера, значит не
# установиться у большинства из них.
#
# Архитектура — текущая. Бинарные колёса (grpcio, lattica) ставятся под ту
# машину, на которой идёт сборка, и собрать arm-пакет на Intel нельзя. Для
# второй архитектуры запустите этот же скрипт там.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP="$(cd "$HERE/.." && pwd)"
AGENT="$(cd "$DESKTOP/../agent" && pwd)"
OUT="${LOOMA_PKG_OUT:-$DESKTOP/dist-pkg}"
VERSION="${LOOMA_VERSION:-$(python3 -c "import json;print(json.load(open('$DESKTOP/src-tauri/tauri.conf.json'))['version'])")}"

# Питон закреплён версией И контрольной суммой. Ссылка без суммы — это
# обещание, что содержимое по ней не поменяется, а его никто не давал.
PY_RELEASE="20260901"
PY_VERSION="3.12.14"
case "$(uname -m)" in
    arm64)  PY_ARCH="aarch64"; PY_SHA="81a359f1cfadd4da11766534c5913791cea55f26e1bb902cacd2a531bb1e4b2b" ;;
    x86_64) PY_ARCH="x86_64";  PY_SHA="65b195c9cedc1fef6767f044f9822069adbd1bd9204d424ece4628776fdc04bb" ;;
    *) echo "неизвестная архитектура $(uname -m)" >&2; exit 1 ;;
esac
PY_FILE="cpython-${PY_VERSION}+${PY_RELEASE}-${PY_ARCH}-apple-darwin-install_only_stripped.tar.gz"
PY_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PY_RELEASE}/${PY_FILE}"

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
PKGROOT="$STAGE/root"
mkdir -p "$PKGROOT/Applications" \
         "$PKGROOT/Library/Application Support/Looma" \
         "$PKGROOT/Library/LaunchDaemons"

# ------------------------------------------------------------- панель
say "собираю панель"
( cd "$DESKTOP" && npm install --silent && npm run tauri build -- --bundles app )
APP="$DESKTOP/src-tauri/target/release/bundle/macos/Looma.app"
[ -d "$APP" ] || die "не нашёл собранное приложение в $APP"
# ditto, а не cp -R: приложение — бандл, и копировать его надо инструментом,
# который сохраняет права, ACL и расширенные атрибуты. Пока подписи нет,
# разницы не видно; с подписью cp -R её ломает, и выясняется это у того, кто
# скачал пакет.
ditto "$APP" "$PKGROOT/Applications/Looma.app"
# И убираем его из дерева сборки: приложение уехало в пакет, а оставленная
# копия попадает в Spotlight и показывается в поиске рядом с установленной,
# подписанная «debug» или «release». Человек видит три Looma и не знает, какая
# настоящая. Метки `.metadata_never_index` мало: уже проиндексированное она не
# убирает.
rm -rf "$APP"

# -------------------------------------------------------------- питон
CACHE="${LOOMA_PY_CACHE:-$OUT/cache}"
mkdir -p "$CACHE"
if [ ! -f "$CACHE/$PY_FILE" ]; then
    say "качаю питон $PY_VERSION ($PY_ARCH)"
    curl -fsSL --max-time 600 -o "$CACHE/$PY_FILE.part" "$PY_URL"
    mv "$CACHE/$PY_FILE.part" "$CACHE/$PY_FILE"
fi
say "проверяю контрольную сумму"
ACTUAL="$(shasum -a 256 "$CACHE/$PY_FILE" | cut -d' ' -f1)"
[ "$ACTUAL" = "$PY_SHA" ] || die "сумма не сошлась:
  ожидали $PY_SHA
  получили $ACTUAL
Файл подменён или ссылка стала указывать на другое. Не собираю."

RUNTIME="$PKGROOT/Library/Application Support/Looma/runtime"
mkdir -p "$RUNTIME"
# --strip-components=1: в архиве всё лежит под python/, а нам нужен runtime/bin
tar xzf "$CACHE/$PY_FILE" -C "$RUNTIME" --strip-components=1

# -------------------------------------------------------------- агент
say "ставлю агента внутрь питона"
# Внутрь, а не рядом в venv: слой venv здесь ничего не изолирует — питон и так
# только наш, — зато добавляет путь, который ломается при переносе.
"$RUNTIME/bin/python3" -m pip install --quiet --upgrade pip
"$RUNTIME/bin/python3" -m pip install --quiet "$AGENT[p2p]"
# Починить shebang в консольных скриптах. pip вписывает туда путь к питону той
# машины, где шла установка, — а ставим мы во временный каталог сборки, которого
# на машине провайдера не существует. Демон запускается модулем и этого не
# замечает, но `looma-agent` и `looma-launcher` руками — первое, чем человек
# попробует разобраться, когда что-то не так, и они обязаны работать.
for script in "$RUNTIME"/bin/looma-*; do
    [ -f "$script" ] || continue
    sed -i '' "1s|^#!.*|#!/Library/Application Support/Looma/runtime/bin/python3|" "$script"
done

# Кэш pip внутри пакета никому не нужен и весит больше самого агента.
rm -rf "$RUNTIME/lib/python3.12/test" "$RUNTIME/share" 2>/dev/null || true
find "$RUNTIME" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true

# -------------------------------------------------------------- демон
cp "$HERE/app.looma.agent.plist" "$PKGROOT/Library/LaunchDaemons/"

# -------------------------------------------------------------- подпись
# Необязательная: без неё пакет ставится на своей машине, но раздавать его
# нельзя. Идентификаторы передаются переменными, чтобы скрипт не пришлось
# править ради того, чего у большинства собирающих просто нет.
#
# Подписывать надо ИЗНУТРИ наружу: сначала встроенный питон и его модули с
# нативным кодом, потом приложение, и только потом пакет. Подпись, наложенная
# на бандл раньше его содержимого, становится недействительной от первого же
# изменения внутри.
if [ -n "${LOOMA_SIGN_ID:-}" ]; then
    say "подписываю приложение и питон"
    # --options runtime обязателен для нотаризации; питон запускает чужой код,
    # поэтому ему нужны послабления, иначе он не сможет ни собрать venv, ни
    # загрузить колёса с нативными модулями.
    find "$RUNTIME" \( -name "*.dylib" -o -name "*.so" -o -perm +111 -type f \) -print0 \
        | xargs -0 -I{} codesign --force --timestamp --options runtime \
                                 --entitlements "$HERE/runtime.entitlements" \
                                 --sign "$LOOMA_SIGN_ID" {} 2>/dev/null || true
    codesign --force --deep --timestamp --options runtime \
             --sign "$LOOMA_SIGN_ID" "$PKGROOT/Applications/Looma.app"
    codesign -v --verbose=2 "$PKGROOT/Applications/Looma.app" || die "подпись приложения не прошла проверку"
fi

# --------------------------------------------------------------- пакет
say "собираю пакет"
mkdir -p "$OUT"
PKG="$OUT/Looma-$VERSION-$(uname -m).pkg"
pkgbuild --root "$PKGROOT" \
         --scripts "$HERE/scripts" \
         --identifier app.looma.node \
         --version "$VERSION" \
         --install-location / \
         "$STAGE/component.pkg"
if [ -n "${LOOMA_INSTALLER_ID:-}" ]; then
    productbuild --package "$STAGE/component.pkg" --sign "$LOOMA_INSTALLER_ID" "$PKG"
else
    productbuild --package "$STAGE/component.pkg" "$PKG"
fi

say "готово: $PKG ($(du -h "$PKG" | cut -f1))"
cat <<TEXT

Проверить на этой машине — прямо сейчас, подпись для этого не нужна:

  sudo installer -pkg '$PKG' -target /
  sudo launchctl print system/app.looma.agent | head -5
  tail -f /Library/Logs/Looma/agent.log

Gatekeeper останавливает не «неподписанное», а «скачанное»: метку карантина
ставит браузер, и на файле, собранном здесь же, её нет. Дальше — открыть Looma
из Программ и вставить ключ узла. Снять: sudo bash '$HERE/uninstall.sh'

Чтобы РАЗДАВАТЬ, нужен Developer ID: у того, кто скачает пакет, карантин
появится, и без подписи macOS его не откроет.

  productsign --sign 'Developer ID Installer: ...' '$PKG' '$PKG.signed'
  xcrun notarytool submit '$PKG.signed' --keychain-profile looma --wait
  xcrun stapler staple '$PKG.signed'
TEXT

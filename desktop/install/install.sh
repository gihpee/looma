#!/bin/bash
# Установка узла Looma на macOS ИЗ ИСХОДНИКОВ.
#
# Это путь для разработки и своих машин. Провайдеру предназначен .pkg — его
# скачивают с сайта и открывают двойным щелчком, и он несёт свой питон
# (build-pkg.sh). Здесь питон берётся с машины, которого у обычного человека
# нужной версии не окажется.
#
# Ставит три вещи и ничего больше:
#   * служебного пользователя _looma, под которым идут чужие задачи;
#   * агента и его окружение в /Library/Application Support/Looma;
#   * демон launchd, который держит агента запущенным.
#
# Ключ узла здесь не спрашивается: его вводят потом, в панели. Агент дождётся.
#
# Читается сверху вниз намеренно — это скрипт, который человек запускает с
# правами root на своей машине, и он вправе понять, что тот делает.

set -euo pipefail

ROOT="/Library/Application Support/Looma"
LOGS="/Library/Logs/Looma"
PLIST="/Library/LaunchDaemons/app.looma.agent.plist"
LABEL="app.looma.agent"
TASK_USER="_looma"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="${LOOMA_AGENT_SOURCE:-$HERE/../../agent}"

say() { printf '\033[1m%s\033[0m\n' "$*"; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" = "0" ] || die "запустите через sudo: sudo bash $0"
[ -d "$SOURCE/looma_agent" ] || die "не нахожу исходники агента в $SOURCE"

# ---------------------------------------------------------------- питон
# Агенту нужен 3.10 и новее, а системный на macOS — 3.9 и таким останется.
# Свой питон установщик пока не несёт; когда понесёт, этот блок исчезнет
# целиком (см. README).
find_python() {
    if [ -n "${LOOMA_PYTHON:-}" ]; then echo "$LOOMA_PYTHON"; return; fi
    local candidate
    for candidate in /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 \
                     /opt/homebrew/bin/python3.11 /usr/local/bin/python3.12 \
                     "$(command -v python3.13 || true)" \
                     "$(command -v python3.12 || true)" \
                     "$(command -v python3.11 || true)" \
                     "$(command -v python3 || true)"; do
        [ -x "$candidate" ] || continue
        if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
            echo "$candidate"; return
        fi
    done
}

PYTHON="$(find_python)"
[ -n "$PYTHON" ] || die "нужен python 3.10 или новее; системный на macOS — 3.9.
Поставьте его (brew install python@3.12) или укажите свой:
  sudo LOOMA_PYTHON=/путь/к/python3 bash $0"
say "питон: $PYTHON ($("$PYTHON" -V 2>&1))"

# ------------------------------------------------- служебный пользователь
# Под ним идут чужие задачи. Без него агент откажется их запускать — и это
# правильное поведение, а не придирка: задача под пользователем агента читает
# ключ узла и каталоги соседних задач.
if dscl . -read "/Users/$TASK_USER" >/dev/null 2>&1; then
    say "пользователь $TASK_USER уже есть"
else
    # Свободный номер в диапазоне служебных. Перебором, а не наугад: занятый
    # uid означал бы двух пользователей с одними правами.
    NEXT_UID=300
    while dscl . -list /Users UniqueID | awk '{print $2}' | grep -qx "$NEXT_UID"; do
        NEXT_UID=$((NEXT_UID + 1))
    done
    say "завожу пользователя $TASK_USER (uid $NEXT_UID)"
    dscl . -create "/Users/$TASK_USER"
    dscl . -create "/Users/$TASK_USER" UserShell /usr/bin/false
    dscl . -create "/Users/$TASK_USER" RealName "Looma tasks"
    dscl . -create "/Users/$TASK_USER" UniqueID "$NEXT_UID"
    dscl . -create "/Users/$TASK_USER" PrimaryGroupID 20
    dscl . -create "/Users/$TASK_USER" NFSHomeDirectory /var/empty
    # Прячем из окна входа: это не человек.
    dscl . -create "/Users/$TASK_USER" IsHidden 1
fi

# ------------------------------------------------------------- каталоги
say "каталоги в $ROOT"
mkdir -p "$ROOT" "$ROOT/tasks" "$ROOT/envs" "$ROOT/models" "$LOGS"
# Группа admin — чтобы панель, работающая под обычным пользователем, могла
# положить сюда ключ. Всё остальное внутри принадлежит root.
chown root:admin "$ROOT"
chmod 775 "$ROOT"
chown -R root:wheel "$ROOT/tasks" "$ROOT/envs" "$ROOT/models"
chmod 755 "$ROOT/tasks" "$ROOT/envs" "$ROOT/models"
chown root:admin "$LOGS"
chmod 775 "$LOGS"

# ------------------------------------------------------------- агент
say "ставлю агента"
rm -rf "$ROOT/venv"
"$PYTHON" -m venv "$ROOT/venv"
"$ROOT/venv/bin/pip" install --quiet --upgrade pip
# [p2p] — прямой путь между узлами; без него всё едет через оркестратор.
# cuda-часть на Mac не нужна и не ставится.
"$ROOT/venv/bin/pip" install --quiet "$SOURCE[p2p]"
chown -R root:wheel "$ROOT/venv"

# ------------------------------------------------------------- демон
say "ставлю демон $LABEL"
sed -e "s|__PYTHON__|$ROOT/venv/bin/python|g" \
    -e "s|__AGENT__|$ROOT/venv/lib|g" \
    "$HERE/app.looma.agent.plist" > "$PLIST"
chown root:wheel "$PLIST"
chmod 644 "$PLIST"

# bootout перед bootstrap: повторная установка иначе падает на «уже загружен»,
# и человек получает ошибку там, где ничего не сломано.
launchctl bootout system "$PLIST" 2>/dev/null || true
launchctl bootstrap system "$PLIST"

say ""
say "Готово."
say "Осталось вставить ключ узла в приложении Looma."
say "Лог агента: $LOGS/agent.log"

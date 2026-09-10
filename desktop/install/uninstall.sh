#!/bin/bash
# Снять узел Looma с этой машины.
#
#   без аргументов   останавливает узел, данные оставляет
#   --purge          сносит всё: приложения, данные, служебного пользователя
#
# По умолчанию данные остаются намеренно. В кэше окружений и весов лежат
# десятки гигабайт, скачанных однажды, и стирать их у того, кто просто хотел
# остановить узел на неделю, — не то, чего он ждал.

set -euo pipefail

ROOT="/usr/local/looma"
LOGS="/Library/Logs/Looma"
PLIST="/Library/LaunchDaemons/app.looma.agent.plist"
TASK_USER="_looma"
PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

[ "$(id -u)" = "0" ] || { echo "запустите через sudo" >&2; exit 1; }

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }

say "останавливаю демон"
launchctl bootout system "$PLIST" 2>/dev/null || true
rm -f "$PLIST"

if [ "$PURGE" = "0" ]; then
    echo
    echo "Демон снят, данные оставлены. Снести всё вместе с кэшами:"
    echo "  sudo bash $0 --purge"
    exit 0
fi

# Все копии приложения, а не одна: пока пакет собирали и пробовали, их могло
# накопиться несколько — «Looma 2.app» и так далее. Ищем по идентификатору, а
# не по имени: одноимённое чужое приложение сносить мы не станем.
say "убираю приложения"
for app in /Applications/Looma*.app; do
    [ -d "$app" ] || continue
    id="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' \
            "$app/Contents/Info.plist" 2>/dev/null || true)"
    if [ "$id" = "app.looma.desktop" ]; then
        echo "  удаляю $app"
        rm -rf "$app"
    else
        echo "  пропускаю $app (чужое: ${id:-без идентификатора})"
    fi
done

say "убираю данные и логи"
# И прежний каталог: до переезда узел жил в пути с пробелом, который ломал Ray.
rm -rf "$ROOT" "$LOGS" "/Library/Application Support/Looma"

say "убираю служебного пользователя"
dscl . -delete "/Users/$TASK_USER" 2>/dev/null || true

# Launchpad помнит удалённые приложения и продолжает показывать их значки —
# ровно те «призраки», из-за которых кажется, что удаление не сработало.
say "обновляю Launchpad"
sudo -u "${SUDO_USER:-$USER}" defaults write com.apple.dock ResetLaunchPad -bool true 2>/dev/null || true
killall Dock 2>/dev/null || true

# Забыть о пакете: иначе `pkgutil --pkgs` продолжает считать его установленным,
# и повторная установка спорит сама с собой.
pkgutil --forget app.looma.node >/dev/null 2>&1 || true

echo
say "чисто"

#!/bin/bash
# Что сейчас с узлом на этой машине.
#
# После установки первый вопрос — «работает или нет», и ответ на него собран из
# пяти разных мест: launchd, лог, файл состояния, права и служебный
# пользователь. Ходить по ним руками каждый раз — потерянное время, а половину
# ещё и надо знать наизусть.

ROOT="/usr/local/looma"
LOG="/Library/Logs/Looma/agent.log"
LABEL="app.looma.agent"

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
no()   { printf '  \033[31m✗\033[0m %s\n' "$*"; }
head_() { printf '\n\033[1m%s\033[0m\n' "$*"; }

head_ "Установка"
[ -d "/Applications/Looma.app" ] && ok "панель на месте" || no "нет /Applications/Looma.app"
[ -x "$ROOT/runtime/bin/python3" ] && ok "питон: $("$ROOT/runtime/bin/python3" -V 2>&1)" \
                                   || no "нет питона в $ROOT/runtime"
[ -f "/Library/LaunchDaemons/$LABEL.plist" ] && ok "демон прописан" || no "демон не прописан"
dscl . -read /Users/_looma >/dev/null 2>&1 && ok "пользователь _looma заведён" \
                                           || no "нет пользователя _looma — задачи запускаться не будут"

head_ "Демон"
if launchctl print "system/$LABEL" >/dev/null 2>&1; then
    PID="$(launchctl print "system/$LABEL" 2>/dev/null | awk '/^\tpid = /{print $3}')"
    if [ -n "$PID" ]; then ok "работает, pid $PID"; else no "загружен, но не работает"; fi
else
    no "не загружен (sudo launchctl bootstrap system /Library/LaunchDaemons/$LABEL.plist)"
fi

head_ "Ключ узла"
if [ -f "$ROOT/join.key" ]; then
    ok "введён ($(stat -f '%Sp %Su' "$ROOT/join.key"))"
else
    no "не введён — откройте Looma и вставьте ключ"
fi
# Панель работает под обычным пользователем; без права записи в корень она
# ключ положить не сможет, и выглядеть это будет как отказ без причины.
[ -w "$ROOT" ] && ok "панель сможет записать ключ" \
               || no "$ROOT недоступен вам на запись — панель не сохранит ключ"

head_ "Разрешение владельца"
if [ -f "$ROOT/paused" ]; then
    no "узел ОСТАНОВЛЕН из панели — работы не берёт"
else
    ok "узел не остановлен"
fi

head_ "Состояние узла"
if [ -f "$ROOT/status.json" ]; then
    /usr/bin/python3 - "$ROOT/status.json" <<'PY'
import json, sys, time
data = json.load(open(sys.argv[1]))
age = time.time() - data.get("updated_at", 0)
print(f"  узел {data.get('node_id','?')}, агент {data.get('agent_version','?')}")
print(f"  железо: {data.get('gpu_name','?')} ({data.get('device','?')})")
print(f"  задач сейчас: {data.get('tasks_running', 0)}")
print(f"  обновлено {age:.0f} с назад" + ("  ← агент замолчал" if age > 30 else ""))
if not data.get("accepts_tasks", True):
    print(f"  НЕ БЕРЁТ РАБОТУ: {data.get('refusal','причина не названа')}")
PY
else
    no "агент ещё не сообщал о себе"
fi

head_ "Последнее из лога"
[ -f "$LOG" ] && tail -5 "$LOG" | sed 's/^/  /' || no "лога нет: $LOG"
echo

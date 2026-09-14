"""Looma node agent.

The process that connects a machine to Looma. It holds no models and runs no
computation itself: work arrives as a task, gets its own directory and its own
environment, and runs there. See docs/WORKER_RUNTIME.md.
"""

import os

# gRPC и fork. Ставится ЗДЕСЬ, до первого `import grpc` где бы то ни было в
# пакете: движок опроса gRPC выбирает при загрузке и потом не меняет.
#
# Что происходит без этого. Процесс с работающим gRPC (наш агент, прокси
# клиентского сервера Ray) делает fork — запускает задачу, воркер, ещё один
# сервер. gRPC пытается перед fork'ом усыпить свои потоки; не успевает —
# печатает «Other threads are currently calling into gRPC, skipping fork()
# handlers» и отдаёт ребёнку живое состояние epoll. Дальше ребёнок падает
# на внутренней проверке:
#
#     F ... ev_epoll1_linux.cc:1121] Check failed: next_worker->state == KICKED
#
# Со стенда (nv2): так умирал клиентский сервер Ray при ПЕРВОМ подключении
# после старта кластера — молча, с пустым логом, потому что до Python дело
# не доходило. Повтор проходил, потому что это гонка.
#
# Движок `poll` — единственный, для которого gRPC обещает безопасный fork
# (doc/fork_support.md). `epoll1`, умолчание на Linux, этого не обещает и,
# как видно выше, не выполняет. Цена — чуть больше процессора под большой
# нагрузкой; у управляющего канала её нет.
os.environ.setdefault("GRPC_ENABLE_FORK_SUPPORT", "1")
os.environ.setdefault("GRPC_POLL_STRATEGY", "poll")

# The launcher tells us which payload we are. Falls back to the packaged
# version when the agent is started directly, e.g. in tests.
__version__ = os.environ.get("LOOMA_AGENT_VERSION") or "0.1.0"

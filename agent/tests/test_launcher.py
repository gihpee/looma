"""The launcher must survive a broken update, because nothing else can save it."""

from __future__ import annotations

from pathlib import Path

from looma_launcher import payload


def test_bundled_agent_is_the_floor(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOMA_ROOT", str(tmp_path))
    chosen = payload.resolve()
    assert chosen.bundled
    assert chosen.version


def test_dangling_current_falls_back_instead_of_dying(tmp_path, monkeypatch):
    """A half-finished update leaves a symlink pointing nowhere.

    The node must come up on the agent we know is intact, not stay dead.
    """
    monkeypatch.setenv("LOOMA_ROOT", str(tmp_path))
    (tmp_path / "agent").mkdir()
    link = tmp_path / "agent" / "current"
    link.symlink_to(tmp_path / "agent" / "0.9.9-that-never-arrived")
    assert payload.resolve(link).bundled


def test_incomplete_payload_is_not_run(tmp_path, monkeypatch):
    """A directory exists but holds no agent: still not something to run."""
    monkeypatch.setenv("LOOMA_ROOT", str(tmp_path))
    version_dir = tmp_path / "agent" / "0.2.0"
    version_dir.mkdir(parents=True)
    link = tmp_path / "agent" / "current"
    link.symlink_to(version_dir)
    assert payload.resolve(link).bundled


def test_complete_payload_is_selected(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOMA_ROOT", str(tmp_path))
    version_dir = tmp_path / "agent" / "0.2.0" / "looma_agent"
    version_dir.mkdir(parents=True)
    (version_dir / "main.py").write_text("")
    link = tmp_path / "agent" / "current"
    link.symlink_to(version_dir.parent)
    chosen = payload.resolve(link)
    assert not chosen.bundled
    assert chosen.version == "0.2.0"
    assert chosen.path == version_dir.parent


# ------------------------------------------------- отвергнутый релиз не крутится
def test_отказ_записывается_на_диск(tmp_path, monkeypatch):
    """Без этого узел качает отвергнутое заново при каждом подключении, отказ
    повторяется вместе с перезапуском — и так по кругу, раз в секунду."""
    import json

    from looma_launcher import payload as payload_mod

    monkeypatch.setenv("LOOMA_ROOT", str(tmp_path))
    incoming = payload_mod.incoming_dir()
    incoming.mkdir(parents=True, exist_ok=True)
    manifest = incoming / "0.0.1.json"
    manifest.write_text(json.dumps({"version": "0.0.1", "sha256": "нет",
                                    "signature": "00"}))
    (incoming / "0.0.1.tar.gz").write_bytes(b"not a real archive")

    assert payload_mod.install(manifest, installed_version="0.1.0") is None
    written = (payload_mod.refused_dir() / "0.0.1.txt").read_text()
    assert "not newer" in written


def test_отказ_переживает_перезапуск_агента(tmp_path, monkeypatch):
    """Состояние агента живёт ровно столько же, сколько его процесс."""
    from looma_agent.update import refusal_for

    incoming = tmp_path / "agent" / "incoming"
    incoming.mkdir(parents=True)
    refused = tmp_path / "agent" / "refused"
    refused.mkdir()
    (refused / "0.0.1.txt").write_text("не новее запущенной 0.1.0\n")
    monkeypatch.setenv("LOOMA_AGENT_INCOMING", str(incoming))

    assert "не новее" in refusal_for("0.0.1")
    assert refusal_for("9.9.9") == ""


def test_старый_лаунчер_без_отказов_не_ломает_агента(tmp_path, monkeypatch):
    """Он этих файлов не пишет — тогда защиты просто нет, ровно как раньше."""
    from looma_agent.update import refusal_for

    monkeypatch.setenv("LOOMA_AGENT_INCOMING", str(tmp_path / "incoming"))
    assert refusal_for("0.0.1") == ""


def test_отвергнутый_релиз_больше_не_качается(tmp_path, monkeypatch):
    """Скачать его снова — значит слить задачи, перезапуститься, получить тот
    же отказ и начать заново."""
    from looma_agent import update as update_mod
    from looma_agent.proto import agent_pb2

    incoming = tmp_path / "agent" / "incoming"
    incoming.mkdir(parents=True)
    (tmp_path / "agent" / "refused").mkdir()
    (tmp_path / "agent" / "refused" / "0.0.1.txt").write_text("не новее 0.1.0")
    monkeypatch.setenv("LOOMA_AGENT_INCOMING", str(incoming))

    fetched = []
    monkeypatch.setattr(update_mod.Updater, "_carry_out",
                        lambda self, release: fetched.append(release.version))
    updater = update_mod.Updater(current_version="0.1.0", drain=lambda _s: True,
                                 stop=lambda: None)
    updater.on_release(agent_pb2.AgentRelease(version="0.0.1", url="http://где-то"))

    assert fetched == []
    assert updater.status().state == "refused"
    assert "не новее" in updater.status().error


# ------------------------------------------------ два агента на одной машине
def _archive(where: Path, version: str) -> Path:
    """Настоящий архив релиза: с агентом внутри."""
    import tarfile

    inside = where / "src" / "looma_agent"
    inside.mkdir(parents=True)
    (inside / "main.py").write_text("# agent\n")
    path = where / f"{version}.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        tar.add(inside, arcname="looma_agent")
    return path


def test_чужая_распаковка_не_стирается(tmp_path, monkeypatch):
    """Со стенда: три агента на одной машине, том общий, релиз один.

    Каталог распаковки назывался по PID, а в своих контейнерах у всех троих он
    седьмой. Все брали одно имя, и `rmtree` перед своей распаковкой сносил
    чужую — наполовину готовую. Наружу это выходило как «the release archive
    holds no agent», то есть обвинением исправного архива.

    Здесь сосед изображён каталогом, который уже распаковывается: на старом
    коде он исчезает, на новом остаётся.
    """
    monkeypatch.setenv("LOOMA_ROOT", str(tmp_path))
    monkeypatch.setattr(payload.os, "getpid", lambda: 7)
    agents = tmp_path / "agent"
    agents.mkdir(parents=True)

    сосед = agents / f"{payload.BUILDING_PREFIX}0.1.1-7"
    (сосед / "looma_agent").mkdir(parents=True)
    (сосед / "looma_agent" / "main.py").write_text("# сосед ещё распаковывает\n")

    payload._unpack(_archive(tmp_path, "0.1.1"), "0.1.1")
    assert (сосед / "looma_agent" / "main.py").is_file(), (
        "распаковка соседа снесена — это и есть та самая ошибка")


def test_каталоги_распаковки_разные_у_каждого(tmp_path, monkeypatch):
    """Имя обязано быть уникальным, иначе всё предыдущее возвращается."""
    monkeypatch.setenv("LOOMA_ROOT", str(tmp_path))
    monkeypatch.setattr(payload.os, "getpid", lambda: 7)
    (tmp_path / "agent").mkdir(parents=True, exist_ok=True)

    видели = []
    было = payload.Path.mkdir

    def засечь(self, *a, **kw):
        if self.name.startswith(payload.BUILDING_PREFIX):
            видели.append(self.name)
        return было(self, *a, **kw)

    monkeypatch.setattr(payload.Path, "mkdir", засечь)
    archive = _archive(tmp_path, "0.1.1")
    payload._unpack(archive, "0.1.1")
    payload._unpack(archive, "0.1.1")
    assert len(set(видели)) == len(видели) == 2, f"имена повторились: {видели}"


def test_брошенный_каталог_убирается_а_свежий_нет(tmp_path, monkeypatch):
    """Уникальное имя больше не переиспользуется, поэтому мусор надо убирать.
    Но только старый: рядом может распаковываться сосед."""
    import os as _os
    import time

    monkeypatch.setenv("LOOMA_ROOT", str(tmp_path))
    agents = tmp_path / "agent"
    agents.mkdir(parents=True)
    старый = agents / f"{payload.BUILDING_PREFIX}0.1.0-deadbeef"
    свежий = agents / f"{payload.BUILDING_PREFIX}0.1.1-cafebabe"
    for path in (старый, свежий):
        path.mkdir()
    давно = time.time() - payload.STALE_STAGING_S - 60
    _os.utime(старый, (давно, давно))

    payload._sweep_stale(agents)
    assert not старый.exists(), "брошенный каталог должен убираться"
    assert свежий.exists(), "каталог соседа трогать нельзя"


def test_падающая_версия_запоминается_и_не_качается_снова(tmp_path, monkeypatch):
    """Со стенда: узел скачал релиз, тот упал три раза, лаунчер откатился к
    прежней версии — и она немедленно скачала ту же самую снова. Цикл повторялся
    каждые двенадцать секунд, а со стороны выглядел как «узел то появляется, то
    пропадает».

    Откат без записи отказа ничего не решает: предложение от оркестратора
    приходит при каждом подключении и не меняется от того, что мы уже пробовали.
    """
    from looma_launcher import payload as payload_mod
    from looma_launcher.supervise import FAILURES_BEFORE_ROLLBACK, Supervisor

    monkeypatch.setenv("LOOMA_ROOT", str(tmp_path))
    monkeypatch.setenv("LOOMA_AGENT_INCOMING", str(tmp_path / "agent" / "incoming"))
    плохая = payload_mod.Payload(version="0.9.9", path=tmp_path / "agent" / "0.9.9")
    supervisor = Supervisor(плохая, [])
    supervisor.consecutive_failures = FAILURES_BEFORE_ROLLBACK

    supervisor._consider_rollback()

    from looma_agent.update import refusal_for
    assert "падала" in refusal_for("0.9.9"), "агент снова скачает эту версию"


def test_ключ_из_файла_доезжает_до_агента_любой_версии(tmp_path):
    """Со стенда: узел на macOS получил по сети релиз, собранный без чтения
    ключа из файла, и упал на «no join key» три раза подряд — а дальше пошёл
    откат и повторное скачивание того же релиза по кругу.

    Пусковой слой чинит это раз и навсегда: агент получает ключ аргументом,
    ровно как в контейнере, и знать про файл не обязан."""
    from looma_launcher.main import _with_key

    (tmp_path / "join.key").write_text("looma_abc\n")

    assert _with_key([], tmp_path) == ["--key", "looma_abc"]


def test_переданный_ключ_не_подменяется_файлом(tmp_path):
    """В контейнере ключ приходит аргументом, и он главнее: файл рядом может
    остаться от прошлой жизни этого тома."""
    from looma_launcher.main import _with_key

    (tmp_path / "join.key").write_text("looma_старый\n")

    assert _with_key(["--key", "looma_новый"], tmp_path) == ["--key", "looma_новый"]


def test_без_файла_ключа_ничего_не_добавляется(tmp_path):
    from looma_launcher.main import _with_key

    assert _with_key(["--region", "eu"], tmp_path) == ["--region", "eu"]


def test_пауза_действует_на_агента_любой_версии(tmp_path, monkeypatch):
    """Со стенда: кнопку «остановить» сделали в агенте, узел обновился до
    релиза без неё — и кнопка перестала действовать, хотя файл создавала
    исправно. Управление узлом обязано жить в пусковом слое: он меняется только
    вместе с пакетом, а агент приезжает по сети."""
    from looma_launcher.supervise import Supervisor
    from looma_launcher import payload as payload_mod

    monkeypatch.setenv("LOOMA_ROOT", str(tmp_path))
    (tmp_path / "paused").write_text("остановлено")
    supervisor = Supervisor(payload_mod.bundled(), [])

    assert supervisor._paused()

    # Ждать он будет, пока файл не уберут; проверяем, что дожидается именно
    # снятия, а не срока.
    import threading
    threading.Timer(0.3, (tmp_path / "paused").unlink).start()
    supervisor._wait_while_paused()

    assert not supervisor._paused()


def test_пока_узел_стоит_панель_знает_версию_и_причину(tmp_path, monkeypatch):
    """Иначе остановленный узел и мёртвый выглядят одинаково: снимок пишет
    агент, а он в этот момент не работает."""
    import json

    from looma_launcher.supervise import Supervisor
    from looma_launcher import payload as payload_mod

    monkeypatch.setenv("LOOMA_ROOT", str(tmp_path))
    (tmp_path / "paused").write_text("остановлено")
    Supervisor(payload_mod.bundled(), [])._say(running=False)

    снимок = json.loads((tmp_path / "launcher.json").read_text())
    assert снимок["paused"] is True and снимок["running"] is False
    assert снимок["agent_version"], "без версии панель покажет чужую"


def test_служебный_пользователь_передаётся_агенту(monkeypatch):
    """Со стенда: установщик заводит `_looma` (на macOS у служебных
    пользователей имена с подчёркиванием), а приехавший по сети агент искал
    линуксовое `looma-task`, не находил и отказывался брать работу. Узел
    подключался и стоял со словами «не берёт»."""
    import sys as _sys

    from looma_launcher.supervise import _task_user

    if _sys.platform != "darwin":
        assert _task_user() == "", "на Linux имя выбирает сам агент"
        return
    # На macOS: имя подставляется, только если пользователь ЕСТЬ. Имени,
    # которого на машине нет, ничем не лучше отсутствующего, зато оно скрывает
    # настоящую причину за другой.
    import pwd

    имя = _task_user()
    if имя:
        pwd.getpwnam(имя)          # не бросит: пользователь существует


def test_снимок_пускового_слоя_обновляется_а_не_пишется_однажды(tmp_path, monkeypatch):
    """Панель считает узел замолчавшим, если снимку больше полуминуты, — и
    правильно делает, иначе умерший агент выглядел бы работающим вечно. Со
    стенда: «узел работает», а через несколько секунд «агент замолчал»."""
    import json
    import time as _time

    from looma_launcher import payload as payload_mod
    from looma_launcher.supervise import Supervisor

    monkeypatch.setenv("LOOMA_ROOT", str(tmp_path))
    supervisor = Supervisor(payload_mod.bundled(), [])

    supervisor._say(running=True)
    первый = json.loads((tmp_path / "launcher.json").read_text())["updated_at"]
    _time.sleep(0.05)
    supervisor._say(running=True)
    второй = json.loads((tmp_path / "launcher.json").read_text())["updated_at"]

    assert второй > первый, "снимок не обновляется — панель решит, что узел замолчал"

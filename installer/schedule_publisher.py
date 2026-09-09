# -*- coding: utf-8 -*-
"""
schedule_publisher — регистрация РАСПИСАНИЯ ВЫПУСКА на машине клиента.

ЗАЧЕМ. Завод раз в несколько минут заглядывает в очередь публикаций и выпускает
то, ЧТО ЧЕЛОВЕК УЖЕ ОДОБРИЛ и чему подошло время. Но саму «будилку», которая
дёргает очередь, у Валерии заводили РУКАМИ через Планировщик заданий Windows.
Кода регистрации не было нигде — значит у клиента её не создавал никто, и
расписание у него просто не работало. Этот файл закрывает дыру: ставит будилку
при установке и снимает при удалении, на обеих ОС.

⚠️ ЧТО ЭТА БУДИЛКА НЕ ДЕЛАЕТ. Она НЕ решает, что публиковать, и не может
опубликовать неодобренное. Единственное, что она умеет, — позвать
`publish_queue.py --run`, а очередь с 23.08.2026 выпускает ТОЛЬКО записи с
человеческой отметкой одобрения (`publish_queue.is_approved()`); подошедшее по
времени, но не одобренное, она не отправляет, а пишет, что ждёт одобрения.
Поэтому «включено расписание» и «завод публикует сам по себе» — разные вещи:
без `--approve` расписание крутится вхолостую, и так задумано.

ЧТО ИМЕННО СТАВИТСЯ. Обе стороны зовут ОДНО И ТО ЖЕ (см. run_argv() — команда
собрана в одном месте, чтобы Windows и мак не разъехались):
  Windows — задание Планировщика «Контент-завод — выпуск по расписанию»
    (schtasks /Create /XML). Раз в 5 минут зовёт
    `.venv\\Scripts\\pythonw.exe publish_queue.py --run`.
  macOS  — агент запуска ~/Library/LaunchAgents/ru.kontentzavod.publish.plist
    (launchctl), StartInterval 300 секунд, то же самое через `.venv/bin/python3`.

ПОЧЕМУ 5 МИНУТ. Время выхода в очереди задаётся с точностью до минуты
(«2026-08-15 09:15»). Опрос раз в 5 минут — это максимум 5 минут опоздания, что
для ленты незаметно. Чаще — лишние пробуждения ноутбука ради чтения одного json;
реже — опоздание становится видимым и растёт риск упереться в границу догона
(`--catchup-hours`, по умолчанию 6 часов). Ровно 5 минут работают на заводе
Валерии с 13.08.2026 — берём обкатанное значение. Меняется флагом
`--interval-min`, но по умолчанию не трогаем.

ПОЧЕМУ ТОЛЬКО ПРИ ВОШЕДШЕМ ПОЛЬЗОВАТЕЛЕ (осознанный выбор, не недоделка).
  Задание «работает, когда пользователь не вошёл» в Windows требует одного из
  двух: либо запуск от SYSTEM (нужны права администратора — а установщик ставит
  завод в профиль пользователя и админа НЕ просит), либо сохранённого ПАРОЛЯ
  Windows этого клиента (`schtasks /RU ... /RP ...`). Пароль клиента мы не
  спрашиваем и не храним — это не наше дело. Да и от SYSTEM публикация всё равно
  сломалась бы: у SYSTEM нет профиля клиента, нет подключённого Google Диска и
  нет сохранённых доступов к площадкам.
  Поэтому задание живёт в сеансе клиента (InteractiveToken) и работает, пока он
  в системе. Пропущенное не теряется:
    * в задании включено «запускать при первой возможности» (StartWhenAvailable) —
      после включения компьютера очередь дёргается сразу, не дожидаясь круга;
    * сама очередь догоняет пропущенное за последние `--catchup-hours` (6 ч), а
      что старше — помечает `missed` и НЕ вываливает пачкой в ленту.
  И главное: задание — это РЕЗЕРВ. Основной путь автовыпуска — отложка на
  стороне площадок (`--at` кладёт запись в «Отложенные» ВК и в scheduled_date
  Upload-Post), там компьютер клиента вообще не нужен.

ПОЧЕМУ НЕ МЕЛЬКАЕТ ЧЁРНОЕ ОКНО. Зовём `pythonw.exe` — тот же интерпретатор без
консоли. Он лежит рядом с `python.exe` внутри установки, потому что
`offline_install.make_venv_from_embed()` копирует встраиваемый дистрибутив
целиком. Если `pythonw.exe` вдруг не окажется — откатываемся на `python.exe`,
но честно пишем об этом в проверке.

ПОЧЕМУ НЕ ГОЛОЕ СЛОВО `python`. На чистой Windows 10/11 команда `python` есть
ВСЕГДА, но это заглушка Microsoft Store, а не питон (install/stages.json,
os_support.windows.differs). Интерпретатор берём только внутри установки.

ЗАПУСК:
    python installer/schedule_publisher.py --install      # завести расписание
    python installer/schedule_publisher.py --status       # живо ли оно
    python installer/schedule_publisher.py --status --json
    python installer/schedule_publisher.py --remove       # снять
    python installer/schedule_publisher.py --install --dry-run

КОДЫ ВОЗВРАТА (как у doctor модулей, CONTRACT.md §2):
    0 — расписание заведено и включено;
    2 — урезанный режим: расписания нет, но это не поломка установки
        (например, в коробке нет самой очереди publish_queue.py);
    1 — не смогли: команда ОС отказала.
Установку код возврата НЕ роняет: в .iss шаг помечен как необязательный.

Только stdlib. Кроссплатформенно.
"""
from __future__ import annotations

import argparse
import json

import os
import plistlib
import subprocess
import sys
from pathlib import Path

try:                                   # русский текст не должен убивать вывод
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:                      # noqa: BLE001 — старый питон, не критично
    pass

# ── ИМЕНА. Их видит нетехнический клиент в списке заданий — только по-русски.
#    ⚠️ TASK_NAME продублировано в installer/kontent-zavod.iss (секция
#    [UninstallRun], страховочный вызов schtasks /Delete). Меняешь здесь —
#    поменяй и там.
TASK_NAME = "Контент-завод — выпуск по расписанию"
TASK_DESC = ("Раз в несколько минут проверяет очередь выпуска Контент-завода и "
             "отправляет то, что вы ОДОБРИЛИ и чему подошло время. "
             "Неодобренное не публикует никогда — оно ждёт вашего «да». "
             "Если выключить — одобренное перестанет выходить в срок.")

# macOS: обратный домен — требование launchd, клиенту это имя показывать не надо.
MAC_LABEL = "ru.kontentzavod.publish"

DEFAULT_INTERVAL_MIN = 5
QUEUE_SCRIPT = "publish_queue.py"


def is_windows() -> bool:
    return sys.platform.startswith("win")


def is_macos() -> bool:
    return sys.platform == "darwin"


def find_root(explicit: str | None = None) -> Path:
    """Корень установки. Те же маркеры, что у install/run_doctors.py."""
    if explicit:
        return Path(explicit).resolve()
    here = Path(__file__).resolve().parent
    for cand in (here, *here.parents):
        for marker in (".factory_root", "modules", "CONTRACT.md"):
            if (cand / marker).exists():
                return cand
    return here.parent


def queue_script(root: Path) -> Path | None:
    """Файл очереди публикаций. None — если его в этой коробке нет."""
    p = root / QUEUE_SCRIPT
    return p if p.is_file() else None


def interpreter(root: Path) -> tuple[Path | None, bool]:
    """(путь к питону внутри установки, тихий ли он).

    Тихий = pythonw.exe, который не открывает окно консоли при каждом срабатывании.
    Голое `python` не возвращаем НИКОГДА: на чистой Windows это заглушка Store.

    ⚠️ Порядок для мака — `python3`, потом `python`, ровно как в
    install/check_env.py::BOX_PYTHON_RELPATHS. Раньше здесь проверялся ТОЛЬКО
    `.venv/bin/python`, а `make_venv` на маке кладёт в `.venv/bin` пару
    `python3` + симлинк `python`, и в урезанных сборках второго может не быть —
    тогда interpreter() возвращал None и расписание на маке не заводилось вовсе
    (код 2, «окружение не развёрнуто»), хотя питон в коробке был.
    """
    if is_windows():
        scripts = root / ".venv" / "Scripts"
        quiet = scripts / "pythonw.exe"
        if quiet.is_file():
            return quiet, True
        loud = scripts / "python.exe"
        if loud.is_file():
            return loud, False
        return None, False
    for name in ("python3", "python"):
        nix = root / ".venv" / "bin" / name
        if nix.is_file():
            # На маке окна консоли нет в принципе: launchd запускает задачу без
            # терминала, у процесса просто нет управляющего tty. Отдельного
            # «тихого» интерпретатора, как pythonw.exe, там не нужно —
            # StandardOutPath ловит вывод в файл, клиент ничего не видит.
            return nix, True
    return None, False


def run_argv(py: Path, script: Path) -> list[str]:
    """Команда круга — ОДНА на обе ОС. Windows и мак обязаны звать одно и то же.

    Держим одной функцией специально: раньше строка собиралась в win_install()
    и в mac_install() порознь, и разъехаться они могли молча — на маке это
    вылезло бы только у клиента, которого никто не проверяет.
    """
    return [str(py), str(script), "--run"]


def _ansi_encoding() -> str:
    """Настоящая ANSI-кодовая страница Windows (обычно cp1251, иногда cp65001).

    Не `locale.getpreferredencoding()`: при PYTHONUTF8=1 он врёт «utf-8» независимо
    от системы — поймано на прогоне, файл для мастера уехал в utf-8 и Inno показал
    бы клиенту кракозябры вместо строки про расписание.
    """
    if is_windows():
        try:
            import ctypes
            return "cp%d" % ctypes.windll.kernel32.GetACP()   # noqa: attr — Windows
        except Exception:                                     # noqa: BLE001
            return "cp1251"
    return "utf-8"


def _decode_console(raw: bytes) -> str:
    """Расшифровать вывод консольной утилиты Windows.

    Однозначного ответа тут нет: `schtasks /Query /XML` отдаёт UTF-16, а обычные
    сообщения об ошибках — в OEM-кодировке консоли (cp866 на русской Windows).
    Гадаем по BOM и нулевым байтам, дальше — по кодовым страницам системы.
    """
    if not raw:
        return ""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or raw.count(b"\x00") > len(raw) // 4:
        try:
            return raw.decode("utf-16")
        except (UnicodeDecodeError, ValueError):
            pass
    cands = []
    if is_windows():
        try:
            import ctypes
            cands.append("cp%d" % ctypes.windll.kernel32.GetOEMCP())  # noqa: attr
        except Exception:                                             # noqa: BLE001
            cands.append("cp866")
        cands.append(_ansi_encoding())
    cands.append("utf-8")
    for enc in cands:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def _run(cmd: list[str]) -> tuple[int, str]:
    """Запустить команду ОС, вернуть (код, слепленный вывод). Никогда не бросает."""
    try:
        r = subprocess.run(cmd, capture_output=True, check=False)
    except OSError as e:
        return 1, str(e)
    out = _decode_console(r.stdout or b"") + _decode_console(r.stderr or b"")
    return r.returncode, out.strip()


# ══════════════════════════════════════════════════════════════════════════════
#  WINDOWS — Планировщик заданий
# ══════════════════════════════════════════════════════════════════════════════

def _xml_escape(s: str) -> str:
    """Путь установки может содержать & (например, папка «Иванов & Ко») — без
    экранирования такой XML Планировщик отвергнет целиком."""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _task_xml(py: Path, script: Path, root: Path, interval_min: int) -> str:
    """XML задания. Через /XML, а не через набор ключей schtasks, потому что
    ключами НЕ выставить три вещи, без которых у клиента-ноутбучника задание
    молча не сработает ни разу:
      * DisallowStartIfOnBatteries / StopIfGoingOnBatteries — по умолчанию в
        схеме Планировщика они ВКЛЮЧЕНЫ, то есть на батарее задание не стартует
        и глохнет при отключении от розетки;
      * StartWhenAvailable — догнать пропущенный круг после включения машины;
      * MultipleInstancesPolicy=IgnoreNew — если публикация большого ролика
        затянулась дольше 5 минут, второй экземпляр не полезет отправлять то же
        самое ещё раз.
    Ещё здесь ExecutionTimeLimit=PT1H: зависшая заливка не должна висеть вечно.
    """
    # Дата начала — заведомо в прошлом, чтобы задание было активно с первого дня
    # и не ждало полуночи. Часовой пояс не указываем: локальное время машины.
    start = "2020-01-01T00:00:00"
    # Порядок элементов ЗНАЧИМ: схема Планировщика местами последовательная, и
    # перестановка ловится невнятным «неправильно отформатированное значение».
    # Раскладка ниже 1-в-1 повторяет то, что Планировщик сам отдаёт на «Экспорт».
    # UserId в Principal НЕ указываем сознательно: schtasks подставит текущего
    # пользователя сам, а вписанное «ДОМЕН\\Имя» ломается на учётках Microsoft.
    # И никакого /RU: без /RP он ушёл бы спрашивать пароль в невидимом окне и
    # подвесил установку насмерть.
    esc = _xml_escape
    # Команда — из общего run_argv(), а не собрана здесь заново: см. её докстроку.
    argv = run_argv(py, script)
    # Кавычки вокруг пути к скрипту — часть КОМАНДНОЙ строки, а не текст XML:
    # экранируем то, что внутри них, а сами кавычки оставляем литеральными,
    # ровно как их пишет сам Планировщик на «Экспорт».
    exe = _xml_escape(argv[0])
    args = '"%s" %s' % (_xml_escape(argv[1]), " ".join(argv[2:]))
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{esc(TASK_DESC)}</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <Repetition>
        <Interval>PT{interval_min}M</Interval>
        <Duration>P1D</Duration>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>{start}</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{exe}</Command>
      <Arguments>{args}</Arguments>
      <WorkingDirectory>{esc(str(root))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def win_install(root: Path, interval_min: int, dry: bool) -> tuple[int, list[str]]:
    say: list[str] = []
    script = queue_script(root)
    if script is None:
        return 2, [f"В этой коробке нет {QUEUE_SCRIPT} — дёргать нечего, "
                   f"расписание не завожу (это не поломка установки)."]
    py, quiet = interpreter(root)
    if py is None:
        return 2, ["Не нашёл питон внутри установки (.venv\\Scripts\\python.exe) — "
                   "расписание заведу после того, как окружение развернётся."]
    if not quiet:
        say.append("⚠ pythonw.exe в окружении нет — беру python.exe. Раз в "
                   "несколько минут будет мелькать чёрное окошко консоли.")

    xml = _task_xml(py, script, root, interval_min)
    if dry:
        say.append(f"Завёл бы задание «{TASK_NAME}»: каждые {interval_min} мин "
                   f"{py} \"{script}\" --run")
        return 0, say

    # ⚠️ schtasks /XML читает ФАЙЛ ТОЛЬКО В UTF-16. UTF-8 он разбирает как мусор
    #    и падает «неправильно отформатированное значение» — на русском описании
    #    это ловится сразу.
    tmp = Path(os.environ.get("TEMP") or root) / "kz_schedule_task.xml"
    try:
        tmp.write_text(xml, encoding="utf-16")
        code, out = _run(["schtasks", "/Create", "/TN", TASK_NAME,
                          "/XML", str(tmp), "/F"])
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass

    if code != 0:
        say.append("Не смог завести задание Планировщика. Ответ Windows: " +
                   (out or "(пусто)"))
        return 1, say
    say.append(f"Расписание выпуска заведено: «{TASK_NAME}», "
               f"проверка очереди каждые {interval_min} мин.")
    return 0, say


def win_remove(dry: bool) -> tuple[int, list[str]]:
    if dry:
        return 0, [f"Снял бы задание «{TASK_NAME}»."]
    code, out = _run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
    if code != 0:
        # Задания и так нет — это норма при повторном удалении.
        return 2, [f"Задание «{TASK_NAME}» снять не удалось (возможно, его уже "
                   f"нет). Ответ Windows: {out or '(пусто)'}"]
    return 0, [f"Задание «{TASK_NAME}» снято."]


def win_status(root: Path) -> dict:
    st: dict = {"платформа": "windows", "имя": TASK_NAME, "заведено": False,
                "включено": False, "интервал_мин": None, "последний_запуск": None,
                "результат_последнего": None, "следующий_запуск": None,
                "тихий_запуск": None, "замечания": []}
    code, out = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/XML"])
    if code != 0:
        st["замечания"].append("Задания в Планировщике нет.")
        return st
    st["заведено"] = True
    # Разбираем свой же XML без сторонних библиотек: нам нужны три поля.
    low = out
    st["включено"] = "<Enabled>true</Enabled>" in low
    for token in low.split("<Interval>")[1:]:
        st["интервал_мин"] = token.split("</Interval>")[0].strip()
        break
    st["тихий_запуск"] = "pythonw.exe" in low
    if not st["тихий_запуск"]:
        st["замечания"].append("Запуск не тихий: будет мелькать окно консоли.")

    # Время/результат последнего прогона. CSV /V — порядок столбцов одинаков на
    # любом языке Windows, а вот ЗАГОЛОВКИ переведены, поэтому берём по номеру.
    code2, csv_out = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "CSV", "/V"])
    if code2 == 0:
        rows = [r for r in csv_out.splitlines() if r.strip()]
        if len(rows) >= 2:
            import csv as _csv
            try:
                cells = next(_csv.reader([rows[1]]))
                if len(cells) > 6:
                    st["следующий_запуск"] = cells[2]
                    st["последний_запуск"] = cells[5]
                    st["результат_последнего"] = cells[6]
            except (StopIteration, _csv.Error):
                pass
    return st


# ══════════════════════════════════════════════════════════════════════════════
#  macOS — агент запуска launchd
# ══════════════════════════════════════════════════════════════════════════════
#  ⚠️ Почему НЕ `launchctl submit`, как в sync_setup.py:98. Там этим способом
#  поднимают демон обмена — вечно живущий процесс на машине владельца, который
#  не жалко потерять при перезагрузке. Нам нужно другое: коротко просыпаться раз
#  в 5 минут и переживать перезагрузку. `submit` не умеет ни интервала, ни
#  автозагрузки — только plist-агент в ~/Library/LaunchAgents. Копировать оттуда
#  подход было бы ошибкой.
#  Агент пользователя (LaunchAgent), а не системный демон (LaunchDaemon), по той
#  же причине, что и InteractiveToken на Windows: публикации нужны профиль
#  клиента, его Google Диск и его доступы к площадкам. LaunchDaemon вдобавок
#  потребовал бы прав администратора.

def mac_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{MAC_LABEL}.plist"


def mac_plist(root: Path, py: Path, script: Path, interval_min: int) -> dict:
    """Содержимое агента запуска. Отдельной функцией — чтобы его можно было
    проверить тестом, не трогая launchctl и не завися от того, что мы сейчас на
    Windows: launchctl тут не вызывается, и файл никуда не пишется."""
    logs = root / ".state"
    return {
        "Label": MAC_LABEL,
        # Та же команда, что и у задания Windows — см. run_argv().
        "ProgramArguments": run_argv(py, script),
        "StartInterval": interval_min * 60,
        "WorkingDirectory": str(root),
        # RunAtLoad=false: при входе в систему не дёргаем сразу — первый круг
        # придёт через интервал. Иначе каждый логин = лишняя проверка очереди.
        "RunAtLoad": False,
        # Окна на маке не будет и без pythonw: launchd запускает задачу без
        # терминала. Вывод уводим в файл журнала — иначе он ушёл бы в
        # системный лог, где клиент его не найдёт.
        "StandardOutPath": str(logs / "publish_queue.launchd.log"),
        "StandardErrorPath": str(logs / "publish_queue.launchd.log"),
        "ProcessType": "Background",
        # Явное «не выключено». Ключ пишем ВСЕГДА: без него агент подчиняется
        # базе отключённых служб пользователя, а туда его кладёт наше же
        # `launchctl unload -w` при --remove (см. mac_remove). Установка после
        # удаления молча поднимала бы отключённый агент.
        "Disabled": False,
    }


def mac_install(root: Path, interval_min: int, dry: bool) -> tuple[int, list[str]]:
    say: list[str] = []
    script = queue_script(root)
    if script is None:
        return 2, [f"В этой коробке нет {QUEUE_SCRIPT} — дёргать нечего, "
                   f"расписание не завожу (это не поломка установки)."]
    py, _ = interpreter(root)
    if py is None:
        return 2, ["Не нашёл питон внутри установки (.venv/bin/python3) — "
                   "расписание заведу после того, как окружение развернётся."]

    plist = mac_plist(root, py, script, interval_min)
    target = mac_plist_path()
    if dry:
        say.append(f"Завёл бы агент {target}: каждые {interval_min} мин "
                   + " ".join(run_argv(py, script)))
        return 0, say

    (root / ".state").mkdir(parents=True, exist_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "wb") as f:
        plistlib.dump(plist, f)

    uid = os.getuid()                                   # noqa: attr — только POSIX
    # Сначала выгружаем старую версию, иначе bootstrap ругнётся «уже загружен».
    _run(["launchctl", "bootout", f"gui/{uid}/{MAC_LABEL}"])
    # И снимаем возможную отметку «отключено», оставленную прошлым --remove:
    # bootstrap отключённую службу загрузит, но запускать по таймеру не станет —
    # и расписание молча не сработает НИ РАЗУ. Ошибку игнорируем: если отметки
    # нет, launchctl отвечает ненулевым кодом, и это норма.
    _run(["launchctl", "enable", f"gui/{uid}/{MAC_LABEL}"])
    code, out = _run(["launchctl", "bootstrap", f"gui/{uid}", str(target)])
    if code != 0:
        # macOS до Catalina не знает bootstrap — там работает старый load -w.
        code, out = _run(["launchctl", "load", "-w", str(target)])
    if code != 0:
        say.append(f"Файл агента записан ({target}), но launchctl его не принял: "
                   f"{out or '(пусто)'}. Расписание заработает после перезагрузки "
                   f"или команды: launchctl load -w {target}")
        return 2, say
    say.append(f"Расписание выпуска заведено (агент запуска macOS), "
               f"проверка очереди каждые {interval_min} мин.")
    # Перезагрузку агент переживает не «сам по себе», а потому что лежит в
    # ~/Library/LaunchAgents: launchd загружает всё из этой папки при КАЖДОМ
    # входе пользователя в систему. bootstrap выше нужен только чтобы агент
    # заработал прямо сейчас, не дожидаясь перелогина.
    say.append("После перезагрузки расписание поднимется само — агент лежит в "
               "папке автозапуска пользователя.")
    return 0, say


def mac_remove(dry: bool) -> tuple[int, list[str]]:
    target = mac_plist_path()
    if dry:
        return 0, [f"Снял бы агент {target}."]
    uid = os.getuid()                                   # noqa: attr — только POSIX
    if not target.exists():
        # Файла нет, но служба могла остаться загруженной от прошлой установки
        # (папку удалили руками мимо --remove) — тогда launchd каждые 5 минут
        # дёргает несуществующий путь. Выгружаем на всякий случай и молчим.
        _run(["launchctl", "bootout", f"gui/{uid}/{MAC_LABEL}"])
        return 2, ["Агента запуска нет — снимать нечего."]
    _run(["launchctl", "bootout", f"gui/{uid}/{MAC_LABEL}"])
    # `-w` дописывает агент в базу отключённых служб пользователя. Это здесь
    # нужно: пока plist не удалён, launchd не поднимет его при следующем входе.
    # Обратную отметку снимает mac_install (`launchctl enable`) — без неё
    # повторная установка получила бы загруженный, но никогда не срабатывающий
    # агент.
    _run(["launchctl", "unload", "-w", str(target)])
    try:
        target.unlink()
    except OSError as e:
        return 1, [f"Не смог удалить {target}: {e}"]
    return 0, ["Расписание выпуска снято — одобренное больше не выходит в срок."]


def mac_status(root: Path) -> dict:
    st: dict = {"платформа": "macos", "имя": MAC_LABEL, "заведено": False,
                "включено": False, "интервал_мин": None, "последний_запуск": None,
                "результат_последнего": None, "следующий_запуск": None,
                "тихий_запуск": True, "замечания": []}
    target = mac_plist_path()
    if not target.exists():
        st["замечания"].append("Агента запуска нет.")
        return st
    st["заведено"] = True
    try:
        with open(target, "rb") as f:
            data = plistlib.load(f)
        sec = int(data.get("StartInterval") or 0)
        st["интервал_мин"] = f"PT{sec // 60}M" if sec else None
        argv = data.get("ProgramArguments") or []
        st["чем_запускается"] = argv[0] if argv else None
        if data.get("Disabled"):
            st["замечания"].append("Агент помечен отключённым — по таймеру он "
                                   "не сработает. Завести заново: --install.")
    except (OSError, ValueError, plistlib.InvalidFileException):
        st["замечания"].append("Файл агента не разбирается.")
    uid = os.getuid()                                   # noqa: attr — только POSIX
    code, out = _run(["launchctl", "print", f"gui/{uid}/{MAC_LABEL}"])
    st["включено"] = code == 0
    if code == 0:
        for line in out.splitlines():
            s = line.strip()
            if s.startswith("last exit code"):
                st["результат_последнего"] = s.split("=")[-1].strip()
    else:
        st["замечания"].append("Агент записан, но не загружен в систему. "
                               "Заработает после перезагрузки.")
    return st


# ══════════════════════════════════════════════════════════════════════════════
#  Общая часть
# ══════════════════════════════════════════════════════════════════════════════

def status(root: Path) -> dict:
    if is_windows():
        st = win_status(root)
    elif is_macos():
        st = mac_status(root)
    else:
        st = {"платформа": sys.platform, "заведено": False, "включено": False,
              "замечания": ["Эта ОС официально не поддерживается — расписание "
                            "заводится только на Windows и macOS."]}
    st["корень"] = str(root)
    st["очередь_на_месте"] = queue_script(root) is not None
    if not st["очередь_на_месте"]:
        st.setdefault("замечания", []).append(
            f"В установке нет {QUEUE_SCRIPT} — выпускать по расписанию нечем.")
    py, quiet = interpreter(root)
    st["питон"] = str(py) if py else None
    if py is None:
        st.setdefault("замечания", []).append(
            "Питон внутри установки не найден — окружение не развёрнуто.")
    return st


def brief(st: dict) -> str:
    """Одна строка для клиента: живо расписание или нет. Без терминов.

    Формулировки здесь и в install/box_root/CLAUDE.md обязаны совпадать по
    смыслу дословно: расписание выпускает ОДОБРЕННОЕ, а не «материалы вообще».
    Клиент читает эту строку на финальной странице мастера установки.
    """
    if st.get("заведено") and st.get("включено"):
        когда = st.get("интервал_мин") or ""
        каждые = ""
        if когда.startswith("PT") and когда.endswith("M"):
            каждые = f" (проверка каждые {когда[2:-1]} мин)"
        строка = ("Расписание выпуска работает" + каждые +
                  ": выходит только то, что вы одобрили.")
        рез = st.get("результат_последнего")
        посл = st.get("последний_запуск")
        if посл and посл.strip() and "N/A" not in посл and "Никогда" not in посл:
            строка += f" Последняя проверка: {посл}"
            if рез not in (None, "", "0"):
                строка += f", код {рез}"
            строка += "."
        return строка
    if st.get("заведено"):
        return "Расписание выпуска заведено, но сейчас выключено — даже " \
               "одобренное в срок не выйдет."
    if not st.get("очередь_на_месте"):
        return "Расписание выпуска не заведено: в этой сборке нет очереди " \
               "публикаций. Выпуск — только по вашей команде."
    return "Расписание выпуска НЕ заведено — даже одобренное в срок не выйдет."


def human(st: dict) -> str:
    lines = ["", "  РАСПИСАНИЕ ВЫПУСКА", ""]
    lines.append("   " + brief(st))
    lines.append("")
    if st.get("имя"):
        lines.append(f"   • как называется   {st['имя']}")
    lines.append(f"   • корень завода    {st.get('корень')}")
    lines.append(f"   • чем запускается  {st.get('питон') or '— не найден —'}")
    if st.get("следующий_запуск"):
        lines.append(f"   • следующий круг   {st['следующий_запуск']}")
    for note in st.get("замечания") or []:
        lines.append(f"   ⚠ {note}")
    lines.append("")
    lines.append("   Расписание НЕ публикует ничего само по себе: оно отправляет")
    lines.append("   только записи очереди, которые вы одобрили. Неодобренное")
    lines.append("   ждёт вашего «да» и по времени не выходит.")
    lines.append("")
    lines.append("   Проверить это в любой момент:")
    lines.append("     python installer/schedule_publisher.py --status")
    lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Расписание выпуска: завести / снять / проверить.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--install", action="store_true", help="завести расписание")
    g.add_argument("--remove", action="store_true", help="снять расписание")
    g.add_argument("--status", action="store_true",
                   help="показать, живо ли расписание (по умолчанию)")
    ap.add_argument("--root", help="корень установки (по умолчанию — авто-поиск)")
    ap.add_argument("--interval-min", type=int, default=DEFAULT_INTERVAL_MIN,
                    help=f"как часто проверять очередь, мин (по умолчанию "
                         f"{DEFAULT_INTERVAL_MIN})")
    ap.add_argument("--json", action="store_true", help="машинная сводка")
    ap.add_argument("--brief", action="store_true", help="одна строка для клиента")
    ap.add_argument("--brief-out", metavar="PATH",
                    help="записать эту одну строку в файл (его читает установщик; "
                         "кодировка — системная, иначе в мастере будут кракозябры)")
    ap.add_argument("--dry-run", action="store_true", help="показать, не делая")
    a = ap.parse_args(argv)

    root = find_root(a.root)
    if a.interval_min < 1 or a.interval_min > 1440:
        print("--interval-min должен быть от 1 до 1440 минут.")
        return 1

    code = 0
    said: list[str] = []
    if a.install:
        if is_windows():
            code, said = win_install(root, a.interval_min, a.dry_run)
        elif is_macos():
            code, said = mac_install(root, a.interval_min, a.dry_run)
        else:
            code, said = 2, ["Эта ОС не поддерживается — расписание заводится "
                             "только на Windows и macOS."]
    elif a.remove:
        if is_windows():
            code, said = win_remove(a.dry_run)
        elif is_macos():
            code, said = mac_remove(a.dry_run)
        else:
            code, said = 2, ["Эта ОС не поддерживается — снимать нечего."]

    st = status(root)
    st["сообщения"] = said
    if not (a.install or a.remove):
        code = 0 if (st.get("заведено") and st.get("включено")) else 2

    line = brief(st)
    if a.brief_out:
        # Мастер установки читает этот файл однобайтовой строкой и переводит из
        # СИСТЕМНОЙ кодировки. Отдай ему utf-8 — клиент увидит кракозябры.
        try:
            Path(a.brief_out).parent.mkdir(parents=True, exist_ok=True)
            Path(a.brief_out).write_text(line, encoding=_ansi_encoding(),
                                         errors="replace")
        except OSError:
            pass

    if a.json:
        print(json.dumps(st, ensure_ascii=False, indent=2))
    elif a.brief:
        print(line)
    else:
        for s in said:
            print(s)
        print(human(st))
    return code


if __name__ == "__main__":
    sys.exit(main())

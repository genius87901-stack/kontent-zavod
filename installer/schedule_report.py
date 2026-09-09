# -*- coding: utf-8 -*-
"""
schedule_report — «будилка» СУТОЧНОГО ОТЧЁТА на машине клиента.

ЗАЧЕМ. Клиент, заведя Telegram-архив, отвечает в мастере на вопрос «присылать
отчёт за день?» (`daily_report_enabled` в модуле publishing). Ответ «да» сам по
себе ничего не запускает: отчёт шлёт `daily_report.py --send`, и кто-то должен
звать его раз в сутки. Этот файл заводит и снимает такое задание.

ПОЧЕМУ ОТДЕЛЬНЫМ ЗАДАНИЕМ, А НЕ ВНУТРИ ВЫПУСКА. Расписание выпуска
(schedule_publisher) дёргает очередь каждые 5 минут — отчёту такая частота не
нужна и вредна: 288 сообщений в сутки вместо одного. Разные ритмы — разные
задания; к тому же отчёт можно выключить, не трогая выпуск.

МЕХАНИКУ НЕ ДУБЛИРУЕМ. Всё, что уже выяснено про задания Планировщика —
запуск не от батареи, догон пропущенного, тихий pythonw, поиск интерпретатора
коробки — живёт в schedule_publisher.py. Здесь мы берём оттуда готовые куски
(`interpreter`, `_run`, `find_root`, `_xml_escape`), а своего только триггер:
раз в сутки в назначенный час вместо повтора каждые N минут.

ЗАПУСК:
    python installer/schedule_report.py --install            # в 09:00
    python installer/schedule_report.py --install --time 12:00
    python installer/schedule_report.py --status
    python installer/schedule_report.py --remove
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _publisher():
    """schedule_publisher.py как модуль: он лежит рядом, но installer/ — не пакет."""
    cand = HERE / "schedule_publisher.py"
    if not cand.is_file():
        raise SystemExit("Рядом нет schedule_publisher.py — без него не найти "
                         "ни корень установки, ни интерпретатор коробки.")
    spec = importlib.util.spec_from_file_location("kz_schedule_publisher", cand)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SP = _publisher()

# Имя видит нетехнический клиент в списке заданий Windows — только по-русски.
TASK_NAME = "Контент-завод — отчёт за день"
TASK_DESC = ("Раз в сутки присылает в ваш Telegram-архив короткий отчёт: "
             "сколько контента вышло за прошедший день и на каких площадках, "
             "что ещё стоит в очереди и не заканчивается ли контент-план. "
             "Если выключить — отчёты перестанут приходить, публикация "
             "продолжит работать.")

MAC_LABEL = "ru.kontentzavod.report"
REPORT_SCRIPT = "daily_report.py"
DEFAULT_TIME = "09:00"


def report_script(root: Path):
    p = root / REPORT_SCRIPT
    return p if p.is_file() else None


def parse_time(raw: str) -> tuple[int, int]:
    """«09:00» → (9, 0). Плохое значение — понятная ошибка, а не тихий дефолт:
    молча сдвинуть время отчёта хуже, чем отказаться его заводить."""
    try:
        hh, _, mm = str(raw).strip().partition(":")
        h, m = int(hh), int(mm or 0)
    except ValueError:
        raise SystemExit(f"Не понял время «{raw}». Формат — ЧЧ:ММ, например 09:00.")
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise SystemExit(f"Время «{raw}» вне суток. Формат — ЧЧ:ММ, например 09:00.")
    return h, m


def _task_xml(py: Path, script: Path, root: Path, hh: int, mm: int) -> str:
    """XML задания: ежедневный триггер в назначенный час.

    Отличие от выпуска — нет <Repetition>: срабатывает один раз в сутки.
    Остальные настройки те же и по той же причине (см. schedule_publisher):
    работать от батареи, догнать пропущенное после включения машины, не
    плодить второй экземпляр.
    """
    esc = SP._xml_escape
    start = f"2020-01-01T{hh:02d}:{mm:02d}:00"
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{esc(TASK_DESC)}</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
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
    <ExecutionTimeLimit>PT30M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{esc(str(py))}</Command>
      <Arguments>"{esc(str(script))}" --send</Arguments>
      <WorkingDirectory>{esc(str(root))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def win_install(root: Path, hh: int, mm: int, dry: bool, py_override=None):
    say: list[str] = []
    script = report_script(root)
    if script is None:
        return 2, [f"В этой коробке нет {REPORT_SCRIPT} — отчёт слать нечем "
                   f"(это не поломка установки)."]
    if py_override:
        py, quiet = Path(py_override), Path(py_override).name.lower() == "pythonw.exe"
    else:
        py, quiet = SP.interpreter(root)
    if py is None:
        return 2, ["Не нашёл питон внутри установки — отчёт заведу после того, "
                   "как окружение развернётся."]
    if not quiet:
        say.append("⚠ pythonw.exe в окружении нет — беру python.exe. Раз в сутки "
                   "мелькнёт чёрное окошко консоли.")

    if dry:
        say.append(f"Завёл бы задание «{TASK_NAME}»: ежедневно в "
                   f"{hh:02d}:{mm:02d} {py} \"{script}\" --send")
        return 0, say

    # schtasks /XML читает файл ТОЛЬКО в UTF-16 (см. schedule_publisher).
    tmp = Path(os.environ.get("TEMP") or root) / "kz_report_task.xml"
    try:
        tmp.write_text(_task_xml(py, script, root, hh, mm), encoding="utf-16")
        code, out = SP._run(["schtasks", "/Create", "/TN", TASK_NAME,
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
    say.append(f"Отчёт за день заведён: «{TASK_NAME}», ежедневно в "
               f"{hh:02d}:{mm:02d}.")
    return 0, say


def win_remove(dry: bool):
    if dry:
        return 0, [f"Снял бы задание «{TASK_NAME}»."]
    code, out = SP._run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
    if code != 0:
        return 2, [f"Задание «{TASK_NAME}» снять не удалось (возможно, его уже "
                   f"нет). Ответ Windows: {out or '(пусто)'}"]
    return 0, [f"Задание «{TASK_NAME}» снято."]


def win_status():
    st = {"платформа": "windows", "имя": TASK_NAME, "заведено": False,
          "включено": False}
    code, out = SP._run(["schtasks", "/Query", "/TN", TASK_NAME, "/XML"])
    if code == 0:
        st["заведено"] = True
        st["включено"] = "<Enabled>true</Enabled>" in (out or "")
    return st


def mac_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{MAC_LABEL}.plist"


def mac_install(root: Path, hh: int, mm: int, dry: bool, py_override=None):
    import plistlib
    script = report_script(root)
    if script is None:
        return 2, [f"В этой коробке нет {REPORT_SCRIPT} — отчёт слать нечем."]
    py = Path(py_override) if py_override else SP.interpreter(root)[0]
    if py is None:
        return 2, ["Не нашёл питон внутри установки — отчёт заведу после того, "
                   "как окружение развернётся."]
    logs = root / ".state"
    target = mac_plist_path()
    if dry:
        return 0, [f"Завёл бы агент {target}: ежедневно в {hh:02d}:{mm:02d}."]

    plist = {
        "Label": MAC_LABEL,
        "ProgramArguments": [str(py), str(script), "--send"],
        # Ежедневно в назначенный час — аналог CalendarTrigger у Windows.
        "StartCalendarInterval": {"Hour": hh, "Minute": mm},
        "WorkingDirectory": str(root),
        "RunAtLoad": False,
        "StandardOutPath": str(logs / "daily_report.launchd.log"),
        "StandardErrorPath": str(logs / "daily_report.launchd.log"),
        "ProcessType": "Background",
    }
    logs.mkdir(parents=True, exist_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "wb") as f:
        plistlib.dump(plist, f)

    uid = os.getuid()                                   # noqa: attr — только POSIX
    SP._run(["launchctl", "bootout", f"gui/{uid}/{MAC_LABEL}"])
    code, out = SP._run(["launchctl", "bootstrap", f"gui/{uid}", str(target)])
    if code != 0:
        code, out = SP._run(["launchctl", "load", "-w", str(target)])
    if code != 0:
        return 2, [f"Файл агента записан ({target}), но launchctl его не принял: "
                   f"{out or '(пусто)'}. Заработает после перезагрузки."]
    return 0, [f"Отчёт за день заведён (агент запуска macOS), ежедневно в "
               f"{hh:02d}:{mm:02d}."]


def mac_remove(dry: bool):
    target = mac_plist_path()
    if dry:
        return 0, [f"Снял бы агент {target}."]
    if not target.exists():
        return 2, ["Агента отчёта нет — снимать нечего."]
    uid = os.getuid()                                   # noqa: attr — только POSIX
    SP._run(["launchctl", "bootout", f"gui/{uid}/{MAC_LABEL}"])
    SP._run(["launchctl", "unload", "-w", str(target)])
    try:
        target.unlink()
    except OSError as e:
        return 1, [f"Не смог удалить {target}: {e}"]
    return 0, ["Отчёт за день снят."]


def mac_status():
    target = mac_plist_path()
    return {"платформа": "macos", "имя": MAC_LABEL,
            "заведено": target.exists(), "включено": target.exists()}


def _answer(root: Path, key: str) -> str:
    """Ответ клиента по ключу. Сначала канонический ридер публикации, а если он
    почему-то не поднялся — ЧИТАЕМ ФАЙЛ САМИ.

    Запасной путь здесь не украшение. `modules/publishing` может не
    импортироваться по куче причин (обрезанная копия, чужой sys.path, битый
    __init__), и «не смог прочитать» молча превращалось бы в «клиент ответил
    нет»: человек в мастере сказал «да, присылайте отчёт», задание не завелось,
    и никто об этом не узнал — отчёта ведь и так не было. Поэтому ответ
    достаётся хотя бы из config/factory.config.json, где мастер его и оставил.
    """
    try:
        sys.path.insert(0, str(root / "modules"))
        from publishing import config_reader
        val = config_reader.value(key, start=str(root))
        if val:
            return str(val).strip()
    except Exception:                                   # noqa: BLE001
        pass
    for name in ("config/factory.config.json", ".env"):
        p = root / name
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        if name.endswith(".json"):
            try:
                data = json.loads(text)
            except ValueError:
                continue
            for k, v in (data or {}).items():
                if str(k).strip().lower() == key.lower() and str(v).strip():
                    return str(v).strip()
        else:
            for line in text.splitlines():
                k, _, v = line.partition("=")
                if k.strip().lower() == key.lower() and v.strip():
                    return v.strip().strip('"').strip("'")
    return ""


def remember(root: Path, enabled: bool, when: str = "") -> None:
    """Записать ответ клиента в config/factory.config.json.

    Нужно для `--enable`/`--disable`: клиент, установленный ДО патча 1.3.25,
    вопроса в мастере не видел, и гонять его через весь этап установки ради
    одного «да» — плохой обмен. Пишем ровно те же ключи, что записал бы мастер,
    поэтому повторная установка ответ не потеряет и не задвоит.
    Остальные ответы в файле не трогаем: читаем, правим два ключа, кладём назад.
    """
    p = root / "config" / "factory.config.json"
    data = {}
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig")) or {}
        except ValueError:
            data = {}
    data["daily_report_enabled"] = "да" if enabled else "нет"
    if when:
        data["daily_report_time"] = when
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def wanted(root: Path) -> bool:
    """Ответил ли клиент «да» в мастере."""
    return _answer(root, "daily_report_enabled").lower() in (
        "да", "yes", "true", "1", "on")


def configured_time(root: Path) -> str:
    return _answer(root, "daily_report_time") or DEFAULT_TIME


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Отчёт за день: завести / снять / проверить.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--install", action="store_true", help="завести отчёт")
    g.add_argument("--remove", action="store_true", help="снять отчёт")
    g.add_argument("--status", action="store_true", help="показать состояние")
    g.add_argument("--enable", action="store_true",
                   help="включить отчёт: запомнить ответ «да» и завести задание. "
                        "Для тех, кого мастер об этом не спрашивал.")
    g.add_argument("--disable", action="store_true",
                   help="выключить отчёт: запомнить «нет» и снять задание")
    ap.add_argument("--root", help="корень установки (по умолчанию — авто-поиск)")
    ap.add_argument("--time", help=f"во сколько присылать, ЧЧ:ММ "
                                   f"(по умолчанию из настроек, иначе {DEFAULT_TIME})")
    ap.add_argument("--python", help="каким питоном звать отчёт. Нужен только там, "
                                     "где нет .venv внутри установки (машина "
                                     "разработки). У клиента НЕ используется: "
                                     "там интерпретатор берётся из коробки.")
    ap.add_argument("--from-answer", action="store_true",
                    help="действовать по ответу клиента: «да» — завести, "
                         "иначе снять. Так зовёт установщик.")
    ap.add_argument("--json", action="store_true", help="машинная сводка")
    ap.add_argument("--dry-run", action="store_true", help="показать, не делая")
    a = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                                   # noqa: BLE001
        pass

    root = SP.find_root(a.root)
    install, remove = a.install, a.remove
    if a.from_answer:
        install = wanted(root)
        remove = not install
    if a.enable or a.disable:
        # Сначала ЗАПОМИНАЕМ ответ, потом действуем: иначе повторная установка
        # (или --from-answer из установщика) молча вернула бы старое состояние.
        if not a.dry_run:
            remember(root, a.enable, a.time or "")
        install, remove = a.enable, a.disable

    hh, mm = parse_time(a.time or configured_time(root))

    code, said = 0, []
    if install:
        if SP.is_windows():
            code, said = win_install(root, hh, mm, a.dry_run, a.python)
        elif SP.is_macos():
            code, said = mac_install(root, hh, mm, a.dry_run, a.python)
        else:
            code, said = 2, ["Эта ОС не поддерживается — отчёт заводится только "
                             "на Windows и macOS."]
    elif remove:
        if SP.is_windows():
            code, said = win_remove(a.dry_run)
        elif SP.is_macos():
            code, said = mac_remove(a.dry_run)
        else:
            code, said = 2, ["Эта ОС не поддерживается — снимать нечего."]

    st = win_status() if SP.is_windows() else (
        mac_status() if SP.is_macos() else {"платформа": "неизвестна"})
    st["сообщения"] = said
    if not (install or remove):
        code = 0 if st.get("заведено") and st.get("включено") else 2

    if a.json:
        print(json.dumps(st, ensure_ascii=False, indent=2))
    else:
        for line in said:
            print(line)
        if not said:
            print("Отчёт за день: %s." %
                  ("заведён" if st.get("заведено") else "не заведён"))
    return code


if __name__ == "__main__":
    sys.exit(main())

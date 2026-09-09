# -*- coding: utf-8 -*-
"""
make_venv — локальный .venv и установка зависимостей одним заходом (шаг 3).

Создаёт .venv В КОРНЕ установки (система клиента не трогается; удаление папки
удаляет всё) и ставит объединённый список pip из плана манифестов ОДНИМ
pip install. Все версии строго с '==' (проверяется). Пишет requirements.lock.txt
для воспроизводимости.

ВЕРСИЯ ПИТОНА ПРОВЕРЯЕТСЯ ЗДЕСЬ ЖЕ, А НЕ ТОЛЬКО В check_env.py. `venv.EnvBuilder`
строит окружение вокруг ТОГО интерпретатора, которым запущен этот скрипт, — то есть
запуск «просто python3» тихо давал .venv на негодной версии, а клиент видел не
понятную фразу, а трассировку pip («нет mediapipe==0.10.14 под 3.14»). Поэтому:

    * окно версий берётся из install/check_env.py (там же, где и у мастера:
      TARGET_PYTHON + required_python_range по манифестам) — своей копии правил тут нет;
    * запустили негодным питоном → окружение НЕ создаётся; печатается, какой питон
      запущен, какой нужен и ГОТОВАЯ строка перезапуска подходящим (полным путём);
    * `--python PATH` — назвать интерпретатор явно (мастер передаёт сюда
      python.recommended.path из check_env.py --json);
    * уже собранное .venv на негодном питоне распознаётся и не используется молча:
      нужен `--recreate` (папку завода без спроса не удаляем).

Вход:
    --plan plan.json   вывод collect_manifests.py --json (берём plan.pip)
    --root PATH        корень установки (по умолчанию авто)
    --python PATH      каким интерпретатором строить .venv
    --recreate         пересобрать .venv, собранный на неподходящем питоне
    --dry-run          только показать, что будет установлено

Печатает по-русски. Код возврата 0 при успехе, 1 при ошибке установки,
2 при незапиненной версии в плане или негодном питоне. Только stdlib.
Кроссплатформенно.
"""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# Консоль русской Windows — cp1251, и ЛЮБОЙ значок вне неё (→ ✓ ✗ • ⚠) роняет
# скрипт UnicodeEncodeError прямо в лицо клиенту. Переводим свой вывод в utf-8
# с заменой непечатаемого: ни один текст мастера больше не может убить установку.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:      # старый питон или подменённый stdout — не критично
    pass


# ⚠️ `venv` импортируется ЛОКАЛЬНО (единственное место — сборка нового окружения
# ниже), а не здесь. В коробке этот скрипт запускается вложенным интерпретатором
# (`.venv\Scripts\python.exe` — копия embeddable-Python), а в нём модулей
# `venv`/`ensurepip` НЕТ: импорт наверху ронял бы весь Шаг 4 мастера трассировкой
# ещё до первой проверки. Замер: ModuleNotFoundError на python.exe из коробки 1.0.4.


def find_root(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p
    here = Path(__file__).resolve().parent
    for cand in (here, *here.parents):
        for marker in (".factory_root", "modules", "CONTRACT.md"):
            if (cand / marker).exists():
                return cand
    return here.parent if here.name == "install" else Path.cwd()


def venv_python(root: Path) -> Path:
    win = root / ".venv" / "Scripts" / "python.exe"
    nix = root / ".venv" / "bin" / "python"
    return win if win.exists() else nix


def check_env_module(root: Path):
    """Загрузить install/check_env.py — там живёт выбор интерпретатора.

    Своей логики версий здесь нет специально: окно версий и правило «каким питоном
    ставить» должны быть в ОДНОМ месте. Если модуль не загрузился — работаем
    по-старому (текущим интерпретатором), проверка не имеет права ронять установку.
    """
    import importlib.util
    for cand in (Path(__file__).resolve().parent / "check_env.py",
                 root / "install" / "check_env.py"):
        if not cand.is_file():
            continue
        try:
            spec = importlib.util.spec_from_file_location("_check_env_mv", cand)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
        except Exception:
            continue
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Создать .venv и поставить зависимости.")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--root")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--python", metavar="PATH",
                    help="Каким интерпретатором строить .venv. Обычно это "
                         "python.recommended.path из check_env.py --json. Без флага "
                         "скрипт проверяет тот питон, которым запущен сам, и при "
                         "негодной версии ищет подходящий на машине.")
    ap.add_argument("--recreate", action="store_true",
                    help="Пересобрать .venv, если он собран на неподходящем питоне "
                         "(папка .venv удаляется и создаётся заново).")
    args = ap.parse_args(argv)

    root = find_root(args.root)
    plan_raw = json.loads(Path(args.plan).read_text(encoding="utf-8-sig"))
    plan = plan_raw.get("plan", plan_raw)
    pips = list(plan.get("pip", []))

    # Страховка контракта: всё должно быть с ==.
    bad = [p for p in pips if "==" not in p]
    if bad:
        print("НЕЛЬЗЯ ставить незапиненные версии (CONTRACT.md §3):")
        for b in bad:
            print(f"  • {b}")
        return 2

    print(f"Корень установки: {root}")
    print(f"Пакетов к установке: {len(pips)}")
    if args.dry_run:
        for p in pips:
            print(f"  {p}")
        return 0

    venv_dir = root / ".venv"
    ce = check_env_module(root)

    # ── ПЕРВЫЙ ШЛАГБАУМ: подходит ли сам компьютер (МАК-Б2) ──────────────────
    # Шаг 1 мастера этот вопрос уже задавал, но Шаг 4 запускают и отдельно —
    # «повтори установку», ручной прогон, перезапуск после обрыва интернета.
    # Пропустить сюда Intel-мак значит отдать клиенту стену текста от pip
    # («Could not find a version that satisfies the requirement torch==2.8.0»)
    # ПОСЛЕ Homebrew и гигабайтов трафика. Правило одно и живёт в check_env.py —
    # своей копии здесь нет, как и в случае с окном версий питона.
    if ce and hasattr(ce, "macos_arch_verdict"):
        arch = ce.macos_arch_verdict(root)
        if not arch["ok"]:
            print(f"Установка на этом компьютере невозможна: {arch['reason']}.")
            print(f"Что делать: {arch['fix']}.")
            print("Это не сбой установки и не проблема с интернетом — "
                  "повторять смысла нет.")
            return 2

    # ── ВТОРОЙ ШЛАГБАУМ: каким питоном строить .venv ─────────────────────────
    # `venv.EnvBuilder` берёт ТОТ интерпретатор, которым запущен этот скрипт.
    # Именно из-за этого замер M0 давал клиенту питонью трассировку от pip
    # («нет mediapipe==0.10.14 под 3.14») вместо человеческого «не тот питон»:
    # свежий brew-питон проходил проверку и молча становился основой окружения.
    # Поэтому интерпретатор выбирается ЯВНО (правило — в install/check_env.py).
    choice = ce.interpreter_for(root, args.python) if ce else None
    if choice and not choice["ok"]:
        # Подходящего питона нет вовсе — тут уже надо ставить.
        print(f"Не могу создать окружение: {choice['reason']}.")
        print(f"Что сделать: {choice['fix']} — и повторите установку.")
        return 2
    if choice and choice["ok"] and not choice["current"] and not args.python:
        # Скрипт запущен НЕ тем питоном, но подходящий на машине есть.
        # Молча подменять интерпретатор не будем: окружение должно строиться
        # тем, кого назвали явно. Печатаем готовую строку для перезапуска.
        print(f"Установка запущена Python {'.'.join(map(str, sys.version_info[:3]))} — "
              f"он не подходит, нужен {choice['window']}.")
        print(f"Подходящий питон на этом компьютере уже есть: {choice['path']} "
              f"(версия {choice['version']}). Ставить ничего не надо.")
        print("Повторите установку им — вот этой командой:")
        print(f'  "{choice["path"]}" "{Path(__file__).resolve()}" '
              f'--plan "{args.plan}"' + (f' --root "{args.root}"' if args.root else ""))
        return 2

    # Уже собранное .venv на негодном питоне — тупик: pip будет падать снова.
    if ce and venv_python(root).exists():
        v = ce.probe_python(str(venv_python(root)))
        lo, hi = ce.required_python_range(root)
        if v and not ce.version_fits(v, lo, hi):
            if args.recreate:
                print(f"Прежнее окружение .venv собрано на Python {ce.vstr(v)} — "
                      f"он не подходит. Пересобираю…")
                shutil.rmtree(venv_dir, ignore_errors=True)
            else:
                print(f"Локальное окружение .venv собрано на Python {ce.vstr(v)}, "
                      f"а нужен {ce.window_text(lo, hi)}. Из-за этого установка "
                      f"пакетов будет падать.")
                print("Окружение надо пересобрать — это папка .venv внутри завода, "
                      "ничего вашего в ней нет. Повторите с ключом --recreate.")
                return 2

    if not venv_python(root).exists():
        print("Создаю локальное окружение .venv …")
        if choice and choice["ok"] and not choice["current"]:
            # Сюда попадаем ТОЛЬКО с явным --python: интерпретатор назвали, и он
            # не тот, которым запущен скрипт. `venv.EnvBuilder` тут неприменим —
            # он умеет собрать окружение исключительно вокруг СЕБЯ.
            print(f"Использую Python {choice['version']}: {choice['path']}")
            subprocess.run([choice["path"], "-m", "venv", str(venv_dir)], check=False)
        else:
            try:
                import venv  # локально: см. предупреждение у импортов файла
            except ModuleNotFoundError:
                print("Этим интерпретатором окружение не собрать: во встроенном "
                      "Python нет модуля venv. В коробке окружение уже развёрнуто "
                      "установщиком — если папки .venv нет, переустановите завод "
                      "или назовите системный питон ключом --python.")
                return 2
            venv.EnvBuilder(with_pip=True, clear=False).create(str(venv_dir))
    py = venv_python(root)
    if not py.exists():
        print("Не удалось создать .venv — окружение не собралось.")
        return 1

    # Зафиксируем список для воспроизводимости.
    lock = root / "requirements.lock.txt"
    lock.write_text("\n".join(pips) + "\n", encoding="utf-8")

    print("Обновляю pip …")
    subprocess.run([str(py), "-m", "pip", "install", "--upgrade", "pip"],
                   check=False)

    if not pips:
        print("Список зависимостей пуст — ставить нечего.")
        return 0

    print("Ставлю зависимости одним заходом (это может занять время) …")
    r = subprocess.run(
        [str(py), "-m", "pip", "install", "--no-input", "-r", str(lock)],
        check=False,
    )
    if r.returncode != 0:
        # Причина одна и та же, но называть чужую ОС нельзя: мак-клиенту фраза
        # «частые причины на Windows» ничего не объясняет. Ветвимся по ОС,
        # Windows-текст остаётся прежним байт-в-байт.
        if platform.system().strip().lower() == "windows":
            print(
                "\nУстановка части пакетов не удалась. Частые причины на Windows: "
                "нет интернета, или тяжёлый пакет (torch/mediapipe/whisperx) не собрал "
                "колесо. Проверьте интернет и повторите «установи» — pip доставит "
                "недостающее, уже поставленное не тронет."
            )
        else:
            print(
                "\nУстановка части пакетов не удалась. Частые причины на маке: "
                "нет интернета, или под запущенный питон ещё нет готовых колёс "
                "тяжёлого пакета (torch/mediapipe/whisperx) — тогда проверку версии "
                "стоит перечитать: python install/check_env.py. Проверьте интернет и "
                "повторите «установи» — pip доставит недостающее, уже поставленное "
                "не тронет."
            )
        return 1
    print("Зависимости установлены.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

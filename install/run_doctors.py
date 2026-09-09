# -*- coding: utf-8 -*-
"""
run_doctors — прогон doctor каждого модуля и русская сводка (шаг 6 установки).

СТАНДАРТ ВЫЗОВА DOCTOR (контракт запуска, зафиксирован OWNER):
    python -m <name>.doctor
  запускается с каталогом modules/ на PYTHONPATH (модули лежат в modules/<name>/,
  внутри — пакет с __init__.py и doctor.py). Рабочий каталог — корень установки.
  Интерпретатор — из локального .venv, если он есть, иначе текущий python.

Контракт doctor (CONTRACT.md §2), BOX-51 — код возврата это СТЕПЕНЬ беды:
    - код возврата 0  → модуль полностью работоспособен;
    - код возврата 2  → УРЕЗАННЫЙ РЕЖИМ: главное работает, часть возможностей
      недоступна (не заполнен токен площадки, нет необязательного пакета);
    - код возврата 1  → НЕ ГОТОВ: главного пути нет либо сама проверка упала.
    На stdout в обоих ненулевых случаях — по-русски человеческим языком, что не
    так и как починить (НЕ стектрейс).
    Разница не косметическая: код 1 на пройденном этапе уходит владельцу красной
    срочной тревогой (health_report.phase → broken), код 2 — обычной строкой.

Скрипт агрегирует коды и печатает сводку. Сам код возврата скрипта — 0 всегда
(частичная готовность — норма для коробки), реальные статусы видны в сводке
и в --json. Только stdlib. Кроссплатформенно.

Вход:
    --root PATH   корень установки (по умолчанию авто)
    --json        машинная сводка
"""
from __future__ import annotations

import argparse
import json
import os
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



def find_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    here = Path(__file__).resolve().parent
    for cand in (here, *here.parents):
        for marker in (".factory_root", "modules", "CONTRACT.md"):
            if (cand / marker).exists():
                return cand
    return here.parent if here.name == "install" else Path.cwd()


def venv_python(root: Path) -> str:
    """Путь к python из локального .venv (Windows/Unix) или текущий sys.executable."""
    win = root / ".venv" / "Scripts" / "python.exe"
    nix = root / ".venv" / "bin" / "python"
    if win.exists():
        return str(win)
    if nix.exists():
        return str(nix)
    return sys.executable


def discover_modules(root: Path) -> list[dict]:
    """[{name, doctor}] по манифестам. doctor — команда из манифеста или стандарт."""
    mod_dir = root / "modules"
    if not mod_dir.is_dir():
        return []
    out = []
    for man in sorted(mod_dir.glob("*/module.manifest.json")):
        try:
            data = json.loads(man.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            data = {}
        name = data.get("name") or man.parent.name
        # Стандарт вызова (CONTRACT.md §2): python -m <name>.doctor.
        doctor = data.get("doctor") or f"python -m {name}.doctor"
        out.append({"name": name, "doctor": doctor})
    return out


def _tokenize(cmd: str, py: str) -> list[str]:
    """Разобрать команду doctor и подменить ведущий python на интерпретатор .venv."""
    import shlex
    parts = shlex.split(cmd, posix=(os.name != "nt"))
    if parts and parts[0].lower() in ("python", "python3", "py"):
        parts[0] = py
    return parts


def run_one(root: Path, py: str, mod: dict, timeout: int = 300) -> dict:
    name = mod["name"]
    env = dict(os.environ)
    mod_dir = str(root / "modules")
    env["PYTHONPATH"] = mod_dir + os.pathsep + str(root) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    args = _tokenize(mod["doctor"], py)
    try:
        proc = subprocess.run(
            args, cwd=str(root), env=env, timeout=timeout,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return {"module": name, "code": 124, "ok": False,
                "text": "Проверка не завершилась за отведённое время (таймаут)."}
    except OSError as e:
        return {"module": name, "code": 127, "ok": False,
                "text": f"Не удалось запустить проверку модуля: {e}"}
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0 and "No module named" in err and not out:
        return {"module": name, "code": proc.returncode, "ok": False,
                "text": f"Не найден пакет проверки ({mod['doctor']}). "
                        f"Проверьте, что в modules/{name}/ есть __init__.py и doctor.py, "
                        f"а имя пакета совпадает с командой doctor в манифесте."}
    text = out or err or "(модуль ничего не сообщил)"
    return {"module": name, "code": proc.returncode,
            "ok": proc.returncode == 0, "text": text}


def summary(results: list[dict]) -> str:
    ok = [r for r in results if r["ok"]]
    # BOX-51: урезанный режим (код 2) и «не готов» (всё остальное) — разные беды
    # и печатаются разными разделами. Раньше они лежали в одной куче, и клиент
    # видел одинаково грозно и «не заполнен токен ВК», и «модуль не импортируется».
    deg = [r for r in results if not r["ok"] and int(r.get("code", 1)) == 2]
    dead = [r for r in results if not r["ok"] and int(r.get("code", 1)) != 2]
    lines = ["=== ИТОГ ПРОВЕРКИ «ВСЁ ЛИ ЖИВО» ===", ""]

    def block(title: str, rows: list) -> None:
        lines.append(title)
        for r in rows:
            first = r["text"].splitlines()[0] if r["text"] else ""
            lines.append(f"  • {r['module']}: {first}")
            for extra in r["text"].splitlines()[1:]:
                lines.append(f"      {extra}")
        lines.append("")

    if ok:
        lines.append("РАБОТАЕТ ПОЛНОСТЬЮ:")
        for r in ok:
            lines.append(f"  ✓ {r['module']}")
        lines.append("")
    if dead:
        block("НЕ РАБОТАЕТ — ЧИНИТЬ:", dead)
    if deg:
        block("РАБОТАЕТ В УРЕЗАННОМ РЕЖИМЕ (это нормально):", deg)
    lines.append(f"Готово полностью: {len(ok)} из {len(results)}. "
                 f"Урезанный режим — это нормально: чего-то не хватает для части "
                 f"функций, остальное работает.")
    return "\n".join(lines)


def filter_by_stage(root: Path, modules: list[dict], stage_id: int) -> list[dict]:
    """Оставить только модули этапа stage_id (install/stages.json), без учёта
    регистра и дефис↔подчёркивание."""
    # `stages.py` лежит РЯДОМ, но полагаться на «sys.path[0] == папка скрипта»
    # здесь нельзя. У WINDOWS-клиента `.venv` собран из embeddable-дистрибутива
    # Python, а тот читает пути из `python311._pth` и НЕ добавляет каталог
    # скрипта автоматически — импорт соседа падает ModuleNotFoundError, и
    # проверка этапа обрывалась у КАЖДОГО windows-клиента. На маке обычный venv,
    # умолчание работает, поэтому дефект и не всплывал у нас.
    # Найдено агентом в копии клиента (Антон Ахтямов, 25.08.2026).
    # Чиним тем же приёмом, что уже принят у соседа install/install_models.py:
    # явно кладём свой каталог в путь поиска. Идемпотентно.
    _here = str(Path(__file__).resolve().parent)
    if _here not in sys.path:
        sys.path.insert(0, _here)
    import stages  # sibling — путь к нему обеспечен строками выше
    want = stages.modules_for_stage(root, stage_id)
    if not want:
        return []
    return [m for m in modules if stages.norm_name(m["name"]) in want]


def health_tick(root: Path) -> None:
    """P120: тонкая точка подключения суточной сводки о здоровье коробки.

    Сама ничего не считает и не отправляет: `health_report.tick()` читает пару
    json и, если пора И клиент дал согласие, отсоединяет фоновый процесс. Без
    согласия не делает вообще ничего. Любая ошибка гасится — проверка модулей не
    имеет права зависеть от диагностики. `KZ_HEALTH_CHILD` ставит сам сборщик
    сводки: он же зовёт этот скрипт, и без флага получилась бы рекурсия.
    """
    if os.environ.get("KZ_HEALTH_CHILD"):
        return
    try:
        import importlib.util
        cand = root / "health_report.py"
        if not cand.is_file():
            return
        spec = importlib.util.spec_from_file_location("factory_health", cand)
        if spec is None or spec.loader is None:
            return
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.tick(root)
    except Exception:                                  # noqa: BLE001
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Прогон doctor модулей и русская сводка.")
    ap.add_argument("--root", help="Корень установки.")
    ap.add_argument("--stage", type=int, metavar="N",
                    help="Проверить только модули этапа N (install/stages.json).")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    root = find_root(args.root)
    py = venv_python(root)
    modules = discover_modules(root)
    if args.stage is not None:
        modules = filter_by_stage(root, modules, args.stage)
    results = [run_one(root, py, m) for m in modules]

    if args.json:
        print(json.dumps({"root": str(root), "results": results},
                         ensure_ascii=False, indent=2))
    else:
        if not results:
            print("Модули не найдены (нет modules/*/module.manifest.json).")
        else:
            print(summary(results))
    health_tick(root)          # P120: тихо и только по согласию клиента
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""
fetch_wheels — вендоринг ПОД WINDOWS запиненных pip-колёс в локальный wheelhouse,
чтобы установка у клиента шла ПОЛНОСТЬЮ ОФЛАЙН (ничего не качается, кроме музыки).

Качает КОЛЁСА под целевую платформу (win_amd64 / cp311) для ВСЕХ pip-спеков из
module.manifest.json + их зависимостей, кладёт в `installer/vendor/wheels`, туда же
кладёт колесо `pip` (для bootstrap embeddable-Python), пишет `requirements.lock.txt`
и СРАЗУ прогоняет лицензионный гейт (license_audit.py): если в скачанных колёсах
GPL/AGPL — печатает громко и возвращает ненулевой код.

ГДЕ ЗАПУСКАТЬ. `pip download --platform win_amd64` работает и с mac/linux (тянет
готовые win-колёса из PyPI) — так можно вендорить, не имея Windows. НО платформенно-
зависимые сборки (torch/av/pillow-heif) честнее и надёжнее вендорить на самой
Windows-машине (BUILD-RECIPE.md, шаг вендоринга): тогда wheelhouse гарантированно
соответствует целевому ABI. Скрипт работает в обоих режимах.

Требует интернет (это ОНЛАЙН-шаг ПОДГОТОВКИ у владельца, не у клиента). Модели и
музыку НЕ трогает (их кладут install_models.py / install_music.py).

Запуск:
    python installer/fetch_wheels.py                     # win_amd64 / cp311 по манифесту
    python installer/fetch_wheels.py --dest DIR          # иной каталог wheelhouse
    python installer/fetch_wheels.py --platform-tag win_amd64 --python-version 3.11
    python installer/fetch_wheels.py --plan install/.plan.json   # взять pip из плана этапа
    python installer/fetch_wheels.py --dry-run           # только показать план

Код возврата: 0 — колёса скачаны и лицензионный гейт чист; 1 — ошибка загрузки или
GPL/AGPL в колёсах; 2 — плохой ввод. Только stdlib (кроме вызова pip). Кроссплатформенно.
"""
from __future__ import annotations

import argparse
import glob
import json
import subprocess
import sys
from pathlib import Path

DEFAULT_PLATFORM = "win_amd64"
DEFAULT_PYVER = "3.11"


def find_root(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    here = Path(__file__).resolve().parent
    for cand in (here, *here.parents):
        for marker in (".factory_root", "modules", "CONTRACT.md"):
            if (cand / marker).exists():
                return cand
    return here.parent if here.name == "installer" else Path.cwd()


def specs_from_manifests(root: Path) -> list[str]:
    seen: dict[str, str] = {}
    for f in sorted(glob.glob(str(root / "modules" / "*" / "module.manifest.json"))):
        try:
            m = json.loads(Path(f).read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        for spec in m.get("pip", []) or []:
            key = spec.split("==")[0].lower()
            if key in seen and seen[key] != spec:
                raise SystemExit(f"Конфликт версий pip: {seen[key]} vs {spec}")
            seen[key] = spec
    return sorted(seen.values())


def specs_from_plan(plan_path: Path) -> list[str]:
    raw = json.loads(plan_path.read_text(encoding="utf-8-sig"))
    plan = raw.get("plan", raw)
    return list(plan.get("pip", []))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Вендоринг win-колёс в офлайн wheelhouse.")
    ap.add_argument("--root")
    ap.add_argument("--dest", help="Каталог wheelhouse (по умолчанию installer/vendor/wheels).")
    ap.add_argument("--plan", help="Взять pip из плана этапа (collect_manifests --json).")
    ap.add_argument("--platform-tag", default=DEFAULT_PLATFORM)
    ap.add_argument("--python-version", default=DEFAULT_PYVER)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-audit", action="store_true",
                    help="Не запускать license_audit после загрузки (не рекомендуется).")
    args = ap.parse_args(argv)

    root = find_root(args.root)
    dest = Path(args.dest) if args.dest else root / "installer" / "vendor" / "wheels"

    try:
        specs = specs_from_plan(Path(args.plan)) if args.plan else specs_from_manifests(root)
    except SystemExit as e:
        print(e); return 2

    bad = [s for s in specs if "==" not in s]
    if bad:
        print("Незапиненные версии (CONTRACT.md §3): " + ", ".join(bad)); return 2

    print(f"Корень: {root}")
    print(f"Целевая платформа: {args.platform_tag} / cp{args.python_version.replace('.', '')}")
    print(f"Wheelhouse: {dest}")
    print(f"Пакетов (прямых спеков): {len(specs)}")
    lock = "\n".join(specs) + "\n"

    if args.dry_run:
        for s in specs:
            print(f"  {s}")
        print("\n(dry-run — ничего не скачано)")
        return 0

    dest.mkdir(parents=True, exist_ok=True)
    (root / "installer" / "vendor").mkdir(parents=True, exist_ok=True)
    lock_path = root / "installer" / "vendor" / "requirements.lock.txt"
    lock_path.write_text(lock, encoding="utf-8")

    # КОНСТРЕЙНТЫ транзитивных зависимостей с проблемной резолюцией.
    #  • omegaconf 2.1.0 имеет невалидную метаданную ("PyYAML (>=5.1.*)"), pip>=24.1
    #    её отвергает и зацикливается при бэктрекинге → пиним валидную 2.3.0
    #    (удовлетворяет требованию pyannote-audio omegaconf<3.0,>=2.1).
    TRANSITIVE_PINS = ["omegaconf==2.3.0"]
    #  • antlr4-python3-runtime==4.9.* (нужен omegaconf 2.x) НЕ имеет готового
    #    wheel на PyPI (только 4.11+). Пакет ЧИСТО-PYTHON → собираем wheel из
    #    sdist сами (pip wheel), кладём в wheelhouse. Универсален (py3-none-any).
    SDIST_BUILD = ["antlr4-python3-runtime==4.9.3"]
    constraints_path = dest.parent / "constraints.txt"
    constraints_path.write_text("\n".join(TRANSITIVE_PINS) + "\n", encoding="utf-8")

    common = [
        sys.executable, "-m", "pip", "download",
        "--only-binary=:all:",
        "--platform", args.platform_tag,
        "--python-version", args.python_version,
        "-c", str(constraints_path),
        "-d", str(dest),
    ]
    # bootstrap-колесо pip — для embeddable-Python (у него нет pip из коробки)
    print("Качаю bootstrap-колесо pip …")
    subprocess.run(common + ["--no-deps", "pip"], check=False)

    # основной набор + зависимости
    print("Качаю запиненные колёса + их зависимости под целевую платформу …")
    r = subprocess.run(common + ["-r", str(lock_path)], check=False)

    # Чисто-python зависимости без готового wheel — собираем wheel из sdist.
    for spec in SDIST_BUILD:
        print(f"Собираю wheel из sdist (чистый python): {spec} …")
        subprocess.run([sys.executable, "-m", "pip", "wheel", "--no-deps",
                        "-w", str(dest), spec], check=False)

    n = len(list(dest.glob("*.whl")))
    if r.returncode != 0:
        print(f"\nЧасть колёс не скачалась (в wheelhouse: {n}). Частая причина: у "
              f"пакета нет готового колеса под {args.platform_tag}/cp"
              f"{args.python_version.replace('.', '')} — тогда вендорить надо на "
              f"Windows-машине (BUILD-RECIPE.md).")
        return 1
    print(f"Готово: колёс в wheelhouse — {n}.")

    if args.skip_audit:
        print("Лицензионный гейт пропущен по --skip-audit (на свой риск).")
        return 0

    # лицензионный гейт по РЕАЛЬНЫМ колёсам
    audit = root / "installer" / "license_audit.py"
    if audit.is_file():
        print("\n── Лицензионный гейт по скачанным колёсам ──")
        ra = subprocess.run([sys.executable, str(audit), "--wheelhouse", str(dest)],
                            check=False)
        if ra.returncode == 1:
            print("\nВЕНДОРИНГ ОСТАНОВЛЕН: в колёсах найден GPL/AGPL. Убери/замени "
                  "пакет-нарушитель (см. THIRD_PARTY.md) и повтори.")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

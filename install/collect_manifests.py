# -*- coding: utf-8 -*-
"""
collect_manifests — сбор и слияние всех module.manifest.json.

Используется скиллом /install (шаг 2). Сканирует modules/<name>/module.manifest.json
относительно корня установки и сливает потребности модулей в один план установки:

    - pip:              объединение, дедуп по имени пакета; конфликт версий → ошибка
    - system_binaries:  дедуп по name
    - config_questions: дедуп по ключу БЕЗ учёта регистра, канон lower_snake_case
    - folders:          объединение, дедуп
    - doctor:           команда проверки на модуль (стандарт: python -m <name>.doctor)

Печатает результат как JSON в stdout (--json) или человекочитаемую сводку по-русски.
Код возврата 0 при успехе, 2 при конфликте (версии pip / несовместимые вопросы).

Только stdlib. Кроссплатформенно. Без абсолютных путей.

Запуск:
    python install/collect_manifests.py            # русская сводка плана
    python install/collect_manifests.py --json      # машинный план для установщика
    python install/collect_manifests.py --root PATH # явно указать корень
"""
from __future__ import annotations

import argparse
import json
import re
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


# Разбор строки pip: "имя==версия[; маркеры]" / "имя[extra]==версия".
_PIP_RE = re.compile(r"^\s*([A-Za-z0-9._-]+)\s*(\[[^\]]*\])?\s*==\s*([^;#\s]+)")


def find_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    here = Path(__file__).resolve().parent
    for cand in (here, *here.parents):
        for marker in (".factory_root", "modules", "CONTRACT.md"):
            if (cand / marker).exists():
                return cand
    return here.parent if here.name == "install" else Path.cwd()


def discover_manifests(root: Path) -> list[Path]:
    mod_dir = root / "modules"
    if not mod_dir.is_dir():
        return []
    return sorted(mod_dir.glob("*/module.manifest.json"))


def _manifest_name(path: Path) -> str:
    """Имя модуля из манифеста (или имя каталога, если не прочиталось)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data.get("name") or path.parent.name
    except (OSError, json.JSONDecodeError):
        return path.parent.name


def filter_by_stage(root: Path, paths: list[Path], stage_id: int) -> list[Path]:
    """Оставить только манифесты модулей, отнесённых к этапу stage_id (stages.json).

    Сопоставление БЕЗ учёта регистра и дефис↔подчёркивание (niche-hooks==niche_hooks).
    """
    import stages  # sibling; sys.path[0] == install/ при запуске install/collect_manifests.py
    want = stages.modules_for_stage(root, stage_id)
    if not want:
        return []
    out = []
    for p in paths:
        key = stages.norm_name(_manifest_name(p))
        dirkey = stages.norm_name(p.parent.name)
        if key in want or dirkey in want:
            out.append(p)
    return out


def canon_key(key: str) -> str:
    return key.strip().lower()


def _pip_name(spec: str) -> tuple[str, str] | None:
    m = _PIP_RE.match(spec)
    if not m:
        return None
    return m.group(1).lower(), m.group(3)


def merge(manifests: list[dict]) -> tuple[dict, list[str]]:
    """Слить манифесты. Возвращает (план, список_ошибок)."""
    errors: list[str] = []

    pip: dict[str, tuple[str, str, str]] = {}   # name -> (version, raw, origin)
    binaries: dict[str, dict] = {}
    questions: dict[str, dict] = {}
    folders: list[str] = []
    seen_folders: set[str] = set()
    doctors: list[dict] = []

    for man in manifests:
        name = man.get("name", "<без имени>")

        # ── pip ──────────────────────────────────────────────────────────
        for spec in man.get("pip", []) or []:
            parsed = _pip_name(spec)
            if parsed is None:
                errors.append(
                    f"[{name}] pip-зависимость без строгого пиннинга '==': '{spec}'. "
                    f"CONTRACT.md §3 требует ==."
                )
                continue
            pkg, ver = parsed
            if pkg in pip and pip[pkg][0] != ver:
                prev_ver, _, prev_origin = pip[pkg]
                errors.append(
                    f"КОНФЛИКТ ВЕРСИЙ pip: пакет '{pkg}' — "
                    f"{prev_ver} (модуль {prev_origin}) против {ver} (модуль {name}). "
                    f"Согласуйте одну версию в манифестах."
                )
            else:
                pip.setdefault(pkg, (ver, spec.strip(), name))

        # ── system_binaries ──────────────────────────────────────────────
        for b in man.get("system_binaries", []) or []:
            bn = (b.get("name") or "").strip()
            if bn and bn not in binaries:
                binaries[bn] = b

        # ── config_questions (дедуп по канону ключа) ─────────────────────
        for q in man.get("config_questions", []) or []:
            raw_key = q.get("key", "")
            if not raw_key:
                errors.append(f"[{name}] вопрос без ключа: {q!r}")
                continue
            ck = canon_key(raw_key)
            if ck in questions:
                prev = questions[ck]
                # secret: если хоть один модуль пометил секретом — секрет.
                if bool(q.get("secret")) != bool(prev.get("secret")):
                    prev["secret"] = prev.get("secret") or q.get("secret")
                    errors.append(
                        f"ВНИМАНИЕ: ключ '{ck}' помечен secret по-разному в модулях "
                        f"{prev.get('_origin')} и {name} — берём secret=true (безопаснее)."
                    )
                prev.setdefault("_origin", name)
                if name not in prev["_modules"]:
                    prev["_modules"].append(name)
                continue
            q = dict(q)
            q["key"] = ck                       # канонизируем ключ
            q["_origin"] = name
            q["_modules"] = [name]
            if not q.get("help"):
                errors.append(
                    f"[{name}] вопрос '{ck}' без help — CONTRACT.md §1 требует "
                    f"инструкцию «где взять» для нетехнического человека."
                )
            questions[ck] = q

        # ── folders ──────────────────────────────────────────────────────
        for f in man.get("folders", []) or []:
            f = str(f).strip().strip("/\\")
            if f and f not in seen_folders:
                seen_folders.add(f)
                folders.append(f)

        # ── doctor ───────────────────────────────────────────────────────
        doc = man.get("doctor") or f"python -m {name}.doctor"
        doctors.append({"module": name, "cmd": doc})

    plan = {
        "modules": [m.get("name") for m in manifests],
        "pip": [pip[p][1] for p in sorted(pip)],
        "system_binaries": list(binaries.values()),
        "config_questions": [
            {k: v for k, v in q.items() if k != "_origin"}
            for q in questions.values()
        ],
        "folders": folders,
        "doctors": doctors,
    }
    return plan, errors


def load_manifests(paths: list[Path]) -> tuple[list[dict], list[str]]:
    out, errors = [], []
    for p in paths:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8-sig")))
        except (json.JSONDecodeError, OSError) as e:
            errors.append(f"Не удалось прочитать {p}: {e}")
    return out, errors


def human_summary(plan: dict, errors: list[str]) -> str:
    lines = []
    mods = ", ".join(m for m in plan["modules"] if m) or "—"
    lines.append(f"Найдено модулей: {len(plan['modules'])} ({mods})")
    lines.append(f"Пакетов Python к установке: {len(plan['pip'])}")
    if plan["system_binaries"]:
        bl = ", ".join(b.get("name", "?") for b in plan["system_binaries"])
        lines.append(f"Системные программы: {bl}")
    lines.append(f"Вопросов клиенту: {len(plan['config_questions'])}")
    lines.append(f"Папок к созданию: {len(plan['folders'])}")
    if errors:
        lines.append("")
        lines.append("ПРОБЛЕМЫ (нужно решить до установки):")
        for e in errors:
            lines.append(f"  • {e}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Сбор и слияние манифестов модулей.")
    ap.add_argument("--root", help="Корень установки (по умолчанию — авто-поиск).")
    ap.add_argument("--json", action="store_true", help="Вывести машинный план JSON.")
    ap.add_argument("--stage", type=int, metavar="N",
                    help="Собрать план только для модулей этапа N (install/stages.json).")
    args = ap.parse_args(argv)

    root = find_root(args.root)
    paths = discover_manifests(root)
    if args.stage is not None:
        paths = filter_by_stage(root, paths, args.stage)
    manifests, read_errs = load_manifests(paths)
    plan, merge_errs = merge(manifests)
    errors = read_errs + merge_errs

    # Ошибки-конфликты (версии/пиннинг) — это стоп. Предупреждения про secret
    # начинаются со слова ВНИМАНИЕ и стопом не считаются.
    hard = [e for e in errors if not e.startswith("ВНИМАНИЕ")]

    if args.json:
        # ensure_ascii=True — НЕ косметика. Мастер зовёт этот вывод так:
        #   collect_manifests.py --stage N --json > install/.plan.json
        #   questions.py --plan install/.plan.json
        # При перенаправлении Python пишет файл в кодировке локали (на русской
        # Windows — cp1251), а questions.read_json читает строго utf-8 — и связка
        # рвётся UnicodeDecodeError на первой же русской букве. С ascii-экранами
        # файл читается любой кодировкой, json.loads разворачивает \uXXXX обратно.
        print(json.dumps({"root": str(root), "plan": plan, "errors": errors},
                         ensure_ascii=True, indent=2))
    else:
        print(human_summary(plan, errors))

    return 2 if hard else 0


if __name__ == "__main__":
    sys.exit(main())

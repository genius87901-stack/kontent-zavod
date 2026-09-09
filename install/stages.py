# -*- coding: utf-8 -*-
"""
stages — поэтапная установка «Контент-завода» (политика + состояние).

Слой установки (OWNER) делит развёртывание завода на 3 последовательных этапа
(intake+движок → Reels+публикация → продвинутое). Раскладка «этап → модули/скилы»
живёт в install/stages.json (правит только OWNER). Пройденные этапы запоминаются в
install/.state.json, чтобы установка была идемпотентной и продолжалась с места.

Модуль используется и как библиотека (collect_manifests / run_doctors импортируют
resolvers), и как CLI для скилла /install:

    python install/stages.py --status          # русская сводка: что стоит, что дальше
    python install/stages.py --json            # машинная сводка для мастера
    python install/stages.py --resolve "этап 2" | next   # → номер этапа на stdout
    python install/stages.py --mark-done 1     # отметить этап пройденным

Только stdlib. Кроссплатформенно. Без абсолютных путей.
"""
from __future__ import annotations

import argparse
import datetime
import json
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



# ── корень установки (тот же контракт поиска, что и в остальных скриптах) ──────
def find_root(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    here = Path(__file__).resolve().parent
    for cand in (here, *here.parents):
        for marker in (".factory_root", "modules", "CONTRACT.md"):
            if (cand / marker).exists():
                return cand
    return here.parent if here.name == "install" else Path.cwd()


def norm_name(name: str) -> str:
    """Канон имени модуля: нижний регистр, дефис == подчёркивание.

    Позволяет stages.json писать 'niche-hooks', а каталогу быть 'niche_hooks'.
    """
    return str(name).strip().lower().replace("-", "_")


# ── политика этапов (stages.json) ─────────────────────────────────────────────
def load_stages(root: Path) -> list[dict]:
    p = root / "install" / "stages.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []
    stages = data.get("stages", []) or []
    return sorted(stages, key=lambda s: s.get("id", 0))


def stage_ids(root: Path) -> list[int]:
    return [int(s["id"]) for s in load_stages(root) if "id" in s]


def load_optional_steps(root: Path) -> list[dict]:
    """Необязательные аддитивные шаги (напр. «музыка») вне нумерации этапов 1–3.

    Живут в stages.json под ключом optional_steps. Не входят в next-цепочку и не
    блокируют этапы: мастер /install предлагает их по запросу клиента.
    """
    p = root / "install" / "stages.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []
    return data.get("optional_steps", []) or []


def stage_by_id(root: Path, sid: int) -> dict | None:
    for s in load_stages(root):
        if int(s.get("id", -1)) == int(sid):
            return s
    return None


def modules_for_stage(root: Path, sid: int) -> set[str]:
    """Каноничные имена модулей этапа (norm_name)."""
    s = stage_by_id(root, sid)
    if not s:
        return set()
    return {norm_name(m) for m in s.get("modules", []) or []}


def stage_of_module(root: Path, name: str) -> int | None:
    key = norm_name(name)
    for s in load_stages(root):
        if key in {norm_name(m) for m in s.get("modules", []) or []}:
            return int(s["id"])
    return None


# ── состояние (.state.json) ───────────────────────────────────────────────────
def state_path(root: Path) -> Path:
    return root / "install" / ".state.json"


def load_state(root: Path) -> dict:
    p = state_path(root)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
            data.setdefault("stages_done", [])
            return data
        except (OSError, json.JSONDecodeError):
            pass
    return {"version": 1, "stages_done": []}


def save_state(root: Path, st: dict) -> None:
    st.setdefault("version", 1)
    st["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
    state_path(root).write_text(
        json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def done_stages(root: Path) -> list[int]:
    return sorted(int(x) for x in load_state(root).get("stages_done", []))


def mark_done(root: Path, sid: int) -> None:
    st = load_state(root)
    done = set(int(x) for x in st.get("stages_done", []))
    done.add(int(sid))
    st["stages_done"] = sorted(done)
    save_state(root, st)


def next_stage(root: Path) -> int | None:
    """Первый по порядку этап, который ещё не пройден. None — все пройдены."""
    done = set(done_stages(root))
    for sid in stage_ids(root):
        if sid not in done:
            return sid
    return None


def prior_missing(root: Path, sid: int) -> list[int]:
    """Непройденные этапы СТРОГО раньше sid (для мягкого контроля порядка)."""
    done = set(done_stages(root))
    return [i for i in stage_ids(root) if i < sid and i not in done]


# ── разбор пожелания клиента ('этап 2', 'stage 3', 'next', '2') ───────────────
_ORDINALS = {
    "перв": 1, "втор": 2, "трет": 3, "четвёрт": 4, "четверт": 4, "пят": 5,
    "first": 1, "second": 2, "third": 3,
}


def resolve(root: Path, text: str) -> int | None:
    t = (text or "").strip().lower()
    if not t or t in ("next", "следующий", "дальше", "далее", "продолжай", "продолжить"):
        return next_stage(root)
    ids = stage_ids(root)
    # первое число в строке
    num = ""
    for ch in t:
        if ch.isdigit():
            num += ch
        elif num:
            break
    if num:
        sid = int(num)
        return sid if sid in ids else None
    # словесные порядковые («третий этап»)
    for stem, sid in _ORDINALS.items():
        if stem in t and sid in ids:
            return sid
    return next_stage(root)


# ── человекочитаемая сводка ───────────────────────────────────────────────────
def status_text(root: Path) -> str:
    stages = load_stages(root)
    if not stages:
        return ("Не найден план этапов (install/stages.json). Это дефект коробки — "
                "сообщите разработчику.")
    done = set(done_stages(root))
    nxt = next_stage(root)
    lines = ["=== ЭТАПЫ УСТАНОВКИ «КОНТЕНТ-ЗАВОДА» ===", ""]
    for s in stages:
        sid = int(s["id"])
        if sid in done:
            mark = "✓ установлен"
        elif sid == nxt:
            mark = "→ следующий"
        else:
            mark = "· позже"
        lines.append(f"[{mark}] {s.get('title', 'Этап ' + str(sid))}")
        unlocks = s.get("unlocks")
        if unlocks:
            lines.append(f"        даёт: {unlocks}")
    lines.append("")
    if nxt is None:
        lines.append("Все этапы пройдены. Завод развёрнут полностью.")
    else:
        s = stage_by_id(root, nxt)
        lines.append(f"Дальше — этап {nxt}: {s.get('title', '')}.")
        lines.append(f"Чтобы поставить, напишите: «установи этап {nxt}» "
                     f"(или просто «установи» — начну с него).")
    opt = load_optional_steps(root)
    if opt:
        lines.append("")
        lines.append("Отдельные шаги (по желанию, вне нумерации этапов):")
        for st in opt:
            lines.append(f"  • {st.get('title', st.get('id', 'шаг'))}")
            if st.get("unlocks"):
                lines.append(f"        даёт: {st['unlocks']}")
    return "\n".join(lines)


def status_json(root: Path) -> dict:
    done = set(done_stages(root))
    out_stages = []
    for s in load_stages(root):
        sid = int(s["id"])
        out_stages.append({
            "id": sid,
            "title": s.get("title", ""),
            "modules": s.get("modules", []),
            "skills": s.get("skills", []),
            "intro": s.get("intro", ""),
            "first_step": s.get("first_step", ""),
            "unlocks": s.get("unlocks", ""),
            "next_hint": s.get("next_hint", ""),
            "warnings": s.get("warnings", []),
            "installed": sid in done,
        })
    return {
        "root": str(root),
        "stages": out_stages,
        "optional_steps": load_optional_steps(root),
        "done": sorted(done),
        "next": next_stage(root),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Поэтапная установка: политика и состояние.")
    ap.add_argument("--root")
    ap.add_argument("--status", action="store_true", help="Русская сводка этапов.")
    ap.add_argument("--json", action="store_true", help="Машинная сводка этапов.")
    ap.add_argument("--resolve", metavar="TEXT",
                    help="Разобрать пожелание ('этап 2' | 'next') → номер на stdout.")
    ap.add_argument("--mark-done", type=int, metavar="N", help="Отметить этап пройденным.")
    args = ap.parse_args(argv)

    root = find_root(args.root)

    if args.mark_done is not None:
        if args.mark_done not in stage_ids(root):
            print(f"Нет такого этапа: {args.mark_done}", file=sys.stderr)
            return 2
        mark_done(root, args.mark_done)
        print(f"Этап {args.mark_done} отмечен пройденным.")
        return 0

    if args.resolve is not None:
        sid = resolve(root, args.resolve)
        if sid is None:
            print("", end="")
            return 2
        print(sid)
        return 0

    if args.json:
        print(json.dumps(status_json(root), ensure_ascii=False, indent=2))
        return 0

    # по умолчанию — человекочитаемый статус
    print(status_text(root))
    return 0


if __name__ == "__main__":
    sys.exit(main())

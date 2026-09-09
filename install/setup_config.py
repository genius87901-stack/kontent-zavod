# -*- coding: utf-8 -*-
"""
setup_config — запись конфига и создание папок (шаги 4–5 установки).

Скилл /install собирает у клиента ответы (по одному вопросу из объединённого
плана) и передаёт их сюда одним JSON-файлом. Этот скрипт:

    1. пишет НЕсекретные ответы в config/factory.config.json;
    2. пишет секреты в .env (права 0600, gitignored) — в лог не попадают;
    3. создаёт маркер корня .factory_root;
    4. создаёт папки модулей из folders (относительно корня).

Структуру Google Диска этот скрипт НЕ создаёт (до 11.08.2026 создавал — своим
списком папок `Загрузить/Готово/Опубликовано/НЕ_ТРОГАТЬ`, устаревшим и не
совпадавшим с контрактом). Единый источник — `modules/intake/drive_structure.json`
через `python -m intake.drive_layout --root ...`, отдельным шагом (см.
`skills/install/SKILL.md`, шаг 6). Два генератора одной структуры расходились:
клиенты получали лишние пустые папки со старыми именами рядом с настоящими.

Секрет отделяется от несекрета по флагу secret из плана манифестов (config_questions).
Ключи канонизируются в lower_snake_case. Секреты в .env кладутся в UPPER_SNAKE —
так их читают и легаси-модули (os.environ), и новый factory_config.get_secret().

Вопросы-ВЫБОР ("type": "choice") валидируются: записывается только значение из списка
"choices"; чужое значение НЕ пишется (модуль возьмёт свой дефолт — как «без ответа»).
Обработка обобщённая: любой модуль может объявить choice-вопрос, хардкода под конкретный
вопрос нет. Режим --show-choices печатает варианты с русскими подписями (для мастера).

Вход:
    --answers answers.json   {"<canon_key>": "<значение>", ...}  (в т.ч. gdrive_root)
    --plan    plan.json      вывод collect_manifests.py --json (для флагов secret и folders)
    --root    PATH           корень установки (по умолчанию авто)
    --show-choices           показать choice-вопросы плана с подписями и выйти (не пишет)

Печатает по-русски, что сделано. Секреты НЕ печатает. Только stdlib.
"""
from __future__ import annotations

import argparse
import json
import os
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
        p = Path(explicit).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p
    here = Path(__file__).resolve().parent
    for cand in (here, *here.parents):
        for marker in (".factory_root", "modules", "CONTRACT.md"):
            if (cand / marker).exists():
                return cand
    return here.parent if here.name == "install" else Path.cwd()


def secret_keys(plan: dict) -> set[str]:
    out = set()
    for q in plan.get("config_questions", []) or []:
        if q.get("secret"):
            out.add(str(q.get("key", "")).strip().lower())
    return out


# ── Вопросы-ВЫБОР (type=choice) ──────────────────────────────────────────────
#
# Человеко-читаемые подписи к машинным значениям — на стороне УСТАНОВЩИКА, чтобы
# не редактировать чужие манифесты (CONTRACT.md: install-слой владеет OWNER).
# Ключи словаря — сами значения (общеупотребимые периоды), а не привязка к
# конкретному вопросу, поэтому маппинг остаётся модуль-агностичным: подойдёт
# любому модулю, который использует такие же значения. Значения, которых тут нет,
# показываются как есть. Приоритет подписи см. в label_for().
CHOICE_LABELS = {
    "daily": "каждый день (по каждому посту)",
    "week": "на неделю вперёд",
    "2weeks": "на 2 недели вперёд",
    "3weeks": "на 3 недели вперёд",
    "month": "на месяц вперёд",
}


def choice_specs(plan: dict) -> dict[str, list[str]]:
    """{канон_ключ: [допустимые значения]} для вопросов type=choice.

    Обобщённо по всем модулям: ключ — из type=="choice" вопроса, список — его
    поле choices. Вопросы без choices пропускаем (нечего валидировать).
    """
    out: dict[str, list[str]] = {}
    for q in plan.get("config_questions", []) or []:
        if str(q.get("type", "")).strip().lower() != "choice":
            continue
        key = str(q.get("key", "")).strip().lower()
        choices = [str(c) for c in (q.get("choices") or [])]
        if key and choices:
            out[key] = choices
    return out


def label_for(question: dict, value: str) -> str:
    """Русская подпись к значению choice.

    Приоритет: 1) поле choice_labels самого вопроса (если модуль его задал — мы
    манифесты не редактируем, но поддерживаем поле на будущее); 2) общий словарь
    установщика CHOICE_LABELS; 3) само значение (когда подписи нет).
    """
    labels = question.get("choice_labels") or {}
    if isinstance(labels, dict) and value in labels:
        return str(labels[value])
    return CHOICE_LABELS.get(value, value)


def render_choice_question(question: dict) -> str:
    """Как мастер предъявляет choice-вопрос человеку (для скилла /install и замера).

    Печатает ask и пронумерованный список вариантов: русская подпись + [машинное
    значение], которое и надо записать в ответы.
    """
    lines = [str(question.get("ask", "")).strip()]
    for i, value in enumerate(question.get("choices") or [], 1):
        label = label_for(question, str(value))
        if label != str(value):
            lines.append(f"  {i}) {label} [{value}]")
        else:
            lines.append(f"  {i}) {value}")
    if not question.get("required", True):
        lines.append("  (можно пропустить — тогда возьмём значение по умолчанию)")
    return "\n".join(lines)


def split_valid_choices(
    public: dict, choices_map: dict[str, list[str]]
) -> tuple[dict, list[tuple[str, str, list[str]]]]:
    """Отсеять choice-ответы, значение которых не из списка choices.

    Возвращает (валидные_public, отклонённые). Невалидное значение НЕ пишем —
    модуль возьмёт свой дефолт (та же семантика, что «без ответа»). Ответы на
    string/int/secret (нет в choices_map) проходят без изменений — обратная
    совместимость.
    """
    valid: dict = {}
    rejected: list[tuple[str, str, list[str]]] = []
    for k, v in public.items():
        ck = k.strip().lower()
        allowed = choices_map.get(ck)
        if allowed is None:
            valid[k] = v            # не choice — как раньше
        elif str(v) in allowed:
            valid[k] = v            # выбор из списка — принимаем
        else:
            rejected.append((k, str(v), allowed))
    return valid, rejected


def write_config(root: Path, public: dict) -> Path:
    """Записать НЕсекретные ответы, СЛИВ с уже сохранёнными.

    Установка поэтапная: setup_config вызывается на каждом этапе. Нельзя затирать
    конфиг прошлых этапов (напр. gdrive_root с этапа 1) — поэтому читаем существующий
    и дополняем его ответами текущего этапа.
    """
    cfg_dir = root / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = cfg_dir / "factory.config.json"
    merged: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(existing, dict):
                merged.update(existing)
        except (OSError, json.JSONDecodeError):
            pass
    merged.update(public)
    path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path


# ── Фирменный шаблон каруселей ───────────────────────────────────────────────
#
# Шаблон = «семейство:акцент» (white:mint). Живёт ключом carousel_template в
# том же factory.config.json, что и прочие ответы, но пишется/снимается ОТДЕЛЬНО
# от мастера: клиент меняет оформление среди работы, а не на установке.
#
# Почему отдельный режим, а не «просто ответ»: обычный путь записи (--answers)
# физически не умеет СНЯТЬ ключ — пустые значения отсеиваются на входе (см. main),
# а write_config только сливает. Без этого «верни исходный стиль каруселей»
# нечем выполнить: сбросить можно лишь подменой значения на другое, что и
# приводило к «команда отработала, а вид не изменился».
CAROUSEL_KEY = "carousel_template"


def carousel_registry(root: Path) -> dict:
    """Реестр шаблонов styles/carousel_templates.json (или {}, если его нет)."""
    p = root / "styles" / "carousel_templates.json"
    if not p.is_file():
        # чужой --root в деве: styles/ лежит рядом с install/
        p = Path(__file__).resolve().parent.parent / "styles" / "carousel_templates.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def parse_carousel_template(value: str, reg: dict) -> tuple[str, str] | None:
    """«семейство:акцент» или один цвет → (семейство, акцент). Мусор → None."""
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    families = reg.get("families") or {}
    accents = [str(a) for a in (reg.get("accents") or [])]
    by_accent = reg.get("_default_family_by_accent") or {}
    if ":" in raw:
        family, _, accent = raw.partition(":")
    else:
        accent, family = raw, str(by_accent.get(raw, "") or "")
    family, accent = family.strip(), accent.strip()
    if family not in families or accent not in accents:
        return None
    return family, accent


def _rewrite_json(path: Path, mutate) -> bool:
    """Прочитать json-словарь, применить mutate(dict), записать. True — изменилось.

    Нет файла или он битый — считаем, что менять нечего (сброс на голой машине
    обязан отработать как успех, а не как ошибка).
    """
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    before = json.dumps(data, ensure_ascii=False, sort_keys=True)
    mutate(data)
    if json.dumps(data, ensure_ascii=False, sort_keys=True) == before:
        return False
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
                    encoding="utf-8")
    return True


def drop_key_everywhere(root: Path, key: str) -> list[str]:
    """Снять ключ и из конфига, и из памяти ответов мастера.

    Обе стороны обязательны: factory_config.answer() читает конфиг, а при пустом
    значении падает в config/answers.memory.json (BOX-17). Снимешь только в
    конфиге — значение воскреснет из памяти, и сброс молча не подействует.
    """
    ck = key.strip().lower()
    changed = []
    targets = (("config/factory.config.json", root / "config" / "factory.config.json"),
               ("config/answers.memory.json", root / "config" / "answers.memory.json"))
    for label, path in targets:
        if _rewrite_json(path, lambda d: [d.pop(k) for k in list(d)
                                          if k.strip().lower() == ck]):
            changed.append(label)
    return changed


def write_env(root: Path, secrets: dict) -> Path | None:
    if not secrets:
        return None
    path = root / ".env"
    existing: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                existing[k.strip()] = v.strip()
    # Секреты храним в UPPER_SNAKE — их читают легаси-модули и get_secret().
    for k, v in secrets.items():
        existing[k.strip().upper()] = str(v)
    body = "# Секреты «Контент-завода». НЕ коммитить. Правит только установщик.\n"
    body += "\n".join(f"{k}={v}" for k, v in sorted(existing.items())) + "\n"
    path.write_text(body, encoding="utf-8")
    try:
        os.chmod(path, 0o600)   # на Windows no-op, но на Unix обязателен
    except OSError:
        pass
    return path


def ensure_marker(root: Path) -> None:
    (root / ".factory_root").write_text(
        "Маркер корня установки «Контент-завода». Не удалять.\n", encoding="utf-8"
    )


def make_folders(root: Path, folders: list[str]) -> list[Path]:
    made = []
    for rel in folders:
        rel = str(rel).strip().strip("/\\")
        if not rel:
            continue
        p = (root / rel)
        p.mkdir(parents=True, exist_ok=True)
        made.append(p)
    return made


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Запись конфига и создание папок.")
    ap.add_argument("--answers",
                    help="JSON с ответами клиента (отсутствующий файл = пустые ответы).")
    ap.add_argument("--plan", help="JSON плана (collect_manifests --json).")
    ap.add_argument("--root", help="Корень установки (по умолчанию авто).")
    ap.add_argument("--show-choices", action="store_true",
                    help="Показать choice-вопросы плана с русскими подписями и выйти "
                         "(ничего не пишет; --answers не нужен).")
    ap.add_argument("--set-carousel-template", metavar="СЕМЕЙСТВО:АКЦЕНТ",
                    help="Запомнить фирменный шаблон каруселей (например white:mint "
                         "или просто mint). Ни --plan, ни --answers не нужны.")
    ap.add_argument("--reset-carousel-style", action="store_true",
                    help="Забыть фирменный шаблон каруселей: завод вернётся к тому "
                         "состоянию, с которого начинает новая установка. Остальных "
                         "настроек не касается.")
    args = ap.parse_args(argv)

    # ── Оформление каруселей: отдельные режимы, мимо мастера ──
    # Клиент меняет вид среди работы («верни исходный стиль каруселей»), а не на
    # установке, поэтому плана и файла ответов тут нет — одна команда целиком.
    if args.reset_carousel_style or args.set_carousel_template:
        if args.reset_carousel_style and args.set_carousel_template:
            ap.error("--reset-carousel-style и --set-carousel-template — "
                     "взаимоисключающие режимы.")
        root = find_root(args.root)
        if args.set_carousel_template:
            reg = carousel_registry(root)
            parsed = parse_carousel_template(args.set_carousel_template, reg)
            if not parsed:
                fams = ", ".join(sorted((reg.get("families") or {}).keys())) or "—"
                accs = ", ".join(sorted(str(a) for a in (reg.get("accents") or []))) or "—"
                print(f"Не знаю такого шаблона: {args.set_carousel_template!r}. "
                      f"Семейства: {fams}. Акценты: {accs}.")
                return 2
            family, accent = parsed
            ensure_marker(root)
            cfg_path = write_config(root, {CAROUSEL_KEY: f"{family}:{accent}"})
            title = str((reg["families"][family]).get("title", family))
            print(f"Фирменный шаблон каруселей: {title} · {accent} "
                  f"(записан в {cfg_path.relative_to(root)}).")
            return 0
        changed = drop_key_everywhere(root, CAROUSEL_KEY)
        if changed:
            print("Фирменный шаблон каруселей снят: " + ", ".join(changed) + ".")
        else:
            print("Фирменный шаблон каруселей не был задан — и так стандартный.")
        print("Следующая карусель собирается так же, как на новой установке. "
              "Остальные настройки не тронуты.")
        return 0

    if not args.plan:
        ap.error("--plan обязателен (кроме режимов оформления каруселей).")

    plan_raw = json.loads(Path(args.plan).read_text(encoding="utf-8-sig"))
    plan = plan_raw.get("plan", plan_raw)

    # Режим предъявления вариантов: как мастер покажет choice-вопросы человеку.
    if args.show_choices:
        qs = [q for q in (plan.get("config_questions", []) or [])
              if str(q.get("type", "")).strip().lower() == "choice"]
        if not qs:
            print("В этом плане нет вопросов-выбора (type=choice).")
        for q in qs:
            print(render_choice_question(q))
            print()
        return 0

    if not args.answers:
        ap.error("--answers обязателен (кроме режима --show-choices).")

    root = find_root(args.root)
    ans_path = Path(args.answers)
    # Этап без вопросов может не создавать файл ответов — трактуем как пустой.
    answers = json.loads(ans_path.read_text(encoding="utf-8-sig")) if ans_path.exists() else {}

    sk = secret_keys(plan)
    public = {k: v for k, v in answers.items()
              if k.strip().lower() not in sk and v not in (None, "")}
    secrets = {k: v for k, v in answers.items()
               if k.strip().lower() in sk and v not in (None, "")}

    # Валидация вопросов-ВЫБОР: пишем только значение из choices; чужое отбрасываем
    # (модуль возьмёт дефолт). Обобщённо — по любому choice-вопросу плана.
    choices_map = choice_specs(plan)
    public, rejected_choices = split_valid_choices(public, choices_map)

    ensure_marker(root)
    cfg_path = write_config(root, public)
    env_path = write_env(root, secrets)
    folders = make_folders(root, plan.get("folders", []))

    print(f"Корень установки: {root}")
    for k, _v, allowed in rejected_choices:
        # Значение не из списка — не записано (не секрет, показать безопасно).
        print(f"Ответ на «{k}» не из предложенных вариантов — не записан, "
              f"модуль возьмёт значение по умолчанию. "
              f"Допустимые варианты: {', '.join(allowed)}.")
    print(f"Записан конфиг: {cfg_path.relative_to(root)} "
          f"(несекретных значений: {len(public)})")
    if env_path:
        print(f"Секреты записаны в .env (значения не показываю): ключей {len(secrets)}")
    else:
        print("Секретов не задано — модули пойдут в урезанном режиме, где нужен ключ.")
    print(f"Создано папок модулей: {len(folders)}")
    if public.get("gdrive_root"):
        print("Структуру Google Диска создаёт отдельный шаг — "
              "python -m intake.drive_layout (см. SKILL.md, шаг 6).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

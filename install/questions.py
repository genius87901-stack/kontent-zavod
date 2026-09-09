# -*- coding: utf-8 -*-
"""
questions — контракт вопросов установки: клиент ВЫБИРАЕТ, а не печатает (BOX-17).

Зачем. Раньше мастер /install спрашивал клиента свободным текстом («сколько постов
в неделю?», «имя файла книги смыслов?»). Нетехнический человек в такой формулировке
угадывает, а не отвечает. Этот модуль приводит ВСЕ вопросы установки к одному виду:
пронумерованные варианты, дефолт помечен, у каждого варианта сказано ПРОСТЫМИ СЛОВАМИ,
что будет, если его выбрать, и всегда есть «не знаю / решу позже». Свободный ввод
остаётся только там, где иначе нельзя (ключ доступа, имя файла, путь к папке) — и то
он спрятан ЗА выбором «у меня есть / введу своё».

Где что лежит:
    install/questions.overlay.json  — варианты, подписи и последствия (правит OWNER);
                                      манифесты модулей при этом НЕ трогаются;
    install/questions.py            — сборка вопросов и предъявление их мастеру;
    config/answers.memory.json      — что клиент отвечал в прошлый раз (без секретов).

Контракт вопроса (json-схема) — python install/questions.py --schema.

CLI:
    python install/questions.py --schema
    python install/questions.py --plan install/.plan.json --render     # как увидит клиент
    python install/questions.py --plan install/.plan.json --json       # для мастера
    python install/questions.py --plan install/.plan.json --only-keys upload_post_api_key,vk_user_token
    python install/questions.py --styles                               # пресеты оформления
    python install/questions.py --previous                             # прошлые ответы
    python install/questions.py --remember install/.answers.json       # запомнить ответы

Ничего не публикует, в сеть не ходит, секреты не печатает и не запоминает.
Только stdlib. Кроссплатформенно (Windows — основная ОС клиента, macOS — вторая).
"""
from __future__ import annotations

import argparse
import json
import platform
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



SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Вопрос установки «Контент-завода» (контракт v1)",
    "type": "object",
    "required": ["id", "key", "ask", "input", "options"],
    "properties": {
        "id": {"type": "string",
               "description": "Устойчивый идентификатор вопроса (== key)."},
        "key": {"type": "string",
                "description": "Канонический ключ ответа (lower_snake_case). "
                               "Под ним ответ ложится в конфиг или .env."},
        "ask": {"type": "string", "description": "Сам вопрос, по-русски, без жаргона."},
        "why": {"type": "string",
                "description": "Зачем спрашиваем — одна фраза простыми словами."},
        "help": {"type": "string", "description": "Подсказка «где взять» из манифеста."},
        "input": {"enum": ["choice", "multi_choice", "choice_or_text", "text"],
                  "description": "choice — только выбор; multi_choice — можно несколько; "
                                 "choice_or_text — выбор, где один из вариантов открывает "
                                 "свободный ввод; text — свободный ввод (крайний случай)."},
        "secret": {"type": "boolean",
                   "description": "Значение уходит в .env, в переписке не показывается."},
        "required": {"type": "boolean",
                     "description": "false — вопрос можно пропустить без последствий "
                                    "для установки."},
        "source": {"type": "string",
                   "description": "Откуда вопрос: module:<имя> или wizard."},
        "previous": {"description": "Что клиент ответил в прошлую установку (null — не "
                                    "отвечал). Мастер предлагает это значение первым."},
        "join": {"type": "string",
                 "description": "Только для multi_choice: чем склеить выбранные значения."},
        "options": {
            "type": "array",
            "description": "Варианты ответа. Ровно один помечен default.",
            "items": {
                "type": "object",
                "required": ["value", "label", "consequence"],
                "properties": {
                    "value": {"description": "Машинное значение ответа. null — ничего не "
                                             "записывать (модуль возьмёт свой дефолт). "
                                             "'__enter__' — открыть свободный ввод."},
                    "label": {"type": "string", "description": "Короткая подпись варианта."},
                    "consequence": {"type": "string",
                                    "description": "Что будет, если выбрать — простым языком."},
                    "default": {"type": "boolean", "description": "Вариант по умолчанию."},
                    "recommended": {"type": "boolean", "description": "Что советуем."},
                    "free_text": {"type": "boolean",
                                  "description": "Выбор открывает свободный ввод."},
                    "skip": {"type": "boolean", "description": "Вариант «пропустить»."},
                    "later": {"type": "boolean",
                              "description": "«Решу позже» — вопрос будет задан снова "
                                             "при следующей установке."},
                },
            },
        },
        "skipped_effect": {"type": "string",
                           "description": "Что будет, если вопрос вообще пропустить."},
    },
}

ENTER = "__enter__"          # значение-маркер «открыть свободный ввод»
LATER = "__later__"          # так помечается отложенный вопрос в памяти ответов


# ── корень и файлы ───────────────────────────────────────────────────────────
def find_root(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    here = Path(__file__).resolve().parent
    for cand in (here, *here.parents):
        for marker in (".factory_root", "modules", "CONTRACT.md"):
            if (cand / marker).exists():
                return cand
    return here.parent if here.name == "install" else Path.cwd()


def canon(key: str) -> str:
    return str(key).strip().lower().replace("-", "_")


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return default


def overlay(root: Path) -> dict:
    """Описания вариантов. Файл ЕДЕТ РЯДОМ С КОДОМ, поэтому ищем и у корня
    установки, и рядом со скриптом (второе спасает, когда --root указывает на
    чужую папку: сами описания от этого не меняются)."""
    for cand in (root / "install" / "questions.overlay.json",
                 Path(__file__).resolve().parent / "questions.overlay.json"):
        data = read_json(cand, None)
        if isinstance(data, dict) and data:
            return data
    return {}


def memory_path(root: Path) -> Path:
    return root / "config" / "answers.memory.json"


def load_memory(root: Path) -> dict:
    """Ответы прошлой установки. Секретов тут нет по построению."""
    data = read_json(memory_path(root), {})
    return data if isinstance(data, dict) else {}


def stored_secret_keys(root: Path) -> set[str]:
    """ИМЕНА секретов, которые уже лежат в .env. Значения не читаем и не печатаем.

    Нужно, чтобы при повторной установке не переспрашивать ключ, который клиент
    уже дал: мастер просто скажет «ключ уже сохранён, менять?». Смотрим только
    .env установки: переменные окружения текущей оболочки к коробке отношения
    не имеют и ложно «зачесть» ключ не должны.
    """
    out = set()
    p = root / ".env"
    if not p.exists():
        return out
    try:
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                name, val = line.split("=", 1)
                if val.strip():
                    out.add(canon(name))
    except OSError:
        pass
    return out


def previous_answers(root: Path) -> dict:
    """Что клиент уже отвечал: память мастера + уже записанный конфиг.

    Конфиг (config/factory.config.json) — источник правды по несекретным ответам:
    его пишет setup_config на каждом этапе. Память мастера добавляет к нему то,
    чего в конфиге нет: отложенные вопросы (__later__) и ответы, которые модулям
    не нужны (например выбранный пресет оформления до появления модуля стиля).
    """
    out = {}
    cfg = read_json(root / "config" / "factory.config.json", {})
    if isinstance(cfg, dict):
        out.update({canon(k): v for k, v in cfg.items()})
    out.update({canon(k): v for k, v in load_memory(root).items()})
    return out


def remember(root: Path, answers: dict, secret_keys: set[str],
             deferred: set[str] | None = None) -> Path:
    """Запомнить НЕсекретные ответы, чтобы не спрашивать заново.

    Секреты сюда не попадают никогда — они живут только в .env. Отложенные
    вопросы («не знаю / решу позже») передаются отдельным списком deferred и
    помечаются '__later__': ответа нет, но при следующей установке мастер спросит
    снова, а не подставит молча дефолт. В сами ответы (.answers.json) '__later__'
    попадать НЕ должен — иначе он уедет в конфиг или .env как настоящее значение.
    """
    mem = load_memory(root)
    for k, v in (answers or {}).items():
        ck = canon(k)
        if ck in secret_keys or v in (None, "", ENTER, LATER):
            continue
        mem[ck] = v
    for k in (deferred or ()):
        ck = canon(k)
        if ck and ck not in (answers or {}):
            mem[ck] = LATER
    p = memory_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(mem, ensure_ascii=False, indent=2, sort_keys=True),
                 encoding="utf-8")
    return p


# ── динамические варианты ────────────────────────────────────────────────────
def style_presets(root: Path) -> list[dict]:
    """Пресеты оформления — читаются из папки styles/ ДИНАМИЧЕСКИ, без хардкода.

    В styles/ лежат три вида файлов: базовый default.json, бренд-пресеты
    preset_*.json и стили субтитров subtitle_*.json. Служебные справочники
    (без поля name, напр. platform_zones.json) в список не попадают.
    """
    d = root / "styles"
    if not d.is_dir():
        # styles/ едет в коробке рядом с install/ — подстраховка для чужого --root.
        d = Path(__file__).resolve().parent.parent / "styles"
    if not d.is_dir():
        return []
    kinds = {"preset": "оформление бренда", "subtitle": "стиль субтитров",
             "base": "базовое оформление"}
    out = []
    for p in sorted(d.glob("*.json")):
        data = read_json(p, {})
        if not isinstance(data, dict) or not data.get("name"):
            continue        # справочник, а не стиль
        stem = p.stem
        kind = "base" if stem == "default" else stem.split("_", 1)[0]
        note = str(data.get("_note") or "").split(". ")[0].strip()
        if stem == "default":
            consequence = "Оформление движка по умолчанию — то, как выглядят наши примеры."
        else:
            fields = [f for f in ("font_family", "sub_style", "karaoke", "safe_zone",
                                  "accent") if f in data]
            human = {
                "font_family": "свой шрифт",
                "sub_style": "свой вид подписей под видео",
                "karaoke": "подсветка слов в субтитрах",
                "safe_zone": "свои отступы от краёв экрана",
                "accent": "свой акцентный цвет",
            }
            what = ", ".join(human[f] for f in fields) or "точечная настройка"
            consequence = (note or f"Меняет: {what}.")
            if not consequence.endswith("."):
                consequence += "."
            consequence += " Остальное берётся из базового оформления."
        out.append({
            "value": str(data["name"]),
            "file": f"styles/{p.name}",
            "kind": kinds.get(kind, "оформление"),
            "label": f"{data['name']} — {kinds.get(kind, 'оформление')}",
            "consequence": consequence,
            "default": stem == "default",
        })
    return out


def gdrive_candidates() -> list[dict]:
    """Найденные на машине папки Google Диска. Ничего не создаём, только смотрим."""
    home = Path.home()
    cands: list[Path] = []
    # macOS: новый Google Drive монтируется в CloudStorage; старый — в домашней папке.
    cloud = home / "Library" / "CloudStorage"
    if cloud.is_dir():
        for p in sorted(cloud.glob("GoogleDrive-*")):
            for inner in ("Мой диск", "My Drive"):
                if (p / inner).is_dir():
                    cands.append(p / inner)
            if not any(str(c).startswith(str(p)) for c in cands):
                cands.append(p)
    # Windows и старые версии клиента: папка в профиле пользователя.
    for name in ("Google Drive", "GoogleDrive", "Google Диск", "Мой диск", "My Drive"):
        p = home / name
        if p.is_dir():
            cands.append(p)
    # Windows: отдельная буква диска (G: по умолчанию у Google Drive for desktop).
    if platform.system().lower() == "windows":
        for letter in ("G", "H"):
            for inner in ("Мой диск", "My Drive"):
                p = Path(f"{letter}:/") / inner
                try:
                    if p.is_dir():
                        cands.append(p)
                except OSError:
                    pass
    seen, out = set(), []
    for p in cands:
        s = str(p)
        if s in seen:
            continue
        seen.add(s)
        out.append({
            "value": s,
            "label": f"Эта: {p.name} ({p.parent.name})",
            "consequence": "Внутри неё заведу папку «КонтентЗавод», а в ней — "
                           "«1 Сырьё», «2 На утверждение», «3 Готово к публикации», "
                           "«4 Опубликовано», «5 Смыслы и настройки», «НЕ_ТРОГАТЬ».",
        })
    # Нашли папку — она и предлагается по умолчанию (клиенту не надо вспоминать путь).
    if out:
        out[0]["default"] = True
    return out


DYNAMIC = {
    "style_presets": lambda root: style_presets(root),
    "gdrive_candidates": lambda root: gdrive_candidates(),
}


# ── сборка вопросов ──────────────────────────────────────────────────────────
def _norm_option(o: dict) -> dict:
    out = {"value": o.get("value"),
           "label": str(o.get("label", "")),
           "consequence": str(o.get("consequence", ""))}
    for flag in ("default", "recommended", "free_text", "skip", "later"):
        if o.get(flag):
            out[flag] = True
    if o.get("file"):
        out["file"] = o["file"]
    return out


def build_question(q: dict, ov: dict, root: Path, prev: dict,
                   have_secrets: set[str] | None = None) -> dict:
    """Один вопрос манифеста → вопрос-ВЫБОР по контракту."""
    key = canon(q.get("key", ""))
    qtype = str(q.get("type", "string")).strip().lower()
    is_secret = bool(q.get("secret"))

    # Приоритет описаний: свой вопрос мастера → запись by_key → шаблон по типу.
    # Мастерский спек побеждает и для ключа, который пришёл из манифеста модуля
    # (так gdrive_root получает найденные на машине папки, а не «введите строку»).
    spec = ((ov.get("wizard_questions") or {}).get(key)
            or (ov.get("by_key") or {}).get(key))
    if spec is None:
        tmpl = (ov.get("defaults_by_type") or {})
        spec = dict(tmpl.get("secret" if is_secret else qtype)
                    or tmpl.get("string") or {})
    spec = dict(spec)

    options = [_norm_option(o) for o in (spec.get("options") or [])]

    # type=choice из манифеста без своей записи в overlay: строим варианты из choices,
    # чтобы новый модуль сразу получил выбор, а не свободный ввод.
    if not options and qtype == "choice":
        for c in q.get("choices") or []:
            options.append({"value": str(c), "label": str(c),
                            "consequence": "Вариант из списка модуля."})
        if options:
            options[0]["default"] = True

    # динамические варианты (пресеты оформления, найденные папки Google Диска)
    dyn = spec.get("dynamic")
    if dyn in DYNAMIC:
        options = [_norm_option(o) for o in DYNAMIC[dyn](root)] + options

    # Совсем нет вариантов (новый ключ, о котором слой установки ещё не знает) —
    # тогда хотя бы «ввести / пропустить», но всё равно выбором.
    if not options:
        options = [
            {"value": ENTER, "label": "Ввести своё значение",
             "consequence": "Будет использовано ваше значение.", "free_text": True},
            {"value": None, "label": "Пропустить",
             "consequence": "Возьмём значение по умолчанию.", "default": True,
             "skip": True},
        ]

    # «не знаю / решу позже» — всегда, если своего «позже» нет
    if not any(o.get("later") for o in options):
        options.append(_norm_option(ov.get("skip_option") or {
            "value": None, "label": "Не знаю / решу позже",
            "consequence": "Возьмём значение по умолчанию, вопрос отложим.",
            "later": True}))

    # Ровно один дефолт: первый помеченный побеждает, лишние пометки снимаем.
    # У multi_choice дефолтов законно несколько (напр. две площадки сразу).
    marked = [o for o in options if o.get("default")]
    if str(spec.get("input", "")) == "multi_choice":
        if not marked:
            options[0]["default"] = True
    elif marked:
        for o in marked[1:]:
            o.pop("default", None)
    else:
        for o in options:
            if o.get("skip") or o.get("later"):
                o["default"] = True
                break
        else:
            options[0]["default"] = True

    # Прошлый ответ становится вариантом по умолчанию: при повторной установке
    # клиент просто соглашается, а не выбирает всё заново.
    prev_here = prev.get(key)
    if prev_here not in (None, "", LATER):
        for o in options:
            if str(o.get("value")) == str(prev_here):
                for other in options:
                    other.pop("default", None)
                o["default"] = True
                o["previous"] = True
                break

    inp = spec.get("input")
    if not inp:
        inp = "choice_or_text" if any(o.get("free_text") for o in options) else "choice"

    prev_val = prev.get(key)
    question = {
        "id": key,
        "key": key,
        # Вопрос мастера формулируем сами (единый тон «вы»), для модульных
        # вопросов берём формулировку манифеста.
        "ask": str(spec.get("ask") or q.get("ask") or key),
        "why": str(spec.get("why") or ""),
        "input": inp,
        "secret": is_secret,
        "required": bool(q.get("required", False)),
        "source": q.get("_source", "module"),
        "previous": None if prev_val in (None, "", LATER) else prev_val,
        "previous_deferred": prev_val == LATER,
        "already_set": bool(is_secret and (have_secrets or set()) and
                            key in (have_secrets or set())),
        "options": options,
        "skipped_effect": next(
            (o["consequence"] for o in options if o.get("default")), ""),
    }
    if spec.get("join"):
        question["join"] = spec["join"]
    if q.get("help"):
        question["help"] = q["help"]
    return question


def wizard_questions(root: Path, ov: dict, prev: dict, keys: list[str],
                     have_secrets: set[str] | None = None) -> list[dict]:
    """Собственные вопросы мастера (не из манифестов модулей)."""
    src = ov.get("wizard_questions") or {}
    out = []
    for key in keys:
        spec = src.get(key)
        if not isinstance(spec, dict):
            continue
        fake = {"key": key, "ask": spec.get("ask", key), "type": "string",
                "required": False, "_source": "wizard"}
        ov2 = dict(ov)
        ov2["by_key"] = dict(ov.get("by_key") or {})
        ov2["by_key"][key] = spec
        out.append(build_question(fake, ov2, root, prev, have_secrets))
    return out


#: Ключи, которые мастер НЕ СПРАШИВАЕТ И НЕ ЗАПИСЫВАЕТ ни при каких условиях.
#:
#: 08.09.2026. Прямой ключ ВК добывался входом через чужое мобильное приложение,
#: и этот вход запрещён: после него ВКонтакте блокирует аккаунт клиента целиком,
#: а снять блокировку может только сам владелец аккаунта. Одного запрета в
#: инструкциях мало — у клиентов на руках старые письма и лист самопроверки,
#: которые ведут «подключи ВК». Пока мастер готов записать ключ, запрет
#: обходится в один вопрос. Здесь он не обходится.
#:
#: Ключ, полученный РАНЬШЕ, продолжает читаться из `.env`: замок стоит на
#: ЗАПИСИ нового, а не на чтении старого — чужие настройки не выдёргиваем.
BLOCKED_KEYS = {"vk_user_token_kate", "vk_stories_token", "vk_user_token"}


def collect(root: Path, plan: dict, only_keys: set[str] | None,
            with_wizard: list[str]) -> list[dict]:
    ov = overlay(root)
    prev = previous_answers(root)
    have_secrets = stored_secret_keys(root)
    out = []
    seen = set()
    for q in plan.get("config_questions", []) or []:
        key = canon(q.get("key", ""))
        if not key or key in seen:
            continue
        if key in BLOCKED_KEYS:
            # Молча пропустить нельзя: вопрос вернули осознанно, и тот, кто это
            # сделал, должен увидеть причину, а не гадать, куда делся вопрос.
            print("  ⚠️ вопрос «%s» пропущен: ключ запрещён к настройке "
                  "(прямой вход в ВК блокирует аккаунт клиента). "
                  "ВКонтакте подключается через smmbox_connect.py" % key)
            continue
        if only_keys is not None and key not in only_keys:
            continue
        seen.add(key)
        out.append(build_question(q, ov, root, prev, have_secrets))
    for wq in wizard_questions(root, ov, prev, with_wizard, have_secrets):
        if wq["key"] not in seen:
            seen.add(wq["key"])
            out.append(wq)
    return out


# ── предъявление человеку ────────────────────────────────────────────────────
def render(questions: list[dict]) -> str:
    lines = []
    for n, q in enumerate(questions, 1):
        lines.append(f"Вопрос {n} из {len(questions)}. {q['ask']}")
        if q.get("why"):
            lines.append(f"  Зачем: {q['why']}")
        if q.get("secret"):
            lines.append("  (значение сохранится скрыто и в переписке показано не будет)")
        if q.get("already_set"):
            lines.append("  Этот ключ уже сохранён с прошлой установки — можно ничего "
                         "не вводить. Сказать «поменять ключ» — спрошу заново.")
        if q.get("previous"):
            lines.append(f"  В прошлый раз вы выбрали: {q['previous']} — "
                         f"оставляем? Можно поменять.")
        elif q.get("previous_deferred"):
            lines.append("  В прошлый раз вы отложили этот вопрос — спрашиваю снова.")
        if q["input"] == "multi_choice":
            lines.append("  Можно выбрать несколько.")
        for i, o in enumerate(q["options"], 1):
            marks = []
            if o.get("default"):
                marks.append("по умолчанию")
            if o.get("recommended"):
                marks.append("советую")
            mark = f" [{', '.join(marks)}]" if marks else ""
            lines.append(f"  {i}) {o['label']}{mark}")
            if o.get("consequence"):
                lines.append(f"     → {o['consequence']}")
        lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Вопросы установки в формате ВЫБОРА (BOX-17).")
    ap.add_argument("--root")
    ap.add_argument("--plan", help="JSON плана (collect_manifests.py --json).")
    ap.add_argument("--only-keys", help="Только эти ключи, через запятую "
                                        "(напр. после выбора соцсетей).")
    ap.add_argument("--wizard", default="gdrive_root,brand_preset",
                    help="Собственные вопросы мастера через запятую "
                         "(пустая строка — без них).")
    ap.add_argument("--render", action="store_true", help="Как это увидит клиент.")
    ap.add_argument("--json", action="store_true", help="Машинный вид для мастера.")
    ap.add_argument("--schema", action="store_true", help="Контракт вопроса (json-схема).")
    ap.add_argument("--styles", action="store_true",
                    help="Пресеты оформления из styles/ (динамически).")
    ap.add_argument("--previous", action="store_true",
                    help="Что клиент отвечал в прошлые установки (без секретов).")
    ap.add_argument("--remember", help="Запомнить ответы из этого JSON (без секретов).")
    ap.add_argument("--deferred", default="",
                    help="Ключи, которые клиент отложил («не знаю / решу позже»), "
                         "через запятую. В сами ответы их писать НЕЛЬЗЯ — только сюда.")
    args = ap.parse_args(argv)

    if args.schema:
        print(json.dumps(SCHEMA, ensure_ascii=False, indent=2))
        return 0

    root = find_root(args.root)

    if args.styles:
        pres = style_presets(root)
        if not pres:
            print("Папки styles/ нет или в ней нет пресетов — вопрос про оформление "
                  "мастер задавать не будет.")
            return 0
        for p in pres:
            mark = " [по умолчанию]" if p.get("default") else ""
            print(f"{p['value']} ({p['kind']}, {p['file']}){mark}\n   {p['consequence']}")
        return 0

    if args.previous:
        prev = previous_answers(root)
        if not prev:
            print("Прошлых ответов нет — установка выполняется впервые.")
            return 0
        for k, v in sorted(prev.items()):
            print(f"{k} = {'(отложен, спрошу снова)' if v == LATER else v}")
        return 0

    if args.remember:
        answers = read_json(Path(args.remember), {})
        secret = set()
        if args.plan:
            plan_raw = read_json(Path(args.plan), {})
            plan = plan_raw.get("plan", plan_raw)
            secret = {canon(q.get("key", "")) for q in
                      (plan.get("config_questions") or []) if q.get("secret")}
        deferred = {canon(k) for k in args.deferred.split(",") if k.strip()}
        p = remember(root, answers, secret, deferred)
        kept = len([k for k, v in (answers or {}).items()
                    if canon(k) not in secret and v not in (None, "", ENTER, LATER)])
        print(f"Запомнено ответов: {kept} (секреты не сохраняются), "
              f"отложено вопросов: {len(deferred)}. Файл: "
              f"{p.relative_to(root) if p.is_relative_to(root) else p}")
        return 0

    if not args.plan:
        ap.error("нужен --plan (или один из режимов --schema/--styles/--previous/--remember)")

    plan_raw = read_json(Path(args.plan), {})
    plan = plan_raw.get("plan", plan_raw)
    only = None
    if args.only_keys:
        only = {canon(k) for k in args.only_keys.split(",") if k.strip()}
    wiz = [k.strip() for k in (args.wizard or "").split(",") if k.strip()]
    questions = collect(root, plan, only, wiz)

    if args.json:
        print(json.dumps({"version": 1, "count": len(questions),
                          "questions": questions}, ensure_ascii=False, indent=2))
    else:
        if not questions:
            print("У этого этапа вопросов нет — ничего спрашивать не нужно.")
        else:
            print(render(questions))
    return 0


if __name__ == "__main__":
    sys.exit(main())

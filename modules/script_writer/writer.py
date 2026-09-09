# -*- coding: utf-8 -*-
"""Сборщик СЦЕНАРИЯ СЪЁМКИ под один разговорный Reels (детерминированный).

Разворачивает одну единицу под съёмку (рубрика + тема + опц. хук/CTA — строка
контент-плана из ПРОМТ 2) в список ДУБЛЕЙ по драматургической арке из книги
смыслов (шаблон «30 сценариев прогрева»): ХУК → … → CTA с кодовым словом.

ПРОСТОЙ ДУБЛЬ (по просьбе эксперта). Каждый дубль несёт ровно то, что нужно на
съёмке:
  * say   — реплика эксперта (что сказать), голос из брифа, реальные факты-активы;
  * show  — что показать в кадре (действие + перебивка);
  * duration_sec — ориентировочная длительность;
  * subtitle  — что появится субтитром.
Раскладку монтажа и тип плана камеры из дубля УБРАЛИ — эксперту нужен простой
сценарий «сказать/показать», а не режиссёрский план. Роль в арке (`intent`
hook/point/emotion/cta) осталась служебным полем для драматургии/валидации.

ТРИ РЫЧАГА КАЧЕСТВА (без платного ИИ, всё офлайн и детерминированно):
  1. РЕАЛЬНЫЕ ФРАЗЫ КНИГИ СМЫСЛОВ. Хук и часть битов берутся из формулировок
     клиента по рубрике (`bible.themes` / банк хуков), а не только из общих
     заготовок модуля. Нет книги → мягкий фолбэк на заготовки.
  2. ВИРАЛЬНЫЕ ХУКИ РАЗВЕДКИ. Если на диске есть банк `/niche-hooks`, реально
     залетевшие первые фразы подмешиваются в пул хука через
     `content_plan.niche_bridge` (только чтение готового банка). Нет банка → тихо
     пропускаем, платный API не зовём.
  3. РОТАЦИЯ АРОК. Не одна арка на всё: несколько драматургических порядков битов,
     детерминированный выбор по теме — уходит ощущение «под копирку». Смысловая
     логика (хук → … → кейс-доказательство → CTA) сохранена.

ДЕТЕРМИНИРОВАННО: без случайности и без сети. Любой выбор из банков — по
устойчивому md5-хешу темы (НЕ builtin hash(), он рандомизирован PYTHONHASHSEED).

«Умный» шаг вынесен в хук `llm_script(...)` (по образцу planner.llm_ideas /
caption_bridge.llm_caption): по умолчанию возвращает детерминированную базу и
платный API НЕ зовёт.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import bible as bible_mod
from . import config, model

# --- нейтральная заготовка реплик -------------------------------------------
# ⚠️ Здесь НЕТ и не должно быть контента конкретного эксперта. До 08.08.2026 в
# этом месте лежал банк реплик Валерии, разложенный по её семи рубрикам, с её
# кейсом («136 точек роста») и её лексикой («бизнес не растёт», «предприниматель»).
# Он уезжал КАЖДОМУ клиенту: у астролога рубрика «Продажи» совпадала по имени —
# и в сценарий вставала её продающая реплика целиком (BUG-2026-08-08, баг-репорт
# Елены Сокольской). Заготовка теперь нейтральная и лежит в данных, а вся
# конкретика берётся ТОЛЬКО из книги смыслов самого клиента.
_LINES_FILE = "rubric_lines.default.json"


def _load_neutral() -> tuple[dict, str]:
    """(нейтральные реплики по стадиям, текст пометки для бита «кейс»)."""
    try:
        raw = (Path(__file__).resolve().parent / _LINES_FILE).read_text(encoding="utf-8-sig")
        data = json.loads(raw)
    except (OSError, ValueError):
        return {}, ""
    lines = data.get("lines")
    return (lines if isinstance(lines, dict) else {},
            str(data.get("case_placeholder") or ""))


_FALLBACK_LINES, _CASE_PLACEHOLDER = _load_neutral()

# --- спецификация стадий (постоянная часть дубля) ---------------------------
# Каждая стадия задаёт роль в арке (intent), подсказку кадра (show), визуальный
# запрос под перебивку (q) и вес для раскладки длительности. Раскладку монтажа и
# тип плана камеры сценарист больше не диктует — их в дубле нет.
_STAGE_SPEC = {
    "hook": {
        "intent": model.INTENT_HOOK, "weight": 3,
        "show": "Смотришь прямо в камеру и бьёшь хуком без паузы. Крупно, энергия с первой секунды, обрыв на интриге.",
        "q": "person speaking confidently straight to camera close up",
    },
    "pain": {
        "intent": model.INTENT_POINT, "weight": 3,
        "show": "Говоришь на камеру; следом перебивка под боль — человек наедине с собой вечером.",
        "q": "tired thoughtful person alone at home evening",
    },
    "mistake": {
        "intent": model.INTENT_POINT, "weight": 3,
        "show": "Заостряешь ключевую мысль на камеру; на экране — короткий тезис словами.",
        "q": "person looking puzzled thinking hard",
    },
    "case": {
        "intent": model.INTENT_POINT, "weight": 4,
        "show": "Рассказываешь кейс; на экране — твоя цифра или факт из этой истории.",
        "q": "person telling a story to camera warm light",
    },
    "insight": {
        "intent": model.INTENT_EMOTION, "weight": 3,
        "show": "Главная мысль — только ты в кадре, крупно, без перебивки. Держишь паузу после фразы.",
        "q": "calm focused portrait looking at camera",
    },
    "opportunity": {
        "intent": model.INTENT_POINT, "weight": 3,
        "show": "Разворачиваешь к возможности; на экране — короткий тезис-возможность.",
        "q": "person hopeful looking forward light",
    },
    "cta": {
        "intent": model.INTENT_CTA, "weight": 3,
        "show": "Прямо в камеру называешь кодовое слово. За спиной крупно само слово. Без перебивки.",
        "q": "call to action confident direct to camera",
    },
}

# --- драматургические арки (ЛЕВЕР 3: ротация, не одна арка на всё) -----------
# Каждая арка — свой ПОРЯДОК средних битов между хуком и CTA. Для ролика на N
# битов берутся первые N стадий арки. Инвариант: «case» (доказательство) стоит в
# первых трёх позициях каждой арки, поэтому при любом N (3–5) кейс в сценарии
# есть — смысловая логика хук → … → кейс → CTA сохраняется. Выбор арки —
# детерминированный по теме (md5), чтобы уходило ощущение «под копирку».
_ARCS = [
    ("classic",     ["pain", "mistake", "case", "insight", "opportunity"]),
    ("proof_first", ["case", "pain", "mistake", "insight", "opportunity"]),
    ("provocation", ["mistake", "case", "pain", "opportunity", "insight"]),
    ("story",       ["pain", "case", "insight", "mistake", "opportunity"]),
    ("contrast",    ["mistake", "pain", "case", "opportunity", "insight"]),
]


def _pick_arc(topic: str, rubric: str):
    """Детерминированно выбирает арку по теме/рубрике. Возвращает (name, order)."""
    key = ("%s|%s" % (rubric or "", topic or "")).strip("|")
    return _ARCS[_stable_index(key, len(_ARCS))]


# --- хук / cta --------------------------------------------------------------

def _stable_index(text: str, n: int) -> int:
    """Детерминированный индекс 0..n-1 из строки (md5, не builtin hash())."""
    if n <= 0:
        return 0
    digest = hashlib.md5((text or "").encode("utf-8")).hexdigest()
    return int(digest, 16) % n


def _as_hook_line(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if text and text[-1] not in ".!?…":
        text += "."
    return text


def _norm(text: str) -> str:
    """Ключ для дедупа фраз: без регистра и лишних пробелов. Оригинал не меняем."""
    return re.sub(r"\s+", " ", (text or "").strip()).casefold()


def _as_say(text: str) -> str:
    """Реальную фразу книги (часто заголовок темы) привести к виду реплики:
    один пробел, заглавная первая буква, концевая точка."""
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return ""
    if text[0].islower():
        text = text[0].upper() + text[1:]
    if text[-1] not in ".!?…":
        text += "."
    return text


def _pick_from_pool(pool: list, key: str, used: set):
    """Детерминированно взять из pool фразу, ещё не занятую (used — множество
    нормализованных фраз). Старт — устойчивый md5 от key, дальше по кругу ищем
    первую свободную и регистрируем её в used. Нет свободных → None."""
    pool = [p for p in (pool or []) if str(p).strip()]
    if not pool:
        return None
    n = len(pool)
    start = _stable_index(key, n)
    for step in range(n):
        cand = pool[(start + step) % n]
        if _norm(cand) not in used:
            used.add(_norm(cand))
            return cand
    return None


def _pick_hook(topic: str, rubric: str, pool: list, explicit_hook, used: set) -> str:
    """Первая фраза-хук. Приоритет: явный хук из брифа → пул реальных фраз
    (виральные хуки разведки + темы рубрики + банк хуков книги; сначала
    релевантные рубрике/теме, выбор устойчивым хешем темы) → сама тема."""
    if explicit_hook and str(explicit_hook).strip():
        used.add(_norm(explicit_hook))
        return _as_hook_line(explicit_hook)
    pool = list(pool or [])
    if pool:
        key = ("%s %s" % (rubric or "", topic or "")).lower()
        words = {w for w in re.split(r"\W+", key) if len(w) >= 4}
        relevant = [h for h in pool
                    if words & {w for w in re.split(r"\W+", h.lower()) if len(w) >= 4}]
        chosen = _pick_from_pool(relevant or pool, topic or rubric or "", used)
        if chosen:
            return _as_hook_line(chosen)
    if topic and str(topic).strip():
        used.add(_norm(topic))
        return _as_hook_line(topic)
    return _as_hook_line("Ты смотришь не туда")


def _cta_line(code_word: str) -> str:
    """Финальный призыв. Нейтральный: раньше здесь дословно стояло позиционирование
    Валерии («смысл и работа остаются на тебе, я забираю только производство») —
    у эксперта другой ниши это чужой оффер в собственном ролике.

    Без кодового слова призыв не выдумывается: остаётся честная пометка-задание,
    потому что слово — это то, по чему эксперт ловит заявки, и подставлять сюда
    чужое хуже, чем оставить пустое место."""
    if not code_word:
        return ("[ЗДЕСЬ ТВОЁ КОДОВОЕ СЛОВО] Назови слово, которое человек напишет "
                "тебе в личку после ролика — по нему ты поймёшь, что он пришёл отсюда.")
    return ("Если откликнулось — напиши мне в личку слово «%s», и я расскажу, "
            "как это работает у тебя." % code_word)


# --- реальные фразы клиента в средние биты (ЛЕВЕР 1) ------------------------

# Реальную фразу клиента отдаём ПОД ЛЮБОЙ средний бит, а не под два из пяти.
# Раньше «боль» и «инсайт» намеренно держались на заготовках модуля — но
# заготовки были написаны под одну нишу, и у клиента другой ниши эти два бита
# из пяти всегда звучали чужим голосом (BUG-2026-08-08). Теперь заготовка
# нейтральна и работает только как страховка, когда у клиента нет своей фразы.
_REAL_PHRASE_STAGES = {"pain", "mistake", "insight", "opportunity"}


def _individual_stats(sources: list) -> dict:
    """Доля дублей, собранных из данных клиента, и что осталось дозаполнить.

    `todo` — не провал сборки, а честная пометка: сценарист не стал подставлять
    чужой кейс/чужое кодовое слово. Такие места эксперт закрывает сам за минуту.
    """
    total = len(sources) or 1
    client = sources.count("client")
    return {
        "shots_total": len(sources),
        "from_client": client,
        "neutral": sources.count("neutral"),
        "todo": sources.count("todo"),
        "percent": int(round(100.0 * client / total)),
        "by_shot": list(sources),
    }


def _beat_say(stage: str, real_pool: list, key: str,
              used: set, bib: dict) -> tuple[str, str]:
    """(реплика бита, источник). Источник — «client» | «neutral» | «todo».

    Порядок один и тот же для всех: сначала данные клиента, потом нейтральная
    заготовка. Чужая конкретика не участвует нигде.

    «Кейс» — отдельный случай: доказательство обязано быть настоящим. Есть
    якорный кейс в книге клиента — берём его; нет — ставим ЯВНУЮ пометку-задание
    («здесь твой кейс»), а не чью-то историю. Пустое место эксперт заполнит за
    минуту, чужой кейс он произнесёт в камеру и не заметит.
    """
    if stage == "case":
        # ПРАВКА КЛИЕНТСКОЙ КОПИИ 09.09.2026 (НиколаевскТВ) — вернуть после
        # обновления (ХЕНДОФФ.md, раздел 6а). Владелец просил чередовать ВСЕ
        # кейсы книги, а не ставить один и тот же во все ролики. Логика чтения —
        # в корневом anchor_bank.py, который обновление не затирает; ротацию
        # делает штатный _pick_from_pool тем же ключом, что и остальные биты.
        try:
            import anchor_bank
            pool = anchor_bank.cases()
            if pool:
                phrase = _pick_from_pool(pool, "case|%s" % key, used)
                if phrase:
                    return _as_say(phrase), "client"
        except Exception:
            pass
        anchor = (bib.get("anchor") or "").strip()
        if anchor:
            return _as_say(anchor), "client"
        return _CASE_PLACEHOLDER, "todo"
    if stage in _REAL_PHRASE_STAGES and real_pool:
        phrase = _pick_from_pool(real_pool, "%s|%s" % (stage, key), used)
        if phrase:
            return _as_say(phrase), "client"
    return _FALLBACK_LINES.get(stage, ""), "neutral"


def _niche_hooks(source=None) -> list:
    """ЛЕВЕР 2: виральные хуки из готового банка разведки `/niche-hooks` через
    `content_plan.niche_bridge` (только чтение готового .md-банка, детерминированно).

    Возвращает список первых фраз в порядке банка. Нет банка / модуль недоступен /
    любая ошибка → [] — мягко: разведку не запускаем, платный API не зовём, ничего
    не роняем. Именно так рычаг остаётся опциональным усилением, а не зависимостью.
    """
    try:
        from content_plan import niche_bridge
    except Exception:
        return []
    try:
        triples = niche_bridge.load_triples(source)
    except Exception:
        return []
    out = []
    for t in triples or []:
        try:
            hook = str(t[1]).strip()
        except (IndexError, TypeError):
            continue
        if hook:
            out.append(hook)
    return out


# --- раскладка длительности -------------------------------------------------

def _split_durations(total: int, weights: list) -> list:
    """Раскладывает total секунд по весам в целые числа, сумма == total,
    каждый дубль >= 2 сек. Детерминированно (метод наибольших остатков)."""
    n = len(weights)
    if n == 0:
        return []
    total = max(total, 2 * n)
    wsum = float(sum(weights)) or float(n)
    raw = [total * w / wsum for w in weights]
    floors = [max(2, int(x)) for x in raw]
    remainder = total - sum(floors)
    # распределяем остаток по наибольшей дробной части (стабильный порядок)
    order = sorted(range(n), key=lambda i: (raw[i] - int(raw[i]), -i), reverse=True)
    i = 0
    while remainder > 0 and order:
        floors[order[i % n]] += 1
        remainder -= 1
        i += 1
    while remainder < 0:  # если из-за минимума 2 перебрали — снимаем с крупных
        big = max(range(n), key=lambda k: floors[k])
        if floors[big] <= 2:
            break
        floors[big] -= 1
        remainder += 1
    return floors


def _subtitle(say: str, limit: int = 62) -> str:
    """Что появится субтитром — первая мысль реплики, коротко."""
    first = re.split(r"(?<=[.!?…])\s", (say or "").strip(), 1)[0].strip()
    first = re.sub(r"\s+", " ", first)
    if len(first) > limit:
        first = first[:limit].rsplit(" ", 1)[0].strip() + "…"
    return first


# --- ХУК РАСШИРЕНИЯ (LLM) ----------------------------------------------------

def llm_script(brief: dict, base_script: dict, generate=None) -> dict:
    """Точка расширения сценария «умным» шагом (LLM).

    КОНТРАКТ РЕЛЬСОВ (как planner.llm_ideas / caption_bridge.llm_caption): сама
    функция НЕ ходит в сеть и НЕ зовёт платный API. По умолчанию возвращает
    детерминированную базу `base_script` как есть — её уже достаточно для готового
    к съёмке сценария.

    Расширение включается ТОЛЬКО если ведущий явно передаёт `generate` —
    вызываемый объект `generate(brief, base_script) -> dict|None`, который где-то
    снаружи (в отдельной задаче) обратится к модели и вернёт УЛУЧШЕННЫЙ сценарий
    того же формата (или подмешает поля дублей). Здесь только безопасный шов:
      * generate вернул валидный сценарий с непустыми дублями → берём его,
        предварительно прогнав через model.validate (мусор отбрасываем → база);
      * generate упал или вернул None/мусор → тихо остаёмся на базе.
    Никакого сетевого кода в модуле нет.
    """
    if generate is None:
        return base_script
    try:
        improved = generate(brief, base_script)
    except Exception:
        return base_script
    if not isinstance(improved, dict) or not improved.get("shots"):
        return base_script
    if model.validate(improved):
        return base_script
    improved.setdefault("slug", base_script.get("slug"))
    improved["llm_used"] = True
    return improved


# --- сборка ------------------------------------------------------------------

def _norm_brief(brief: dict) -> dict:
    b = dict(brief or {})
    b.setdefault("rubric", "Точки роста")
    b.setdefault("topic", "")
    b.setdefault("hook", "")            # явный хук (опц.)
    b.setdefault("platform", "instagram")
    b.setdefault("tone", "на «ты», конкретика, без инфоцыганщины")
    b.setdefault("voice_gender", config.voice_gender())
    b.setdefault("duration_sec", config.default_duration_sec())
    b.setdefault("beats", config.default_beats())
    b.setdefault("code_word", "")       # явное кодовое слово (опц.)
    return b


def build_script(brief: dict, bible_path=None, generate=None,
                 niche_source=None) -> dict:
    """Собирает сценарий съёмки из брифа. Возвращает dict (не сохраняет).

    Три рычага качества (всё офлайн, детерминированно):
      * реальные фразы книги смыслов (bible.themes / банк хуков) — приоритетно;
      * виральные хуки разведки через niche_bridge (niche_source — путь к банку,
        по умолчанию корень установки; нет банка → пропускаем);
      * ротация драматургических арок по теме.
    generate прокидывается в llm_script (по умолчанию None → чистая база)."""
    b = _norm_brief(brief)
    bib = bible_mod.load_bible(bible_path)
    rubric = b["rubric"]
    topic = str(b["topic"]).strip()

    # Кодовое слово: явное из брифа → конфиг → книга смыслов. Дефолта БОЛЬШЕ НЕТ.
    # Раньше здесь стояло «РОСТ» — слово Валерии, и клиент другой ниши получал в
    # финал ролика чужой призыв, по которому к нему никто не придёт. Не знаем
    # слова — собираем CTA без него и говорим об этом прямо (warnings ниже).
    code_word = (str(b["code_word"]).strip()
                 or config.code_word(bib.get("code_word") or ""))

    # ЛЕВЕР 3: арка выбирается детерминированно по теме, берём первые N битов.
    arc_name, arc_order = _pick_arc(topic, rubric)
    beats = arc_order[:int(b["beats"])]
    stages = ["hook"] + beats + ["cta"]

    weights = [_STAGE_SPEC[s]["weight"] for s in stages]
    durations = _split_durations(int(b["duration_sec"]), weights)

    # ЛЕВЕР 1+2: пул хука = виральные хуки разведки → темы рубрики из книги →
    # банк хуков книги. used держит уже занятые фразы, чтобы хук и биты не
    # повторяли друг друга.
    used = set()
    rubric_themes = list((bib.get("themes") or {}).get(rubric) or [])
    niche = _niche_hooks(niche_source)
    hook_pool = niche + rubric_themes + list(bib.get("hooks") or [])
    hook_text = _pick_hook(topic, rubric, hook_pool, b["hook"], used)
    phrase_key = topic or rubric or ""

    shots = []
    sources = []          # откуда взят каждый дубль: client / neutral / todo
    for i, stage in enumerate(stages):
        spec = _STAGE_SPEC[stage]
        if stage == "hook":
            say = hook_text
            subtitle = _subtitle(hook_text, 48)
            sources.append("client" if hook_pool and not b["hook"] else
                           ("client" if b["hook"] else "neutral"))
        elif stage == "cta":
            say = _cta_line(code_word)
            subtitle = ("Напиши «%s»" % code_word) if code_word else "Напиши мне в личку"
            sources.append("client" if code_word else "todo")
        else:
            say, src = _beat_say(stage, rubric_themes, phrase_key, used, bib)
            subtitle = _subtitle(say)
            sources.append(src)
        shots.append({
            "n": i + 1,
            "stage": stage,
            "intent": spec["intent"],
            "duration_sec": durations[i],
            "say": say,
            "show": spec["show"],
            "subtitle": subtitle,
            "visual_query": spec["q"],
        })

    script = {
        "version": model.SCHEMA_VERSION,
        "generated": model.now_iso(),
        "slug": model.slug("%s %s" % (rubric, topic or hook_text)),
        "arc": arc_name,
        "source": {
            "rubric": rubric,
            "topic": topic,
            "platform": b["platform"],
            "tone": b["tone"],
            "voice_gender": b["voice_gender"],
            "duration_target_sec": int(b["duration_sec"]),
            "beats": len(beats),
        },
        "code_word": code_word,
        "bible_ok": bool(bib.get("ok")),
        "anchor": bib.get("anchor"),
        "total_duration_sec": sum(durations),
        # Сколько сценария собрано из данных САМОГО клиента. Главная цифра этого
        # модуля: пока её никто не считал, подмена чужим контентом жила полгода и
        # выглядела как успешная сборка. Её печатает doctor и видит человек.
        "individual": _individual_stats(sources),
        "shots": shots,
        "checklist": [
            "Снимаешь вертикально 9:16, один в кадре.",
            "Свет ровный на лицо — крупные планы это любят.",
            "Говори своими словами: реплики — опора, не чтение слово-в-слово.",
            "Сумму разбора в ролике не называем — только кодовое слово в финале.",
        ],
        "quality_notes": [
            "Хук бьёт в боль дословно и обрывается на интриге.",
            "Цифры — только твои реальные, из книги смыслов; без «учёные доказали» и без чужих кейсов.",
            "Продаём через инсайт, а не в лоб: смысл остаётся за экспертом, "
            "автоматизируется только производство.",
        ],
        "llm_used": False,
    }
    return llm_script(b, script, generate=generate)

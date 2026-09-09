# -*- coding: utf-8 -*-
"""ШОВ К ПОДПИСЯМ/ПРИЗЫВАМ. Собирает текст подписи под материал из книги смыслов
(CONTENT_BIBLE_Valeria_Chirkova.md) детерминированными шаблонами тона.

Зачем: презентация обещает «тексты, подписи и призывы к каждому материалу».
Раскладка выкладки (AGENTS.md): карусель → IG (полная подпись) + Threads/TG
(короткая, потолок 1024 символа). Отсюда два выхода:
  * full  — подпись под IG: хук → раскрытие боли → оффер/ценность → призыв с
            кодовым словом;
  * short — версия ≤1024 символов под Threads/TG: сжимаем СМЫСЛ, а не хвост —
            хук и призыв с кодовым словом остаются всегда.

ПРЕДОХРАНИТЕЛЬ: тут только ГЕНЕРАЦИЯ текста. Ничего не публикуется, --send не
зовётся, платные API не трогаются, input() нет. Пути — относительно корня
установки (каталог с CONTRACT.md), никаких абсолютных путей и секретов.

«Умная» генерация — это LLM-шаг: см. документированный хук ``llm_caption`` ниже.
Платный API он НЕ зовёт по умолчанию (возвращает None → берётся шаблонная база).
База уже даёт валидную подпись; LLM — апгрейд качества, подключается позже.

Тон (ПРОЕКТ.md): на «ты», мужской род по умолчанию, конкретика, без
инфоцыганщины. Никаких выдуманных цифр — только реальные активы книги смыслов
(кейс Сергея, «136 точек роста»). Суммы в подписи не называем (красная линия).
"""
from __future__ import annotations

import re

# Потолок короткой подписи к альбому в Telegram/Threads — 1024 символа
# (AGENTS.md). Полную сервис отклонит, поэтому short обязан влезать.
SHORT_LIMIT = 1024

# Имя книги смыслов в корне установки (значение по умолчанию, если путь не задан).
DEFAULT_BIBLE_NAME = "CONTENT_BIBLE_Valeria_Chirkova.md"

# Кодовое слово по умолчанию (ПРОЕКТ.md: CTA «РОСТ», если не оговорено другое).
DEFAULT_CODE_WORD = "РОСТ"

# Якорная история из книги смыслов — реальный актив, не выдумка.
ANCHOR = "Клиент пришёл за контентом — на разборе мы нашли 136 точек роста."


# --- профили рубрик ---------------------------------------------------------
# Под каждую рубрику держим каркас подписи: боль (раскрытие), ценность (оффер),
# кодовое слово CTA и роль в воронке. Смыслы — сжатая выжимка книги смыслов;
# конкретную тему подставляем в шаблон, поэтому строки не «пустые» (проходят
# тест плотности из ПРОМТ_КОНТЕНТ.md).
# ОБНОВЛЕНО 22.08.2026: у каждой рубрики — свой набор ТЕХНИЧЕСКИХ хештегов
# (нейтральных, без привязки к нише конкретного клиента — их не знает этот
# файл). Хештег обязателен в каждом посте наравне с призывом (см. docstring
# модуля и AGENTS.md). Клиент может расширить/заменить набор под свою нишу —
# это стартовый generic-набор, не финальная формулировка.
_RUBRIC_PROFILE = {
    "Точки роста": {
        "code_word": "РОСТ",
        "pain": ("Ты работаешь больше, а роста нет — и кажется, что дело в "
                 "рекламе или в команде. Почти всегда деньги уже внутри "
                 "бизнеса, просто ты их не видишь: изнутри слепая зона не "
                 "читается."),
        "value": ("Стратегический разбор — это взгляд со стороны, который "
                  "находит точки роста там, где ты давно перестал смотреть. "
                  "Не новая реклама, а деньги, которые уже есть."),
        "role": "магнит",
        "hashtags": ("#точкироста", "#бизнесстратегия", "#развитиебизнеса",
                    "#предпринимательство"),
    },
    "Кейсы": {
        "code_word": "РОСТ",
        "pain": ("Со стороны кажется, что твой запрос — про одно. На разборе "
                 "почти всегда вскрывается, что дело глубже, чем ты думал на "
                 "входе."),
        "value": ("Взгляд со стороны видит очевидное, которое изнутри "
                  "невидимо. Разбор показывает, где именно у тебя лежит "
                  "следующий рост — на реальных цифрах твоего бизнеса."),
        "role": "доказательство",
        "hashtags": ("#кейс", "#бизнесстратегия", "#разборбизнеса", "#рост"),
    },
    "ИИ": {
        "code_word": "РОСТ",
        "pain": ("Кажется, что ИИ сам по себе принесёт рост. Не принесёт: "
                 "он усиливает систему, а не заменяет её. Автоматизировать "
                 "хаос — значит быстрее производить хаос."),
        "value": ("Сначала стратегия и точки роста, потом инструмент. На "
                  "разборе видно, какую рутину реально отдать нейросетям, а "
                  "где нужен ты."),
        "role": "польза",
        "hashtags": ("#ИИвбизнесе", "#автоматизациябизнеса", "#нейросети",
                    "#предпринимательство"),
    },
    "Мышление": {
        "code_word": "РОСТ",
        "pain": ("«Сделаю сам» — самая дорогая привычка предпринимателя. Ты "
                 "становишься узким местом собственного бизнеса и упираешься "
                 "в потолок, который сам же и держишь."),
        "value": ("Рост требует пересборки, а не новых усилий. Разбор со "
                  "стороны показывает, что именно тебя тормозит — и где "
                  "снять ограничение."),
        "role": "прогрев",
        "hashtags": ("#мышлениепредпринимателя", "#бизнесстратегия",
                    "#развитиебизнеса", "#рост"),
    },
    "Личный бренд": {
        "code_word": "РОСТ",
        "pain": ("Ты ведёшь блог, но контент не приводит клиентов — и "
                 "непонятно, за что тебя выбирать среди похожих."),
        "value": ("Живой инсайт из работы с предпринимателями важнее "
                  "красивой картинки. Разбор помогает увидеть, какой смысл "
                  "твоего бизнеса стоит доносить — и кому."),
        "role": "доверие",
        "hashtags": ("#личныйбренд", "#контентмаркетинг", "#позиционирование",
                    "#предпринимательство"),
    },
    "Продажи": {
        "code_word": "РАЗБОР",
        "pain": ("Ты откладываешь решение, потому что не уверен, что «два "
                 "часа» что-то изменят. Но потолок никуда не денется, пока "
                 "смотришь на бизнес одними и теми же глазами."),
        "value": ("Стратегический разбор за пару часов меняет направление: "
                  "ты выходишь с картой точек роста, а не с общими советами. "
                  "Беру ограниченное число людей — чтобы дойти с каждым до "
                  "результата."),
        "role": "продажа",
        "hashtags": ("#разборбизнеса", "#бизнесстратегия", "#рост", "#продажи"),
    },
    "Провокации": {
        "code_word": "РОСТ",
        "pain": ("Тебе не нужна новая реклама и не нужен новый курс. Ты "
                 "смотришь не туда: подписчики не равны деньгам, а трафик не "
                 "лечит систему."),
        "value": ("Следующий рост ближе, чем кажется — он уже внутри "
                  "бизнеса. Разбор со стороны показывает, где именно."),
        "role": "магнит",
        "hashtags": ("#бизнесстратегия", "#точкироста", "#предпринимательство",
                    "#рост"),
    },
}

# Каркас, если рубрика неизвестна (не падаем — работаем в урезанном режиме).
_FALLBACK_PROFILE = {
    "code_word": DEFAULT_CODE_WORD,
    "pain": ("Изнутри часть возможностей бизнеса не видно — это нормально. "
             "Слепую зону не разглядеть теми же глазами, что и всё остальное."),
    "value": ("Взгляд со стороны находит точки роста там, где ты перестал "
              "смотреть. Не новые вложения, а деньги, которые уже есть."),
    "role": "магнит",
    "hashtags": ("#бизнесстратегия", "#предпринимательство", "#рост", "#smm"),
}


# --- книга смыслов ----------------------------------------------------------

def _bible_default_path():
    """Путь к книге смыслов по умолчанию — от корня установки, без абсолютов.

    Импортируем config лениво: сам факт импорта caption_bridge не должен тянуть
    остальной пакет, если книга смыслов задана явным путём.
    """
    try:
        from . import config
        return config.install_root() / DEFAULT_BIBLE_NAME
    except Exception:
        from pathlib import Path
        # запасной вариант: .../modules/content_plan/caption_bridge.py
        return Path(__file__).resolve().parents[2] / DEFAULT_BIBLE_NAME


def load_bible(content_bible_path=None) -> dict:
    """Читает книгу смыслов и достаёт структуру для подписей.

    Возвращает dict:
      {"code_word": str, "hooks": [str, ...], "anchor": str, "ok": bool}

    Нет файла/битый файл → урезанный режим (дефолтное кодовое слово, пустой банк
    хуков), а НЕ падение — как и остальной модуль (см. config._load_config_file).
    """
    from pathlib import Path
    path = Path(content_bible_path) if content_bible_path else _bible_default_path()
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, ValueError):
        return {"code_word": DEFAULT_CODE_WORD, "hooks": [], "anchor": ANCHOR,
                "ok": False}

    return {
        "code_word": _parse_code_word(text),
        "hooks": _parse_hooks(text),
        "anchor": ANCHOR,
        "ok": True,
    }


def _parse_code_word(text: str) -> str:
    """Достаёт кодовое слово CTA из книги смыслов.

    Ищем прописное слово рядом с «CTA … слово …» / «напишите слово …». Если не
    нашли — дефолт РОСТ. Регэксп нестрогий: книга смыслов написана свободно.
    """
    patterns = [
        r"CTA[^\n]*?«([А-ЯЁ]{3,})»",
        r"слов[оа]\s+«([А-ЯЁ]{3,})»",
        r"CTA[^\n]*?\b([А-ЯЁ]{3,})\b",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m.group(1)
    return DEFAULT_CODE_WORD


def _parse_hooks(text: str) -> list:
    """Банк хуков из секции «50 ХУКОВ ДЛЯ REELS» (нумерованные строки).

    Берём только осмысленные строки-хуки (не «31-50. Вариации …»). Пустой список
    допустим — тогда хук берётся из темы/переданного hook.
    """
    block = text
    m = re.search(r"#\s*50\s+ХУКОВ[^\n]*\n", text)
    if m:
        block = text[m.end():]
    # до следующего заголовка первого уровня
    nxt = re.search(r"\n#\s", block)
    if nxt:
        block = block[:nxt.start()]
    hooks = []
    for line in block.splitlines():
        m = re.match(r"\s*\d+\.\s+(.+?)\s*$", line)
        if not m:
            continue
        h = m.group(1).strip()
        # отсекаем сводные строки-заглушки вида «Вариации на темы …»
        if "Вариаци" in h or h.startswith("31-"):
            continue
        if len(h) >= 8:
            hooks.append(h)
    return hooks


# --- сборка текста ----------------------------------------------------------

def _profile_for(rubric) -> dict:
    return _RUBRIC_PROFILE.get((rubric or "").strip(), _FALLBACK_PROFILE)


def _clean(s) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _as_hook_line(text: str) -> str:
    """Приводит строку к виду хука-открытия: с завершающим знаком."""
    text = _clean(text)
    if not text:
        return ""
    if text[-1] not in ".!?…":
        text += "."
    return text


def _pick_hook(topic, rubric, bible, explicit_hook=None) -> str:
    """Детерминированно выбирает первую фразу-хук.

    Приоритет: явный hook → банк хуков книги смыслов (по релевантности рубрике,
    выбор по hash темы — стабильно) → сама тема. Никакой случайности.
    """
    if explicit_hook:
        return _as_hook_line(explicit_hook)

    hooks = list(bible.get("hooks") or [])
    if hooks:
        key = " ".join(str(x) for x in (rubric or "", topic or "")).lower()
        # грубая релевантность: хуки, делящие слово с рубрикой/темой, вперёд
        words = {w for w in re.split(r"\W+", key) if len(w) >= 4}
        relevant = [h for h in hooks
                    if words & {w for w in re.split(r"\W+", h.lower())
                                if len(w) >= 4}]
        pool = relevant or hooks
        idx = abs(hash(_clean(topic))) % len(pool)
        return _as_hook_line(pool[idx])

    return _as_hook_line(topic)


def _cta_line(code_word: str, fmt: str) -> str:
    """Призыв с кодовым словом в тоне книги смыслов (на «ты», без давления)."""
    cw = (code_word or DEFAULT_CODE_WORD).strip()
    kind = "материал" if not fmt else {
        "carousel": "разбор", "reels": "тему",
    }.get(str(fmt).strip().lower(), "тему")
    return ("Если про твой бизнес — напиши слово %s мне в личные сообщения. "
            "Отвечу лично и подскажу, с чего начать %s." % (cw, kind))


def _hashtag_line(hashtags) -> str:
    """Строка хештегов через пробел. Пусто, если тегов нет."""
    tags = [t if str(t).startswith("#") else "#" + str(t) for t in (hashtags or ())]
    return " ".join(tags)


def _assemble(hook, pain, value, cta, hashtags="") -> str:
    """Собирает абзацы через двойной перенос (как в caption_*.txt). Хештеги —
    отдельным последним блоком, обязательны наравне с призывом (см. AGENTS.md)."""
    blocks = [b for b in (hook, pain, value, cta, hashtags) if b]
    return "\n\n".join(blocks).strip()


def _shorten(hook, pain, value, cta, limit=SHORT_LIMIT, hashtags="") -> str:
    """Короткая версия ≤limit: сжимаем СМЫСЛ (боль+ценность), хвост (призыв с
    кодовым словом + хештеги) и хук не режем.

    Стратегия по шагам, каждый следующий агрессивнее, но хук/CTA/хештеги
    неприкосновенны:
      1) хук + боль + ценность + CTA + хештеги;
      2) убрать боль (оставить ценность);
      3) убрать ценность (оставить боль);
      4) хук + CTA + хештеги;
      5) как крайняя мера — усечь боль/ценность по предложению.
    """
    hook = _clean(hook)
    cta = _clean(cta)
    pain = _clean(pain)
    value = _clean(value)
    hashtags = _clean(hashtags)

    candidates = [
        (hook, pain, value, cta),
        (hook, "", value, cta),
        (hook, pain, "", cta),
        (hook, "", "", cta),
    ]
    for h, p, v, c in candidates:
        text = _assemble(h, p, v, c, hashtags)
        if len(text) <= limit:
            return text

    # Крайняя мера: усечь середину по предложениям, хук+CTA+хештеги держим целиком.
    mandatory = _assemble(hook, "", "", cta, hashtags)
    room = limit - len(mandatory) - 2  # запас на перенос
    middle = _trim_sentences(value or pain, max(0, room))
    return _assemble(hook, "", middle, cta, hashtags) if middle else mandatory


def _trim_sentences(text: str, budget: int) -> str:
    """Оставляет столько целых предложений, сколько влезает в budget символов."""
    text = _clean(text)
    if budget <= 0 or not text:
        return ""
    out = ""
    for part in re.split(r"(?<=[.!?…])\s+", text):
        candidate = (out + " " + part).strip() if out else part
        if len(candidate) > budget:
            break
        out = candidate
    return out


def build(topic, rubric, fmt="carousel", content_bible_path=None,
          *, hook=None, meaning=None, code_word=None, llm=None) -> dict:
    """Собирает подпись/призыв под материал из книги смыслов.

    Аргументы:
      topic              — тема материала (обязательна по смыслу; пустая → хук из
                           рубрики/книги);
      rubric             — рубрика из плана («Точки роста», «Кейсы», «ИИ»,
                           «Мышление», «Личный бренд», «Продажи», «Провокации»);
      fmt                — формат («carousel»/«reels»), влияет только на формулу
                           призыва;
      content_bible_path — путь к книге смыслов (по умолчанию — от корня
                           установки CONTENT_BIBLE_Valeria_Chirkova.md);
      hook               — необязательный готовый хук (перебивает выбор из банка);
      meaning            — необязательный смысл слота (добавляется в раскрытие);
      code_word          — необязательное кодовое слово (перебивает рубрику/книгу);
      llm                — необязательный клиент/колбэк LLM для апгрейда качества
                           (см. llm_caption). По умолчанию None — работает база.

    Возвращает dict:
      {"full": <подпись под IG>, "short": <версия ≤1024 под Threads/TG>,
       "code_word": <кодовое слово CTA>, "rubric": ..., "format": ...}

    Детерминированно, без сети и без платных API.
    """
    bible = load_bible(content_bible_path)
    profile = _profile_for(rubric)
    cw = (code_word or profile.get("code_word") or bible.get("code_word")
          or DEFAULT_CODE_WORD)

    hook_line = _pick_hook(topic, rubric, bible, explicit_hook=hook)

    pain = profile["pain"]
    if meaning:
        pain = pain + " " + _as_hook_line(str(meaning))

    value = profile["value"]
    # Кейсовым/доказательным рубрикам добавляем якорную историю (реальный актив).
    if profile.get("role") in ("доказательство",):
        value = bible.get("anchor", ANCHOR) + " " + value

    cta = _cta_line(cw, fmt)
    hashtags = _hashtag_line(profile.get("hashtags") or bible.get("hashtags"))

    full = _assemble(hook_line, pain, value, cta, hashtags)
    short = _shorten(hook_line, pain, value, cta, limit=SHORT_LIMIT,
                     hashtags=hashtags)

    result = {
        "full": full,
        "short": short,
        "code_word": cw,
        "rubric": rubric,
        "format": fmt,
        "hashtags": hashtags,
    }

    # LLM-апгрейд (по умолчанию не зовётся — платный API запрещён).
    if llm is not None:
        upgraded = llm_caption(topic, rubric, fmt, draft=result, bible=bible,
                               client=llm)
        if upgraded:
            up = dict(result)
            up.update({k: upgraded[k] for k in ("full", "short")
                       if k in upgraded and upgraded[k]})
            # short-инвариант держим даже после LLM (хештеги тоже неприкосновенны).
            if len(up.get("short", "")) > SHORT_LIMIT:
                up["short"] = _shorten(hook_line, pain, value, cta,
                                       limit=SHORT_LIMIT, hashtags=hashtags)
            return up

    return result


def llm_caption(topic, rubric, fmt, draft, bible, *, client=None):
    """ХУК РАСШИРЕНИЯ: апгрейд подписи моделью (подключается позже).

    Это ЕДИНСТВЕННАЯ точка, куда встраивается «умная» генерация. Шаблонная база
    (build) уже отдаёт валидную подпись; LLM лишь повышает качество текста, держа
    те же смыслы, тон и кодовое слово.

    ВХОД:
      topic, rubric, fmt — контекст материала;
      draft — результат шаблонной базы: {"full","short","code_word",...}. Служит
              и контекстом, и запасным вариантом;
      bible — разобранная книга смыслов: {"code_word","hooks","anchor","ok"};
      client — клиент/колбэк модели (SDK, HTTP-обёртка, callable). По умолчанию
               None.

    ВЫХОД:
      {"full": str, "short": str} с улучшенным текстом — ИЛИ None, если апгрейда
      нет (тогда build берёт draft). Реализация ОБЯЗАНА держать инварианты:
        * тон на «ты», без инфоцыганщины (ПРОЕКТ.md);
        * никаких выдуманных цифр — только активы книги смыслов;
        * short ≤ 1024 символов, в нём есть призыв и кодовое слово draft["code_word"];
        * ничего не публикует.

    По умолчанию НИЧЕГО не зовёт (платный API запрещён рельсами) → возвращает None.
    Пример будущей реализации (псевдокод, НЕ активен):

        # prompt = _build_llm_prompt(topic, rubric, fmt, draft, bible)
        # resp = client(prompt)              # любой ваш клиент модели
        # full, short = _split_llm_output(resp)
        # return {"full": full, "short": short}
    """
    return None


# --- самопроверка (замер, без сети и платных API) ---------------------------

def _demo():
    """Прогон build на реальных темах/рубриках книги смыслов. Печатает full и
    short, проверяет: short ≤ 1024, в short есть призыв и кодовое слово.
    Запуск: python -m modules.content_plan.caption_bridge
    """
    cases = [
        ("Где чаще всего лежат дополнительные 500 000 ₽", "Точки роста", "carousel"),
        ("Клиент пришёл за контентом. Мы нашли 136 точек роста", "Кейсы", "reels"),
        ("Что происходит на стратегической сессии за 2 часа", "Продажи", "carousel"),
    ]
    all_ok = True
    for topic, rubric, fmt in cases:
        r = build(topic, rubric, fmt)
        cw = r["code_word"]
        short_ok = len(r["short"]) <= SHORT_LIMIT
        cta_ok = cw in r["short"] and ("личн" in r["short"].lower()
                                       or "директ" in r["short"].lower())
        all_ok = all_ok and short_ok and cta_ok
        print("=" * 72)
        print("РУБРИКА: %s | ФОРМАТ: %s | КОДОВОЕ СЛОВО: %s" % (rubric, fmt, cw))
        print("ТЕМА: %s" % topic)
        print("-" * 72)
        print("[FULL] (%d символов)\n%s" % (len(r["full"]), r["full"]))
        print("-" * 72)
        print("[SHORT] (%d символов, ≤%d: %s | призыв+код: %s)\n%s"
              % (len(r["short"]), SHORT_LIMIT, short_ok, cta_ok, r["short"]))
    print("=" * 72)
    print("ИТОГ: все инварианты соблюдены =", all_ok)
    return all_ok


if __name__ == "__main__":
    import sys
    sys.exit(0 if _demo() else 1)

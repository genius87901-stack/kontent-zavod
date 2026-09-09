# -*- coding: utf-8 -*-
"""Стадия 3 движка №4: transcript.json -> blocks.json (смысловые блоки).

Режет речь НЕ по предложениям, а по СМЫСЛУ. На каждый блок — тема, интент, эмоц.
пик и ВИЗУАЛЬНЫЙ ЗАПРОС (что показать b-roll'ом, а не слово из речи).

Интеллект стадии — Claude. Два режима (как music.py: рабочий путь один, второй —
точка опоры), выбор по наличию ключа в .env:
  - API: есть ANTHROPIC_API_KEY -> зовём Messages API, получаем блоки сами.
  - in-session: ключа нет -> build_prompt() отдаёт готовый промпт; текущая
    Claude-сессия (Claude Code) отвечает JSON, --from-answer его валидирует.
Так модуль работает и на сервере (API), и в ручном контуре завода (сессия).

ВХОД ДЛЯ АВТОСБОРКИ (мост контент-плана). Публичная стадия «под ключ» —
segment(transcript_path, topic=..., hook=...) -> список блоков: сама зовёт API,
сама разбирает ответ, сама валидирует контракт §6. Её и вызывает
modules/content_plan/reels_bridge, когда у слота плана нет готовых блоков.
Ключа нет -> segment() бросает NoApiKey ДО любого обращения в сеть, и мост
честно оставляет слот черновиком с пометкой «блоки готовит человек» — прежнее
поведение завода без ключа не меняется ни на шаг. Проверить наличие ключа, не
трогая сеть, можно api_key() (окружение, затем <корень>/.env).

Контракт выхода = REELS_TALK_ARCH.md §6 (сверено с engine_talk / talk_broll):
  {"src":..., "dur":..., "blocks":[{"t0","t1","topic","intent","emotion",
   "visual_query"}, ...]}
visual_query — КОРОТКАЯ АНГЛИЙСКАЯ фраза под сток-поиск (так talk_broll ищет в
Pixabay), topic/тема — по-русски. Блоки ПЛОТНО покрывают [0, dur] без дыр.

Промпт v2 (R39/R40, research/prompts/blocks_generation.v2.md) дополнительно
просит ОПЦИОНАЛЬНЫЕ поля форматов — title / lower_third / flash_word(+flash_at) /
item_no,item_title,items_total. Их читает render_reels (форматы 3/5/8,
text-behind); блок без поля — формат не рисуется (мягкая деградация, карточки
001/004). validate_blocks валидирует их МЯГКО: кривой тип → warning и отброс
поля, не падение стадии.
"""
import os
import re
import json
import argparse

import talk_plan  # read-only: MIN_SCENE/MAX_SCENE — единый источник порогов (R47)

HERE = os.path.dirname(os.path.abspath(__file__))
INTENTS = ("hook", "benefit", "story", "number", "emotion", "cta")

#: Имя ключа модели. Задаётся клиенту вопросом манифеста engine-media
#: (required:false, secret:true) и лежит в окружении ИЛИ в <корень>/.env.
API_KEY_ENV = "ANTHROPIC_API_KEY"

#: Модель по умолчанию. НЕ меняется без решения владельца: смена модели — это
#: смена цены прогона у клиента (платит он, ключ его).
DEFAULT_MODEL = "claude-sonnet-5"

#: Потолок ответа. Считается по факту сгенерённого, а не по потолку, поэтому
#: запас безопасен: обрезанный на середине JSON — это упавшая стадия, а не
#: экономия. Ролик на 60 с — это ~15 блоков, каждый до ~120 токенов.
MAX_TOKENS = 4000


class NoApiKey(RuntimeError):
    """Ключа модели нет — автоматический путь недоступен, остаётся in-session.

    Отдельный класс (а не голый RuntimeError), чтобы вызывающий отличал «ключ не
    заведён» — штатное состояние коробки без ключа — от «модель не ответила»
    (сеть, квота, кривой JSON). Наследник RuntimeError: прежние обработчики,
    ловившие RuntimeError, продолжают работать."""


def api_key():
    """Ключ модели: окружение, затем <корень установки>/.env. Пусто — ключа нет.

    Смотреть ТОЛЬКО os.environ нельзя: мастер установки кладёт ответы клиента в
    <корень>/.env (install/setup_config.py), и заполненный ключ был бы не виден.
    Само значение никуда не печатается и не логируется (CONTRACT.md §3) —
    наружу отдаём его только клиенту anthropic.

    Сети не касается: это чтение файла. Поэтому проверять «а есть ли ключ»
    безопасно откуда угодно, в том числе из доктора."""
    key = (os.environ.get(API_KEY_ENV) or "").strip()
    if key:
        return key
    path = os.path.join(HERE, ".env")
    try:
        with open(path, encoding="utf-8-sig", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                if name.strip().upper() == API_KEY_ENV:
                    return value.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


# ── чтение транскрипта ───────────────────────────────────────────────────────
def _load_segs(transcript_path):
    segs = json.load(open(transcript_path, encoding="utf-8"))
    if not isinstance(segs, list):
        raise ValueError("transcript.json должен быть МАССИВОМ сегментов "
                         "(не {'segments':...}) — см. REELS_TALK_ARCH.md §6")
    return segs


def _duration(segs):
    return round(float(segs[-1]["end"]), 3)


def timecoded_text(segs):
    """Транскрипт по сегментам с таймкодами — вход для смысловой нарезки."""
    lines = []
    for s in segs:
        txt = " ".join(w["w"] for w in s["words"]).strip()
        t0 = s["words"][0]["s"] if s["words"] else 0.0
        lines.append("[%6.2f–%6.2f] %s" % (t0, s["end"], txt))
    return "\n".join(lines)


# ── промпт для Claude ────────────────────────────────────────────────────────
def _block_count_range(dur):
    """Сколько блоков просить у Claude — из порогов сцен talk_plan (R47).

    Диапазон = [dur/MAX_SCENE .. dur/MIN_SCENE]: ровно столько сцен длиной
    MIN..MAX секунд укладывается в ролик. Раньше в промпте стояло «5–9» жёстко;
    теперь единственный источник истины — константы планировщика.
    """
    lo = max(1, int(round(dur / talk_plan.MAX_SCENE)))
    hi = max(lo + 1, int(round(dur / talk_plan.MIN_SCENE)))
    return lo, hi


def _slot_context(topic=None, hook=None):
    """Контекст слота контент-плана (тема и крючок) — необязательная приправа.

    Зачем: при автосборке рилса блоки нарезаются НЕ вслепую — в слоте плана уже
    записано, ради какой темы и какого крючка ролик снимали. Модель с этим
    контекстом точнее выбирает topic и visual_query. Пусто (ручной запуск CLI) —
    промпт байт-в-байт прежний, ни одного лишнего символа."""
    bits = []
    if topic and str(topic).strip():
        bits.append("ТЕМА ролика по контент-плану: %s" % str(topic).strip())
    if hook and str(hook).strip():
        bits.append("КРЮЧОК, ради которого ролик снимали: %s" % str(hook).strip())
    if not bits:
        return ""
    return ("КОНТЕКСТ СЛОТА (подсказка, а не содержание ролика):\n%s\n"
            "Опирайся на него, выбирая topic и visual_query, но НЕ приписывай "
            "ролику того, чего в речи нет.\n\n" % "\n".join(bits))


def build_prompt(transcript_path, topic=None, hook=None):
    """Промпт v2 — зафиксирован в research/prompts/blocks_generation.v2.md.

    Отличия от v1 (файл v1 сохранён по правилу библиотеки промптов):
    R39 — опциональные поля форматов + первый блок hook + intent строчными +
    калибровка emotion (по замеру JOURNAL: без калибровки порог EMO_PEAK=0.75
    в свежих прогонах не срабатывал ни разу);
    R40 — visual_query: дальние/общие планы, без надписей/логотипов/крупных лиц;
    R47 — числа длин и количества блоков из talk_plan.MIN_SCENE/MAX_SCENE.
    """
    segs = _load_segs(transcript_path)
    dur = _duration(segs)
    body = timecoded_text(segs)
    n_lo, n_hi = _block_count_range(dur)
    return (
        "Ты — монтажёр коротких вертикальных видео (Reels). Ниже транскрипт речи "
        "спикера с таймкодами (секунды). Разбей его на СМЫСЛОВЫЕ блоки — по смене "
        "мысли, а не по предложениям. Обычно %d–%d блоков, длина блока %g–%g "
        "секунд.\n\n"
        "На КАЖДЫЙ блок верни ОБЯЗАТЕЛЬНЫЕ поля:\n"
        "  t0, t1     — границы в секундах (числа). Блоки идут подряд и ПЛОТНО "
        "покрывают весь ролик: t0 первого = 0.0, t1 последнего = %.2f, t1 блока = "
        "t0 следующего (без дыр и нахлёстов).\n"
        "  topic      — тема блока, коротко, по-русски.\n"
        "  intent     — СТРОГО строчными, ровно одно из: %s "
        "(hook=крючок/зачин, benefit=польза, story=история, number=цифра/факт, "
        "emotion=эмоциональный пик, cta=призыв). ПЕРВЫЙ блок — всегда "
        'intent="hook".\n'
        "  emotion    — эмоциональный накал 0.0–1.0. Используй ВЕСЬ диапазон: "
        "спокойная подача 0.2–0.4, обычная речь 0.4–0.6, заметный подъём 0.6–0.8; "
        "САМЫЙ эмоциональный момент ролика оцени 0.85–1.0 (таких блоков 1–2, "
        "не больше).\n"
        "  visual_query — КОРОТКАЯ АНГЛИЙСКАЯ фраза (3–6 слов) для поиска b-roll "
        "на стоке: опиши, что ПОКАЗАТЬ (ассоциативно к смыслу), а не слово из "
        "речи. Предпочитай ОБЩИЕ/ДАЛЬНИЕ планы (wide shot, general view); избегай "
        "кадров с надписями, логотипами и крупными лицами; вертикальная "
        "композиция уместна. Пример: мысль про выход на сцену -> 'keynote "
        "speaker stage event audience'.\n\n"
        "И ОПЦИОНАЛЬНЫЕ поля форматов — заполняй ТОЛЬКО там, где содержание "
        "блока явно этого просит; НЕ выдумывай и НЕ заполняй для галочки:\n"
        "  title       — УДАРНАЯ мысль блока, 2–4 слова (крупный заголовок в "
        "кадре).\n"
        "  lower_third — число или тезис не длиннее 20 символов (плашка внизу "
        "кадра) — только если в блоке есть конкретная цифра/факт.\n"
        "  flash_word  — ОДНО ключевое слово из речи блока (вспышка на экране); "
        "опционально flash_at=[t0,t1] — окно показа в секундах внутри блока.\n"
        "  item_no, item_title, items_total — только если спикер ЯВНО перечисляет "
        "пункты («три причины», «шаг первый»): номер пункта (1..N), короткий "
        "заголовок пункта, всего пунктов.\n\n"
        "Верни СТРОГО JSON-массив блоков, без пояснений и без markdown-заборов:\n"
        '[{"t0":0.0,"t1":8.3,"topic":"...","intent":"hook","emotion":0.7,'
        '"visual_query":"..."}, ...]\n\n'
        "%s"
        "ТРАНСКРИПТ (%d сегментов, длительность %.2f сек):\n%s\n"
        % (n_lo, n_hi, talk_plan.MIN_SCENE, talk_plan.MAX_SCENE, dur,
           "/".join(INTENTS), _slot_context(topic, hook), len(segs), dur, body)
    )


# ── валидация и нормализация блоков ──────────────────────────────────────────
def _parse_json_blocks(raw):
    """Достать JSON-массив из ответа Claude (снять ```-заборы, найти [ ... ])."""
    if isinstance(raw, (list, dict)):
        data = raw
    else:
        s = raw.strip()
        s = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", s).strip()
        m = re.search(r"\[.*\]", s, re.S)
        data = json.loads(m.group(0) if m else s)
    if isinstance(data, dict) and "blocks" in data:
        data = data["blocks"]
    if not isinstance(data, list) or not data:
        raise ValueError("ожидался непустой JSON-массив блоков")
    return data


def _soft_format_fields(b, i, warn):
    """Опциональные поля форматов (R39) — МЯГКАЯ валидация.

    title / lower_third / flash_word(+flash_at) / item_no, item_title,
    items_total — их читает render_reels (text-behind, форматы 3/5/8). Нет
    поля — формат не рисуется (мягкая деградация), поэтому и кривое поле не
    должно ронять стадию: неверный тип → warning и отброс ПОЛЯ, не падение.
    Блоки v1-стиля (без этих полей) проходят без единого warning'а.
    """
    out = {}

    def _txt(key, maxlen=None):
        if b.get(key) is None:
            return
        v = b[key]
        if not isinstance(v, str):
            warn.append("блок %d: %s не строка (%s) — поле отброшено"
                        % (i, key, type(v).__name__))
            return
        v = v.strip()
        if not v:
            return
        if maxlen is not None and len(v) > maxlen:
            warn.append("блок %d: %s длиннее %d симв. (%r) — оставлено, "
                        "проверь глазами" % (i, key, maxlen, v))
        out[key] = v

    def _int(key):
        if b.get(key) is None:
            return
        try:
            out[key] = int(b[key])
        except (TypeError, ValueError):
            warn.append("блок %d: %s не целое (%r) — поле отброшено"
                        % (i, key, b[key]))

    _txt("title")
    _txt("lower_third", maxlen=20)
    _txt("flash_word")
    if b.get("flash_at") is not None:
        try:
            fa0, fa1 = float(b["flash_at"][0]), float(b["flash_at"][1])
            if fa1 <= fa0:
                raise ValueError("t1<=t0")
            out["flash_at"] = [round(fa0, 3), round(fa1, 3)]
        except (TypeError, ValueError, IndexError, KeyError):
            warn.append("блок %d: flash_at не [t0,t1] (%r) — поле отброшено"
                        % (i, b["flash_at"]))
    _int("item_no")
    _txt("item_title")
    _int("items_total")
    if "flash_at" in out and "flash_word" not in out:
        warn.append("блок %d: flash_at без flash_word — поле отброшено" % i)
        del out["flash_at"]
    return out


def validate_blocks(blocks, dur, tile_tol=0.5):
    """Проверить контракт §6 и ПЛОТНО уложить блоки в [0, dur].

    Возвращает (нормализованные_блоки, предупреждения). Кидает ValueError на том,
    что чинить нельзя (нет полей, кривой intent). Мелкие дыры/нахлёсты <tile_tol
    закрывает молча; крупные — предупреждает (возможна ошибка нарезки).
    Опциональные поля форматов (R39) валидируются мягко — см. _soft_format_fields.
    """
    warn = []
    norm = []
    for i, b in enumerate(blocks):
        for k in ("t0", "t1", "topic", "intent", "visual_query"):
            if k not in b:
                raise ValueError("блок %d: нет поля '%s'" % (i, k))
        intent = str(b["intent"]).strip().lower()
        if intent not in INTENTS:
            raise ValueError("блок %d: intent '%s' не из %s"
                             % (i, b["intent"], "/".join(INTENTS)))
        t0, t1 = float(b["t0"]), float(b["t1"])
        if t1 <= t0:
            raise ValueError("блок %d: t1<=t0 (%.2f<=%.2f)" % (i, t1, t0))
        vq = str(b["visual_query"]).strip()
        if not vq:
            raise ValueError("блок %d: пустой visual_query" % i)
        nb = {"t0": round(t0, 3), "t1": round(t1, 3),
              "topic": str(b["topic"]).strip(), "intent": intent,
              "emotion": round(float(b.get("emotion", 0.5)), 2),
              "visual_query": vq}
        nb.update(_soft_format_fields(b, i, warn))
        norm.append(nb)

    norm.sort(key=lambda x: x["t0"])
    # ПЛОТНАЯ укладка: t1[i] = t0[i+1], края -> [0, dur]
    for i in range(len(norm) - 1):
        gap = norm[i + 1]["t0"] - norm[i]["t1"]
        if abs(gap) > tile_tol:
            warn.append("блок %d↔%d: разрыв/нахлёст %.2f с — проверь нарезку"
                        % (i, i + 1, gap))
        norm[i]["t1"] = norm[i + 1]["t0"]
    if abs(norm[0]["t0"]) > tile_tol:
        warn.append("первый блок стартует не с 0.0 (%.2f)" % norm[0]["t0"])
    if abs(norm[-1]["t1"] - dur) > tile_tol:
        warn.append("последний блок кончается не на %.2f (а %.2f)"
                    % (dur, norm[-1]["t1"]))
    norm[0]["t0"] = 0.0
    norm[-1]["t1"] = dur
    return norm, warn


def write_blocks(src, dur, blocks, out_path):
    obj = {"src": src, "dur": dur, "blocks": blocks}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return out_path


# ── путь через API (сервер) ──────────────────────────────────────────────────
def segment_via_api(transcript_path, model=DEFAULT_MODEL, topic=None, hook=None):
    """СЫРОЙ ответ модели на промпт стадии 3 (текст, ещё не разобранный).

    Ключ ищем через api_key() (окружение → .env): раньше смотрели только
    os.environ, и заполненный мастером установки ключ был не виден. Нет ключа —
    NoApiKey ДО создания клиента, то есть до любого касания сети."""
    key = api_key()
    if not key:
        # Ключ проверяем ДО импорта клиента: без ключа не должно случиться ни
        # обращения к сети, ни даже загрузки тяжёлого пакета — путь без ключа
        # обязан быть бесплатным и быстрым.
        raise NoApiKey("нет %s — иди in-session режимом" % API_KEY_ENV)
    import anthropic  # ставится только там, где идём через API
    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model=model, max_tokens=MAX_TOKENS,
        messages=[{"role": "user",
                   "content": build_prompt(transcript_path, topic=topic,
                                           hook=hook)}])
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def segment(transcript_path, topic=None, hook=None, model=DEFAULT_MODEL,
            warnings=None):
    """СТАДИЯ 3 «ПОД КЛЮЧ»: транскрипт -> готовые смысловые блоки (список).

    Это единственная точка, которую зовёт автосборка рилса
    (modules/content_plan/reels_bridge): промпт, разбор ответа и валидация
    контракта §6 живут ЗДЕСЬ и нигде больше — мост не повторяет их у себя.

    Почему заведена именно она, а не вызов segment_via_api() из моста: тот
    отдаёт СЫРОЙ текст модели, и вызывающему пришлось бы дублировать
    _parse_json_blocks + validate_blocks + вычисление длительности — три куска
    контракта §6 в чужом файле, которые начнут расходиться с этим при первой же
    правке промпта. segment() — тонкая обёртка поверх существующего кода, ничего
    не переписывает.

    topic/hook — контекст слота плана, необязательный (см. _slot_context).
    warnings — необязательный список: сюда дописываются мягкие замечания
    валидатора (дыры в укладке, отброшенные поля форматов). Стадию они не
    роняют, но человеку в окно-вето их показать честнее, чем проглотить.

    Возвращает список блоков контракта §6, готовый к записи в blocks.json.
    Бросает: NoApiKey — ключа нет (автопуть недоступен, это НЕ поломка);
    ValueError — модель ответила непригодным (нет полей, кривой intent);
    ошибки клиента anthropic — сеть/квота/доступ."""
    raw = segment_via_api(transcript_path, model=model, topic=topic, hook=hook)
    dur = _duration(_load_segs(transcript_path))
    blocks, warn = validate_blocks(_parse_json_blocks(raw), dur)
    if warnings is not None:
        warnings.extend(warn)
    return blocks


# ── CLI ──────────────────────────────────────────────────────────────────────
def _load_env_key():
    """Подтянуть ключ из <корень>/.env в окружение (нужно веткам CLI ниже)."""
    key = api_key()
    if key:
        os.environ[API_KEY_ENV] = key


def main():
    ap = argparse.ArgumentParser(
        description="transcript.json -> blocks.json (смысловые блоки, Стадия 3)")
    ap.add_argument("transcript", help="путь к transcript.json")
    ap.add_argument("-o", "--out", default=None, help="куда писать blocks.json")
    ap.add_argument("--from-answer", default=None,
                    help="in-session: файл с JSON-ответом Claude на промпт")
    ap.add_argument("--print-prompt", action="store_true",
                    help="только напечатать промпт для Claude и выйти")
    args = ap.parse_args()

    _load_env_key()
    segs = _load_segs(args.transcript)
    dur = _duration(segs)
    src = segs[0].get("src") if isinstance(segs[0], dict) and segs[0].get("src") \
        else os.path.basename(args.transcript).replace("_transcript.json", "")
    out = args.out or args.transcript.replace("transcript", "blocks")

    if args.print_prompt:
        print(build_prompt(args.transcript))
    elif args.from_answer:                              # in-session: валидируем ответ
        raw = open(args.from_answer, encoding="utf-8").read()
        blocks, warn = validate_blocks(_parse_json_blocks(raw), dur)
        write_blocks(src, dur, blocks, out)
        for w in warn:
            print("  ⚠", w)
        print("готово: %s (блоков: %d)" % (out, len(blocks)))
    elif api_key():                                     # сервер: через API
        warn = []
        blocks = segment(args.transcript, warnings=warn)
        write_blocks(src, dur, blocks, out)
        for w in warn:
            print("  ⚠", w)
        print("готово: %s (блоков: %d)" % (out, len(blocks)))
    else:                                               # ключа нет — даём промпт
        print("Ключа ANTHROPIC_API_KEY нет — режим in-session.\n"
              "1) прогони с --print-prompt, отдай промпт Claude;\n"
              "2) сохрани JSON-ответ в файл;\n"
              "3) прогони с --from-answer <файл> — блоки провалидируются и запишутся.")


if __name__ == "__main__":
    raise SystemExit(main())

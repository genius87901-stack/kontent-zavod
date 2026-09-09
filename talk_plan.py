# -*- coding: utf-8 -*-
"""Стадия 6 движка №4 (Этап 5 плана): ПЛАНИРОВЩИК МОНТАЖА.

    blocks.json  →  talk_plan  →  timeline.json  →  talk_broll → engine_talk.render

До этой стадии тайминги вставок = границы смысловых блоков, один в один. Из-за
этого b-roll накрывает ВЕСЬ ролик: блоки плотно укладывают [0, dur], значит
активная вставка есть всегда, и сплит не выключается ни разу. Спикера при этом
видно только в верхней половине кадра — «хоронит» его, как записано в §9
`REELS_TALK_ARCH.md`. Планировщик решает не «что показать», а **когда молчать
картинкой**: где сплит, а где спикер на весь кадр.

ЧЕТЫРЕ ПРАВИЛА (из §9 архитектуры, пороги вынесены в аргументы):

1. **Длина сцены 4–8 с.** Блок длиннее — режется на равные части, короче —
   остаётся как есть. Причина: 12 секунд одного стокового кадра читаются как
   зависшая картинка, 2 секунды — как мигание.
2. **Спикер-only на хуке, на призыве и на эмоциональном пике.** Там работает
   лицо, а не сток: хук продаёт доверие, CTA — просьбу, пик — эмоцию.
3. **Серии не длиннее `max_series` сцен подряд.** Иначе три-четыре стоковых
   кадра встык превращают Reels в заставку.
4. **Доля b-roll ≤ 50% времени.** Это потолок из архитектуры. Когда правила 1–3
   его не набирают, лишние сцены снимаются по приоритету: сначала повторные
   части одного блока (там всё равно один и тот же кадр), потом самые короткие,
   потом ближние к концу.

ВЫХОД совпадает с тем, что движок принимает СЕГОДНЯ (§6 архитектуры):
`timeline.inserts` = `[{t0,t1,src,seg} | {t0,t1,text}]`. Сцены-спикеры в inserts
не попадают вовсе — движок сам показывает спикера на весь кадр, когда активной
вставки нет (`engine_talk.render`, ветка `template="split"`).

ЗАПУСК:
    python talk_plan.py --blocks out_talk/IMG_8138_blocks.json \
                        --out out_talk/IMG_8138_timeline.json

Правило завода «замер, не вывод»: печатает КАРТУ монтажа, а не «план готов».
"""
import argparse
import json
import math
import os

# пороги по умолчанию — §9 REELS_TALK_ARCH
MIN_SCENE = 4.0        # короче — кадр читается как мигание
MAX_SCENE = 8.0        # длиннее — как зависшая картинка
BROLL_SHARE = 0.5      # потолок доли b-roll по времени
MAX_SERIES = 2         # b-roll-сцен подряд
EMO_PEAK = 0.75        # emotion выше — эмоциональный пик, отдаём лицу
# Требование владельца 27.08.2026: перебивок в ролике НЕ МЕНЬШЕ четырёх.
# Дефолт здесь 0 — иначе правило задним числом завалило бы все прежние
# сборки завода. Свой пол каждый формат объявляет сам, полем pace.min_broll
# в styles/*.json (см. docs/ПРАВИЛА_ПЕРЕБИВОК.md, правило П1).
MIN_BROLL = 0          # пол числа b-roll-сцен на ролик
MIN_BREATH = 1.5       # короче — не вдох спикера, а мигание склейки
TOL = 0.05             # допуск на укладку, сек

# R28: микро-врезка — короткий b-roll на слово-акцент (поле flash_word блока).
# Только при темпе dynamic: на calm секундная вставка читается как мигание.
MICRO_MIN = 1.0        # короче — не врезка, а глитч
MICRO_MAX = 2.0        # длиннее — уже обычная сцена, не «врезка»

# на этих намерениях сток не помогает, а мешает (см. правило 2)
SPEAKER_ONLY = ("hook", "cta", "emotion")

# R27: пресеты темпа. calm — текущие пороги §9 (дефолт, поведение не меняется);
# dynamic — сцены 2.5–5 c (мировая практика коротких видео: смена плана 2–4 c,
# AUDIT фактор 5 «сцены 4–8 c против 2–4 c у лидеров»).
PACES = {
    "calm": {"min_scene": MIN_SCENE, "max_scene": MAX_SCENE},
    "dynamic": {"min_scene": 2.5, "max_scene": 5.0},
    # reels — замер референса владельца references/subtitry/01.ref.json:
    # scene_median 1.967 c, разброс 1.07-9.87, 20.08 склеек/мин. Верхнюю
    # границу держим 3.2 c: девятисекундная сцена там ОДНА и живёт только
    # потому, что внутри неё достраивается схема (правило П3).
    "reels": {"min_scene": 1.1, "max_scene": 3.2},
}


def pace_kwargs(pace):
    """Имя пресета -> kwargs для plan(). Неизвестный темп — понятная ошибка."""
    if pace not in PACES:
        raise ValueError("неизвестный темп %r (есть: %s)"
                         % (pace, ", ".join(sorted(PACES))))
    return dict(PACES[pace])


def _split_block(b, min_scene, max_scene):
    """Блок → 1..n сцен длиной по возможности в [min_scene, max_scene]."""
    d = b["t1"] - b["t0"]
    if d <= max_scene + TOL:
        return [dict(b, part=0, parts=1)]
    k = max(1, int(math.ceil(d / max_scene)))          # ни одна часть не длиннее
    while k > 1 and d / k < min_scene:                 # и по возможности не короче
        k -= 1
    step = d / k
    return [dict(b, t0=b["t0"] + i * step, t1=b["t0"] + (i + 1) * step,
                 part=i, parts=k) for i in range(k)]


def _is_speaker_intent(b, emo_peak):
    return (str(b.get("intent", "")).lower() in SPEAKER_ONLY
            or float(b.get("emotion", 0.0) or 0.0) >= emo_peak)


def _accent_window(b):
    """Окно слова-акцента блока: flash_at, иначе короткое окно от старта блока
    (та же логика, что flashes_from_blocks в render_reels). None — акцента нет."""
    if not str(b.get("flash_word", "") or "").strip():
        return None
    at = b.get("flash_at")
    if isinstance(at, (list, tuple)) and len(at) == 2:
        return float(at[0]), float(at[1])
    t0 = float(b["t0"])
    return t0, min(float(b["t1"]), t0 + 1.4)


def _inject_micro(scenes, blocks, total, broll_share, emo_peak):
    """R28: вырезать из сцен-спикеров микро-врезки (kind="micro", 1–2 c) на
    слово-акцент. Правила остаются в силе:
      - правило 2: блоки hook/cta/emotion не трогаем — там работает лицо;
      - правило 3 (серии): врезка сидит СТРОГО внутри сцены-спикера с вдохом
        MIN_BREATH с обеих сторон, соседи всегда спикеры — серию не удлиняет;
      - правило 4 (бюджет): врезка добавляется, только пока суммарный
        b-roll+micro ≤ broll_share.
    Мутирует scenes на месте, возвращает число вставленных врезок."""
    def share():
        return (sum(s["t1"] - s["t0"] for s in scenes
                    if s["kind"] in ("broll", "micro")) / total) if total else 0.0

    done_blocks, added = set(), 0
    for s in list(scenes):
        if s["kind"] != "speaker" or s["block"] in done_blocks:
            continue
        b = blocks[s["block"]]
        if _is_speaker_intent(b, emo_peak):            # правило 2
            continue
        if not str(b.get("visual_query", "")).strip():  # врезке нечего показывать
            continue
        acc = _accent_window(b)
        if acc is None:
            continue
        mid = (acc[0] + acc[1]) / 2.0
        if not (s["t0"] <= mid <= s["t1"]):            # акцент в другой сцене блока
            continue
        d = min(MICRO_MAX, max(MICRO_MIN, acc[1] - acc[0]))
        lo, hi = s["t0"] + MIN_BREATH, s["t1"] - MIN_BREATH   # вдох с обеих сторон
        if hi - lo < d:                                # сцена коротка для врезки
            continue
        m0 = min(max(mid - d / 2.0, lo), hi - d)
        m1 = m0 + d
        if share() + d / total > broll_share + 1e-6:   # правило 4
            continue
        i = scenes.index(s)
        head = dict(s, t1=m0)
        mic = dict(s, t0=m0, t1=m1, kind="micro",
                   why="врезка на слово-акцент «%s»" % b.get("flash_word", ""))
        tail = dict(s, t0=m1)
        scenes[i:i + 1] = [head, mic, tail]
        done_blocks.add(s["block"])
        added += 1
    return added


def plan(blocks, min_scene=MIN_SCENE, max_scene=MAX_SCENE,
         broll_share=BROLL_SHARE, max_series=MAX_SERIES, emo_peak=EMO_PEAK,
         micro=False):
    """blocks (стадия 3) → {"scenes", "broll_blocks", "stats"}.

    `broll_blocks` — ровно тот формат, который ест `talk_broll.broll_for_blocks`
    ({t0,t1,visual_query,...}), поэтому подключается одной строкой без правок
    подборщика: он видит их как обычные блоки, просто их стало меньше.

    `micro=True` (R28, включает --pace dynamic): в сцены-спикеры врезаются
    микро-врезки 1–2 c на слово-акцент (поле flash_word блока). Серии и бюджет
    50% учитывают врезки; врезка попадает в broll_blocks с пометкой micro.
    """
    if not blocks:
        raise ValueError("пустой список блоков")

    scenes = []
    for i, b in enumerate(blocks):
        for s in _split_block(b, min_scene, max_scene):
            s["block"] = i
            # правило 2 — намерение решает раньше любых бюджетов
            s["kind"] = "speaker" if _is_speaker_intent(b, emo_peak) else "broll"
            s["why"] = "интент %s" % b.get("intent", "?") if s["kind"] == "speaker" else ""
            if not str(b.get("visual_query", "")).strip():
                s["kind"], s["why"] = "speaker", "нет visual_query"
            scenes.append(s)
    scenes.sort(key=lambda s: s["t0"])

    total = scenes[-1]["t1"] - scenes[0]["t0"]

    # правило 3 — серии
    run = 0
    for s in scenes:
        if s["kind"] != "broll":
            run = 0
            continue
        run += 1
        if run > max_series:
            s["kind"], s["why"], run = "speaker", "серия длиннее %d" % max_series, 0

    # правило 4 — бюджет.
    def share():
        b = sum(s["t1"] - s["t0"] for s in scenes if s["kind"] == "broll")
        return b / total if total else 0.0

    why_budget = "бюджет b-roll ≤ %d%%" % round(broll_share * 100)
    excess = share() * total - broll_share * total
    # приоритет снятия: сначала повторные части одного блока (там всё равно
    # повторится тот же кадр), потом короткие, потом ближние к концу.
    order = sorted((s for s in scenes if s["kind"] == "broll"),
                   key=lambda s: (-s["part"], s["t1"] - s["t0"], -s["t0"]))

    # 1) снимаем целиком только те сцены, что помещаются в перебор. Иначе
    #    сцена на 7 с, снятая ради 1 с перебора, оставляет 20 с говорящей
    #    головы — поймано тестом на длинном блоке.
    for s in order:
        if excess <= TOL:
            break
        d = s["t1"] - s["t0"]
        if d <= excess + TOL:
            s["kind"], s["why"] = "speaker", why_budget
            excess -= d

    # 2) остаток закрываем ПОДРЕЗКОЙ хвоста сцены — если после неё b-roll
    #    остаётся не короче минимума, а спикеру достаётся не обрывок, а вдох.
    for s in order:
        if excess <= TOL:
            break
        if s["kind"] != "broll":
            continue
        d = s["t1"] - s["t0"]
        # режем не ровно на перебор, а минимум на вдох: перебор бывает в 1 с, и
        # секундная вставка спикера — это мигание склейки, а не пауза. Уходим
        # чуть ниже потолка — потолок и есть «не более».
        cut = max(excess, MIN_BREATH)
        if d - cut >= min_scene - TOL:
            tail = dict(s, t0=s["t1"] - cut, kind="speaker",
                        why=why_budget + ", подрезка")
            s["t1"] -= cut
            scenes.insert(scenes.index(s) + 1, tail)
            excess = 0.0
        else:
            s["kind"], s["why"] = "speaker", why_budget
            excess -= d

    # склейка: соседние сцены-спикеры одного блока — это одна сцена. Резать блок
    # имело смысл ради разного b-roll; если обе части ушли спикеру, кадр не
    # меняется, и два «куска» существуют только на бумаге.
    merged = []
    for s in scenes:
        prev = merged[-1] if merged else None
        if (prev and prev["kind"] == "speaker" == s["kind"]
                and prev["block"] == s["block"]):
            prev["t1"] = s["t1"]
            continue
        merged.append(s)
    scenes = merged

    # R28: микро-врезки — ПОСЛЕ склейки (врезаются в итоговые сцены-спикеры,
    # бюджет и серии считают их как b-roll).
    n_micro = _inject_micro(scenes, blocks, total, broll_share, emo_peak) \
        if micro else 0

    # пометка micro — только у врезок: дефолтный выход (micro=False) байт-в-байт
    broll_blocks = [dict({"t0": round(s["t0"], 2), "t1": round(s["t1"], 2),
                          "topic": s.get("topic", ""), "intent": s.get("intent", ""),
                          "visual_query": s["visual_query"], "scene": i},
                         **({"micro": True} if s["kind"] == "micro" else {}))
                    for i, s in enumerate(scenes)
                    if s["kind"] in ("broll", "micro")]

    runs, cur = [], 0.0
    for s in scenes:                                   # самая длинная говорящая голова
        cur = cur + (s["t1"] - s["t0"]) if s["kind"] == "speaker" else 0.0
        runs.append(cur)

    def share_all():                                   # b-roll + micro
        b = sum(s["t1"] - s["t0"] for s in scenes if s["kind"] in ("broll", "micro"))
        return b / total if total else 0.0

    lens = [s["t1"] - s["t0"] for s in scenes]
    stats = {
        "dur": round(total, 2),
        "scenes": len(scenes),
        "broll_scenes": len(broll_blocks),
        "micro_scenes": n_micro,
        "broll_share": round(share_all(), 3),
        "scene_min": round(min(lens), 2),
        "scene_max": round(max(lens), 2),
        "speaker_run_max": round(max(runs) if runs else 0.0, 2),
    }
    return {"scenes": scenes, "broll_blocks": broll_blocks, "stats": stats}


def validate(p, min_scene=MIN_SCENE, max_scene=MAX_SCENE,
             broll_share=BROLL_SHARE, max_series=MAX_SERIES,
             min_broll=MIN_BROLL):
    """Падает, если план нарушает свои же правила. Возвращает список
    ПРЕДУПРЕЖДЕНИЙ — то, что не ошибка, но человеку показать надо."""
    scenes, warn = p["scenes"], []
    for a, b in zip(scenes, scenes[1:]):               # плотная укладка без дыр
        if abs(b["t0"] - a["t1"]) > TOL:
            raise ValueError("разрыв в укладке: %.2f -> %.2f" % (a["t1"], b["t0"]))
    for s in scenes:
        d = s["t1"] - s["t0"]
        # потолок длины — про b-roll: это он «зависает» картинкой. Спикер может
        # идти дольше, за него отвечает предупреждение speaker_run_max ниже.
        if s["kind"] == "broll" and d > max_scene + TOL:
            raise ValueError("b-roll %.1f-%.1f длиннее %.1f с" % (s["t0"], s["t1"], max_scene))
        if s["kind"] == "broll" and d < min_scene - TOL:
            warn.append("b-roll %.1f-%.1f короче %.1f с (короткий блок)"
                        % (s["t0"], s["t1"], min_scene))
        # R28: микро-врезка обязана оставаться врезкой — строго 1–2 c
        if s["kind"] == "micro" and not (MICRO_MIN - TOL <= d <= MICRO_MAX + TOL):
            raise ValueError("micro %.1f-%.1f вне %.1f–%.1f с"
                             % (s["t0"], s["t1"], MICRO_MIN, MICRO_MAX))
    # правило П1: врезок не меньше пола. Ролик с одной-двумя перебивками
    # владелец считает браком — лучше не выпустить, чем выпустить пустой.
    if p["stats"]["broll_scenes"] < min_broll:
        raise ValueError("перебивок %d, а формат требует не меньше %d "
                         "(docs/ПРАВИЛА_ПЕРЕБИВОК.md, П1)"
                         % (p["stats"]["broll_scenes"], min_broll))
    if p["stats"]["broll_share"] > broll_share + 1e-6:
        raise ValueError("доля b-roll %.0f%% выше потолка %.0f%%"
                         % (p["stats"]["broll_share"] * 100, broll_share * 100))
    run = 0
    for s in scenes:                    # R28: micro считается в серию как b-roll
        run = run + 1 if s["kind"] in ("broll", "micro") else 0
        if run > max_series:
            raise ValueError("серия b-roll длиннее %d сцен на %.1f с" % (max_series, s["t0"]))
    if p["stats"]["speaker_run_max"] > max_scene * 1.5:
        warn.append("говорящая голова без перебивки %.1f с — проверь глазами"
                    % p["stats"]["speaker_run_max"])
    if not p["broll_blocks"]:
        warn.append("b-roll не запланирован вовсе — ролик выйдет одним планом")
    return warn


def to_timeline(p, inserts=None, template="split", acc="lime", overlays=None):
    """План (+ уже подобранные inserts) → timeline.json из §6 архитектуры."""
    return {
        "template": template, "acc": acc,
        "overlays": overlays or {"cta": None, "stickers": False},
        "inserts": inserts if inserts is not None else [],
        "plan": {"stats": p["stats"],
                 "scenes": [{"t0": round(s["t0"], 2), "t1": round(s["t1"], 2),
                             "kind": s["kind"], "block": s["block"],
                             "why": s["why"]} for s in p["scenes"]],
                 "broll_blocks": p["broll_blocks"]},
    }


def ascii_plan(p, width=64):
    """Карта монтажа строкой: █ = b-roll (сплит), ▒ = микро-врезка (R28),
    · = спикер на весь кадр."""
    marks = {"broll": "█", "micro": "▒"}
    scenes, dur = p["scenes"], p["stats"]["dur"] or 1.0
    bar = [" "] * width
    for s in scenes:
        a = int(s["t0"] / dur * width)
        b = max(a + 1, int(s["t1"] / dur * width))
        for i in range(a, min(b, width)):
            bar[i] = marks.get(s["kind"], "·")
    st = p["stats"]
    micro_note = ("   ▒ микро-врезка ×%d" % st["micro_scenes"]
                  if st.get("micro_scenes") else "")
    out = ["", "  КАРТА МОНТАЖА  (%.1f с, %d сцен)" % (st["dur"], st["scenes"]),
           "  " + "".join(bar),
           "  0" + " " * (width - 8) + "%5.1f с" % st["dur"],
           "  █ b-roll (сплит) %d сцен, %.0f%% времени   · спикер на весь кадр%s"
           % (st["broll_scenes"], st["broll_share"] * 100, micro_note), ""]
    for i, s in enumerate(scenes):
        mark = marks.get(s["kind"], "·")
        tail = (s["visual_query"] if s["kind"] == "broll"
                else s["why"] if s["kind"] == "micro" else ("— " + s["why"]))
        out.append("  %s %4.1f–%4.1f  %-22s %s"
                   % (mark, s["t0"], s["t1"], (s.get("topic") or "")[:22], tail[:38]))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="Стадия 6: планировщик монтажа движка №4")
    ap.add_argument("--blocks", required=True, help="blocks.json (стадия 3)")
    ap.add_argument("--out", help="куда писать timeline.json")
    ap.add_argument("--pace", choices=sorted(PACES), default="calm",
                    help="R27: пресет темпа. calm — сцены 4-8 с (текущее "
                         "поведение, дефолт); dynamic — 2.5-5 с (практика "
                         "коротких видео). Явные --min/--max перекрывают пресет")
    ap.add_argument("--min", type=float, default=None,
                    help="мин. длина сцены, с (перекрывает --pace)")
    ap.add_argument("--max", type=float, default=None,
                    help="макс. длина сцены, с (перекрывает --pace)")
    ap.add_argument("--share", type=float, default=BROLL_SHARE, help="потолок доли b-roll")
    ap.add_argument("--series", type=int, default=MAX_SERIES, help="b-roll-сцен подряд")
    ap.add_argument("--emo-peak", dest="emo_peak", type=float, default=EMO_PEAK,
                    help="R27: порог эмоционального пика (emotion выше — сцена "
                         "отдаётся лицу), по умолчанию %.2f" % EMO_PEAK)
    ap.add_argument("--acc", default="lime")
    a = ap.parse_args()

    with open(a.blocks, encoding="utf-8") as f:
        blocks = json.load(f)["blocks"]

    pk = pace_kwargs(a.pace)
    mn = a.min if a.min is not None else pk["min_scene"]
    mx = a.max if a.max is not None else pk["max_scene"]

    # R28: микро-врезки на слово-акцент включает только динамичный темп
    p = plan(blocks, min_scene=mn, max_scene=mx,
             broll_share=a.share, max_series=a.series, emo_peak=a.emo_peak,
             micro=(a.pace == "dynamic"))
    for w in validate(p, min_scene=mn, max_scene=mx,
                      broll_share=a.share, max_series=a.series):
        print("  ! " + w)
    print(ascii_plan(p))

    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(to_timeline(p, acc=a.acc), f, ensure_ascii=False, indent=1)
        print("  timeline: %s (inserts пуст — их наполняет talk_broll)" % a.out)


if __name__ == "__main__":
    raise SystemExit(main())

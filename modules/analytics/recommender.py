# -*- coding: utf-8 -*-
"""Движок рекомендаций: ДЕТЕРМИНИРОВАННЫЕ правила «метрика → совет».

Главное свойство (как весь завод): одни и те же метрики → один и тот же набор
советов. Никакой случайности, никакой сети, никаких платных API. Каждое правило —
это порог на нормализованной ставке (из model.derive) и человеческий совет с
КОНКРЕТНЫМ действием, плюс `planner_hint` — как этот сигнал мог бы повлиять на
планировщик (петля обратной связи, см. DESIGN §6; сам planner НЕ трогаем).

«Умный» шаг вынесен в явный хук `llm_advice(...)` по образцу `planner.llm_ideas` /
`writer.llm_script`: по умолчанию отдаёт детерминированную базу и платный API НЕ
зовёт.
"""
from __future__ import annotations

from . import config, model

# Уровни серьёзности совета (для сортировки и печати).
SEV_HIGH = "high"
SEV_MED = "med"
SEV_LOW = "low"
SEV_GOOD = "good"
_SEV_ORDER = {SEV_HIGH: 0, SEV_MED: 1, SEV_LOW: 2, SEV_GOOD: 3}


def _rec(rule, severity, metric, observed, threshold, advice, planner_hint):
    return {
        "rule": rule,
        "severity": severity,
        "metric": metric,
        "observed": observed,
        "threshold": threshold,
        "advice": advice,
        "planner_hint": planner_hint,
    }


# --- отдельные правила -------------------------------------------------------
# Каждое правило: (record, derived, thr, store) -> rec|None. Возврат None = сигнал
# в норме (или данных не хватает — правило тактично молчит).

def rule_low_retention(rec, d, thr, store):
    ret = d.get("retention_pct")
    if ret is None:
        return None  # не видео / площадка не отдаёт досмотр — нечего проверять
    limit = thr["retention_pct"]
    if ret >= limit:
        return None
    sev = SEV_HIGH if ret < limit * 0.6 else SEV_MED
    return _rec(
        "low_retention", sev, "retention_pct", ret, limit,
        "Досматривают мало (%.0f%% при норме %d%%). Усиль ХУК в первые 3 секунды "
        "и сократи ролик — убери воду в середине, оставь один тезис." % (ret, limit),
        {"target": "script_writer", "action": "strengthen_hook_shorten",
         "rubric": rec.get("rubric")})


def rule_low_ctr(rec, d, thr, store):
    ctr = d.get("ctr_pct")
    if ctr is None:
        return None
    limit = thr["ctr_pct"]
    if ctr >= limit:
        return None
    return _rec(
        "low_ctr", SEV_HIGH, "ctr_pct", ctr, limit,
        "Мало переходов по ссылке (%.2f%% при норме %.1f%%). Добавь ЯВНЫЙ CTA и "
        "кодовое слово в финал, продублируй ссылку в первый комментарий/закреп."
        % (ctr, limit),
        {"target": "content_plan", "action": "explicit_cta_code_word",
         "rubric": rec.get("rubric")})


def rule_low_saves(rec, d, thr, store):
    sr = d.get("save_rate_pct")
    if sr is None:
        return None
    limit = thr["save_rate_pct"]
    if sr >= limit:
        return None
    return _rec(
        "low_saves", SEV_MED, "save_rate_pct", sr, limit,
        "Мало сохранений (%.2f%% при норме %.1f%%) — контенту не хватает "
        "пользы-выжимки. Дай конкретный чек-лист/цифры, которые хочется сохранить."
        % (sr, limit),
        {"target": "content_plan", "action": "add_utility_checklist",
         "rubric": rec.get("rubric")})


def rule_low_engagement(rec, d, thr, store):
    er = d.get("engagement_rate_pct")
    if er is None:
        return None
    limit = thr["engagement_rate_pct"]
    if er >= limit:
        return None
    return _rec(
        "low_engagement", SEV_MED, "engagement_rate_pct", er, limit,
        "Реакций мало относительно охвата (%.1f%% при норме %.1f%%). Задай вопрос "
        "в тексте, добавь провокацию или голосование в сторис к посту."
        % (er, limit),
        {"target": "content_plan", "action": "add_question_or_provocation",
         "rubric": rec.get("rubric")})


def rule_weak_reach(rec, d, thr, store):
    base = d.get("base_audience")
    if base is None:
        return None
    median = model.baseline_reach(store, rec.get("platform"), rec.get("format"),
                                  exclude_id=rec.get("post_id"))
    if median is None:
        return None  # истории < 3 постов — честно молчим (см. DESIGN §7)
    drop = thr["reach_drop_pct"]
    floor = median * (1.0 - drop / 100.0)
    if base >= floor:
        return None
    pct_below = round((median - base) / median * 100.0, 0)
    return _rec(
        "weak_reach", SEV_MED, "base_audience", base, round(floor),
        "Охват ниже вашего среднего на ~%.0f%% (%d против медианы %d). Проверь "
        "время выхода и первые 3 секунды/обложку; смени заход под ту же тему."
        % (pct_below, int(base), int(median)),
        {"target": "content_plan", "action": "revisit_time_and_cover",
         "rubric": rec.get("rubric")})


# Порядок правил = порядок проверки (важное первым).
RULES = [rule_low_retention, rule_low_ctr, rule_low_saves,
         rule_low_engagement, rule_weak_reach]


# --- сборка рекомендаций по одному посту ------------------------------------

def _positive(rec, d):
    """Пост «зашёл» — предложить масштабировать тему/формат в план."""
    return _rec(
        "scale_winner", SEV_GOOD, "overall", None, None,
        "Пост отработал: %s / «%s» по всем ключевым метрикам в норме. Повтори "
        "тему и формат, поставь похожее в план — это ваш рабочий заход."
        % (rec.get("format") or "формат", rec.get("rubric") or "рубрика"),
        {"target": "content_plan", "action": "scale_rubric_format",
         "rubric": rec.get("rubric"), "format": rec.get("format")})


def recommend_one(record: dict, store: dict = None, thr: dict = None,
                  generate=None) -> dict:
    """Прогоняет правила по одному посту. Возвращает блок с советами.
    store нужен правилу охвата (базовая линия); None → пустое хранилище."""
    thr = thr or config.thresholds()
    store = store or model.empty_store()
    d = record.get("derived") or model.derive(record)

    recs = []
    for rule in RULES:
        r = rule(record, d, thr, store)
        if r:
            recs.append(r)
    if not recs:
        recs.append(_positive(record, d))

    recs.sort(key=lambda r: _SEV_ORDER.get(r["severity"], 9))
    block = {
        "post_id": record.get("post_id"),
        "platform": record.get("platform"),
        "format": record.get("format"),
        "rubric": record.get("rubric"),
        "topic": record.get("topic"),
        "derived": d,
        "recommendations": recs,
    }
    # «Умный» шаг — по образцу planner.llm_ideas: по умолчанию база как есть.
    return llm_advice(record, block, generate=generate)


def recommend_store(store: dict, thr: dict = None, generate=None) -> list:
    """Рекомендации по всем постам хранилища."""
    thr = thr or config.thresholds()
    return [recommend_one(p, store, thr, generate=generate)
            for p in store.get("posts", [])]


# --- LLM-хук (заглушка, платный API не зовёт) --------------------------------

def llm_advice(record: dict, base_block: dict, generate=None) -> dict:
    """Точка расширения советов «умным» шагом (LLM).

    КОНТРАКТ РЕЛЬСОВ (как planner.llm_ideas / writer.llm_script): сама функция НЕ
    ходит в сеть и НЕ зовёт платный API. По умолчанию (`generate=None`) возвращает
    детерминированный `base_block` как есть — его уже достаточно.

    Расширение включается ТОЛЬКО если ведущий явно передаёт `generate` —
    вызываемый объект `generate(record, base_block) -> list[str]|None`, который
    где-то снаружи (в отдельной задаче) обратится к модели и вернёт
    дополнительные текстовые советы. Здесь только безопасный шов: строки
    подмешиваются как советы уровня low; сбой/мусор → тихо остаёмся на базе.
    Никакого сетевого кода в модуле нет.
    """
    if generate is None:
        return base_block
    try:
        extra = generate(record, base_block)
    except Exception:
        return base_block
    if not isinstance(extra, (list, tuple)):
        return base_block
    added = []
    for item in extra:
        text = str(item).strip()
        if text:
            added.append(_rec("llm_advice", SEV_LOW, "llm", None, None, text,
                              {"target": "content_plan", "action": "llm_suggested"}))
    if added:
        base_block = dict(base_block)
        base_block["recommendations"] = list(base_block["recommendations"]) + added
        base_block["llm_used"] = True
    return base_block


# --- петля обратной связи с планировщиком (описана в DESIGN §6) --------------

def planner_feedback(blocks: list) -> dict:
    """Сворачивает советы всех постов в ПОДСКАЗКИ для планировщика: какие
    рубрики/форматы масштабировать (зашли), а где повторяются проблемы (усилить
    хук, добавить CTA). Это ПРЕДЛОЖЕНИЕ в свой файл — сам content_plan.planner
    модуль НЕ трогает и НЕ импортирует. Как именно planner мог бы это учитывать —
    в DESIGN §6."""
    scale = {}     # (rubric, format) -> сколько раз «зашло»
    fix = {}       # action -> {"count", "rubrics": set}
    for b in blocks:
        for r in b.get("recommendations", []):
            hint = r.get("planner_hint") or {}
            if r.get("severity") == SEV_GOOD:
                key = "%s | %s" % (b.get("rubric") or "?", b.get("format") or "?")
                scale[key] = scale.get(key, 0) + 1
            elif r.get("severity") in (SEV_HIGH, SEV_MED):
                act = hint.get("action", r.get("rule"))
                slot = fix.setdefault(act, {"count": 0, "rubrics": set()})
                slot["count"] += 1
                if b.get("rubric"):
                    slot["rubrics"].add(b.get("rubric"))
    return {
        "generated_at": model.now_iso(),
        "note": "Подсказки для планировщика (content_plan.planner). Это ПРЕДЛОЖЕНИЕ, "
                "а не команда: planner их читать не обязан и здесь не вызывается.",
        "scale_up": [{"rubric_format": k, "wins": v}
                     for k, v in sorted(scale.items(), key=lambda x: -x[1])],
        "fix_repeating": [{"action": k, "count": v["count"],
                           "rubrics": sorted(v["rubrics"])}
                          for k, v in sorted(fix.items(),
                                             key=lambda x: -x[1]["count"])],
    }

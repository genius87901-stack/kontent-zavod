# -*- coding: utf-8 -*-
"""Человекочитаемый вывод сценария: .md (для разбора) и .txt (на руки на съёмке).

Машинный вид (.json) пишет model.save_json. Здесь — только текст для человека,
никакой генерации: рендер берёт готовый dict сценария из writer.build_script.
"""
from __future__ import annotations

from pathlib import Path

from . import model, paths


def _intent_ru(intent: str) -> str:
    return {
        model.INTENT_HOOK: "ХУК",
        model.INTENT_POINT: "мысль",
        model.INTENT_EMOTION: "эмоция",
        model.INTENT_CTA: "ПРИЗЫВ",
    }.get(intent, intent)


def to_markdown(script: dict) -> str:
    src = script.get("source", {})
    L = []
    L.append("# Сценарий съёмки — %s" % (src.get("topic") or src.get("rubric") or ""))
    L.append("")
    L.append("- Рубрика: **%s**" % src.get("rubric", ""))
    L.append("- Формат: разговорный Reels, ~%d сек, дублей: %d"
             % (script.get("total_duration_sec", 0), len(script.get("shots", []))))
    L.append("- Площадка: %s · тон: %s" % (src.get("platform", ""), src.get("tone", "")))
    L.append("- Кодовое слово CTA: **%s**" % script.get("code_word", ""))
    if not script.get("bible_ok"):
        L.append("- ⚠️ Книга смыслов не прочитана — хуки взяты из темы "
                 "(урезанный режим).")
    L.append("")
    L.append("## Дубли")
    L.append("")
    for sh in script.get("shots", []):
        L.append("### Дубль %d · %s · ~%d сек"
                 % (sh["n"], _intent_ru(sh["intent"]), sh["duration_sec"]))
        L.append("- 🎤 **Говоришь:** %s" % sh["say"])
        L.append("- 🎬 **Показываешь:** %s" % sh["show"])
        L.append("- 📝 **Субтитр:** %s" % sh["subtitle"])
        L.append("")
    L.append("## Чек-лист перед съёмкой")
    for c in script.get("checklist", []):
        L.append("- %s" % c)
    L.append("")
    L.append("## Держим качество")
    for q in script.get("quality_notes", []):
        L.append("- %s" % q)
    L.append("")
    return "\n".join(L)


def to_text(script: dict) -> str:
    """Голый текст под съёмку в руках — без markdown-разметки."""
    src = script.get("source", {})
    L = []
    L.append("СЦЕНАРИЙ СЪЁМКИ: %s" % (src.get("topic") or src.get("rubric") or ""))
    L.append("Рубрика: %s | ~%d сек | кодовое слово: %s"
             % (src.get("rubric", ""), script.get("total_duration_sec", 0),
                script.get("code_word", "")))
    L.append("=" * 56)
    for sh in script.get("shots", []):
        L.append("")
        L.append("ДУБЛЬ %d  [%s, ~%dс]"
                 % (sh["n"], _intent_ru(sh["intent"]), sh["duration_sec"]))
        L.append("  ГОВОРИШЬ : %s" % sh["say"])
        L.append("  ПОКАЗЫВАЕШЬ: %s" % sh["show"])
        L.append("  СУБТИТР  : %s" % sh["subtitle"])
    L.append("")
    L.append("-" * 56)
    L.append("ПЕРЕД СЪЁМКОЙ:")
    for c in script.get("checklist", []):
        L.append("  - %s" % c)
    return "\n".join(L)


def write_all(script: dict, out_dir: Path = None) -> dict:
    """Пишет .md, .txt, .json рядом. Возвращает пути. Ничего не публикует."""
    paths.ensure_dirs()
    out_dir = out_dir or paths.output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = script.get("slug", "script")
    md_path = out_dir / (stem + ".md")
    txt_path = out_dir / (stem + ".txt")
    md_path.write_text(to_markdown(script), encoding="utf-8")
    txt_path.write_text(to_text(script), encoding="utf-8")
    json_path = model.save_json(script, out_dir / (stem + ".json"))
    return {"md": md_path, "txt": txt_path, "json": json_path}

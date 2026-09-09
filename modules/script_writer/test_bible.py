# -*- coding: utf-8 -*-
"""Регресс-тест bible.py на РЕАЛЬНОМ сценарии клиента другой ниши (баг-репорт
Елены Сокольской, астрология/мед. астрология, 08.08.2026).

Корневая причина бага: парсер жёстко заточен под структуру и рубрикатор
ОДНОГО примера-донора (CONTENT_BIBLE_Valeria_Chirkova.md) — точные заголовки
секций («# 50 ХУКОВ ДЛЯ REELS», «# 100 ТЕМ ДЛЯ ПОСТОВ») и зашитый список из 7
рубрик Валерии. У файла другой структуры это тихо даёт неправильные данные
вместо явной ошибки:
  1) код. слово CTA — тихий дефолт «РОСТ», не относящийся к клиенту;
  2) банк хуков — при отсутствии заголовка откатывается на ВЕСЬ документ и
     хватает любые нумерованные строки (включая заголовки разделов) как хуки;
  3) темы по рубрикам — рубрики клиента не матчатся к 7 рубрикам Валерии →
     0 рубрик, 0 тем, молча;
  4) якорный кейс — тихо возвращает кейс ВАЛЕРИИ («136 точек роста»), выдавая
     чужой факт за что-то, что нашлось в файле клиента.

Гоняется двумя способами:
    python3 modules/script_writer/test_bible.py      # автономно
    pytest modules/script_writer/test_bible.py        # если стоит pytest
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from modules.script_writer import bible  # noqa: E402

# Упрощённая, но реалистичная книга смыслов клиента другой ниши: другая
# структура секций, другой CTA-формат, другие названия рубрик.
ELENA_BIBLE = """# Книга смыслов — Елена Сокольская (астрология)

## Позиционирование
Мед. астролог, работаю с картой рождения и циклами здоровья.

## Призыв к действию
Если откликается — пишите мне в личные сообщения, расскажу подробнее и подберу
разбор под ваш запрос.

# Готовые хуки для Reels

1. Твоя карта рождения знает, когда ты заболеешь
2. Почему обычные врачи не видят то, что видит астрология
3. Три знака, что тебе пора к мед. астрологу

# Темы для постов

## Здоровье
1. Что карта рождения говорит о твоём иммунитете
2. Почему одни болеют часто, а другие — нет: взгляд астролога

## Кармические сценарии
1. Как родовые программы влияют на здоровье
2. Сценарий, который ты повторяешь из поколения в поколение

## Обучение
1. Что я даю на разборе за 90 минут
"""

# Документ БЕЗ секции хуков вовсе, но с нумерованными строками в других
# разделах (как у реального клиента: «1. О продукте», «2. Портрет клиента») —
# именно на этом старый парсер брал разделы документа за хуки.
NO_HOOKS_SECTION_BIBLE = """# Книга смыслов

## О продукте
1. О продукте
2. Портрет клиента
3. Возражения

## Кейсы
1. Кейс: разбор клиентки Марины
"""


def test_code_word_falls_back_with_warning_when_no_valeria_pattern():
    bib = bible.load_bible(bible_path=None, text=ELENA_BIBLE)
    assert bib["code_word"] == bible.DEFAULT_CODE_WORD
    assert any("кодовое слово" in w.lower() for w in bib["warnings"])


def test_code_word_found_by_explicit_marker_in_any_niche():
    """Клиент вписал явную пометку «Кодовое слово: …» — симметрично якорному
    кейсу (02_РАСПАКОВКА_ВОПРОСЫ.md просит именно эту форму). Работает в любой
    нише, не зависит от оборота «напишите слово «X»» в остальном тексте."""
    text = ELENA_BIBLE + "\nКодовое слово: ЗВЕЗДА\n"
    bib = bible.load_bible(bible_path=None, text=text)
    assert bib["code_word"] == "ЗВЕЗДА", bib["code_word"]
    assert not any("кодовое слово" in w.lower() for w in bib["warnings"])


def test_code_word_marker_variants_are_accepted():
    for marker, sep in (("Кодовое слово", ":"), ("кодовое слово", "—"),
                        ("КОДОВОЕ СЛОВО", "-")):
        text = ELENA_BIBLE + "\n%s %s «ЗВЕЗДА»\n" % (marker, sep)
        bib = bible.load_bible(bible_path=None, text=text)
        assert bib["code_word"] == "ЗВЕЗДА", (marker, sep, bib["code_word"])


def test_hooks_use_flexible_header_not_only_valeria_title():
    bib = bible.load_bible(bible_path=None, text=ELENA_BIBLE)
    assert "Твоя карта рождения знает, когда ты заболеешь" in bib["hooks"]
    assert len(bib["hooks"]) == 3
    # ничего из секции «Темы для постов» не просочилось в хуки
    assert not any("иммунитете" in h for h in bib["hooks"])


def test_hooks_empty_not_garbage_when_no_hooks_header_at_all():
    """КОРЕНЬ БАГА №2: без заголовка секции хуков — банк должен быть ПУСТ,
    а не набором заголовков разделов документа."""
    bib = bible.load_bible(bible_path=None, text=NO_HOOKS_SECTION_BIBLE)
    assert bib["hooks"] == []
    assert any("хук" in w.lower() for w in bib["warnings"])


def test_themes_keep_client_own_rubric_names_verbatim():
    """КОРЕНЬ БАГА №3: рубрики клиента другой ниши не обязаны совпадать с
    7 рубриками Валерии — они должны остаться под СВОИМИ именами."""
    bib = bible.load_bible(bible_path=None, text=ELENA_BIBLE)
    assert set(bib["themes"].keys()) == {"Здоровье", "Кармические сценарии",
                                          "Обучение"}
    assert "Что карта рождения говорит о твоём иммунитете" in \
        bib["themes"]["Здоровье"]
    # ни одна валерьевская рубрика не должна тихо появиться из ниоткуда
    assert "Точки роста" not in bib["themes"]
    assert "Провокации" not in bib["themes"]


def test_anchor_empty_not_fake_valeria_case_when_not_found():
    """КОРЕНЬ БАГА №4: нет своего кейса в файле — вернуть пусто, а не выдать
    факт Валерии («136 точек роста») за находку в файле клиента."""
    bib = bible.load_bible(bible_path=None, text=ELENA_BIBLE)
    assert bib["anchor"] == ""
    assert any("якорн" in w.lower() for w in bib["warnings"])


def test_anchor_still_defaults_when_file_itself_is_missing():
    """Деградация ТОЛЬКО когда файла нет вовсе — это старое, ожидаемое
    поведение (урезанный режим), не путать с «файл есть, но без кейса»."""
    bib = bible.load_bible(bible_path="/no/such/file/CONTENT_BIBLE.md")
    assert bib["ok"] is False
    assert bib["anchor"] == bible.DEFAULT_ANCHOR


def test_anchor_found_by_explicit_marker_in_any_niche():
    """Клиент вписал свой кейс явной пометкой — он ОБЯЗАН найтись.

    До 09.08.2026 поиск якоря шёл буквально по «136 … точ» — числу из кейса
    эксперта-донора. Чужой факт из выдачи убрали, а поиск остался его: клиент
    любой другой ниши мог вписать кейс куда угодно, и он не находился никогда.
    Дефект был тихий — сценарий просто вечно показывал пометку-задание."""
    text = ELENA_BIBLE + ("\nЯкорный кейс: Женщина пришла за совместимостью — "
                          "по карте увидели родовой сценарий, через три месяца "
                          "отношения выстроились.\n")
    bib = bible.load_bible(bible_path=None, text=text)
    assert "совместимост" in bib["anchor"], bib["anchor"]
    assert "136" not in bib["anchor"]


def test_anchor_marker_variants_are_accepted():
    """Человек напишет как ему удобно — принимаем разумные варианты пометки."""
    for marker in ("Якорный кейс:", "Главный кейс —", "кейс-якорь:"):
        text = ELENA_BIBLE + "\n%s Клиентка вернулась через полгода.\n" % marker
        bib = bible.load_bible(bible_path=None, text=text)
        assert "вернулась" in bib["anchor"], (marker, bib["anchor"])


# ── BUG-2026-08-10: оформление заголовков секций ─────────────────────────────
# Клиентка увидела «хуков в банке: 0, тем по рубрикам: 0 (рубрик: 0)» на НЕ
# пустой книге: парсер знал только «# » и «## », а её разделы оформлены иначе.
# Ниже — синтетические книги в стиле живых клиентских оформлений. Смысловые
# условия при этом не ослаблены, и последний тест сторожит именно это.

_H3_BIBLE = """# Книга смыслов

### 50 ХУКОВ ДЛЯ REELS

1. Первый хук про деньги клиента
2. Второй хук про рост выручки

### 100 ТЕМ ДЛЯ ПОСТОВ

#### Здоровье
1. Тема про иммунитет и карту
2. Тема про сон и циклы

#### Деньги
1. Тема про потолок дохода
"""

_BOLD_BIBLE = """Книга смыслов

**50 ХУКОВ**

1. Первый хук про деньги клиента
2. Второй хук про рост выручки

**100 ТЕМ ДЛЯ ПОСТОВ**

**Здоровье**
1. Тема про иммунитет и карту
2. Тема про сон и циклы

**Деньги**
1. Тема про потолок дохода
"""

_CAPS_BIBLE = """Книга смыслов Марины

50 ХУКОВ ДЛЯ REELS

1. Первый хук про деньги клиента
2. Второй хук про рост выручки

100 ТЕМ ДЛЯ ПОСТОВ

**Здоровье**
1. Тема про иммунитет и карту
2. Тема про сон и циклы

**Деньги**
1. Тема про потолок дохода
"""

_EMOJI_NUM_BIBLE = """# Книга

## 1. 🔥 50 ХУКОВ ДЛЯ REELS

1. Первый хук про деньги клиента
2. Второй хук про рост выручки

## 2) 📌 100 ТЕМ ДЛЯ ПОСТОВ

### Здоровье
1. Тема про иммунитет и карту
2. Тема про сон и циклы
"""

_SECTION_WORD_BIBLE = """Книга смыслов

Раздел 1 — Хуки для Reels

1. Первый хук про деньги клиента
2. Второй хук про рост выручки

Раздел 2 — Темы для постов

**Здоровье**
1. Тема про иммунитет и карту
"""

# Слово «хук» живёт в обычном абзаце, секции хуков в книге НЕТ.
_HOOK_WORD_IN_PROSE_BIBLE = """# Книга смыслов

## Как писать

Хук должен цеплять за 2 секунды, иначе человек листает дальше. Про хуки
подробно рассказываю на созвоне.

1. О продукте
2. Портрет клиента
"""


def _hooks_and_themes(text):
    bib = bible.load_bible(bible_path=None, text=text)
    return (bib["hooks"],
            sum(len(v) for v in bib["themes"].values()),
            list(bib["themes"].keys()))


def test_sections_found_under_h3_headers():
    """«### 50 ХУКОВ» — три решётки, а не одна-две. Раньше секции не было вовсе."""
    hooks, themes, rubrics = _hooks_and_themes(_H3_BIBLE)
    assert len(hooks) == 2, hooks
    assert themes == 3, themes
    assert rubrics == ["Здоровье", "Деньги"], rubrics


def test_sections_found_under_bold_headers():
    """Книга целиком набрана жирным, решёток нет ни одной."""
    hooks, themes, rubrics = _hooks_and_themes(_BOLD_BIBLE)
    assert len(hooks) == 2, hooks
    assert themes == 3, themes
    assert rubrics == ["Здоровье", "Деньги"], rubrics


def test_sections_found_under_bare_caps_lines():
    """Строка ЗАГЛАВНЫМИ без всякой разметки — самый частый «вордовский» стиль."""
    hooks, themes, rubrics = _hooks_and_themes(_CAPS_BIBLE)
    assert len(hooks) == 2, hooks
    assert themes == 3, themes
    assert rubrics == ["Здоровье", "Деньги"], rubrics


def test_sections_found_with_leading_number_and_emoji():
    """Ведущий номер и эмодзи перед текстом заголовка ничего не ломают."""
    hooks, themes, rubrics = _hooks_and_themes(_EMOJI_NUM_BIBLE)
    assert len(hooks) == 2, hooks
    assert themes == 2, themes
    assert rubrics == ["Здоровье"], rubrics


def test_sections_found_by_section_word_with_number():
    """«Раздел 2 — Темы для постов»: без разметки и без капса, но по служебному
    слову с номером это всё-таки заголовок."""
    hooks, themes, rubrics = _hooks_and_themes(_SECTION_WORD_BIBLE)
    assert len(hooks) == 2, hooks
    assert themes == 1, themes
    assert rubrics == ["Здоровье"], rubrics


def test_hook_word_inside_paragraph_does_not_open_section():
    """ГЛАВНЫЙ РИСК гибкого поиска — ложная секция. Строка «Хук должен цеплять
    за 2 секунды» посреди абзаца заголовком быть НЕ ДОЛЖНА: иначе банк хуков
    наберётся из «1. О продукте / 2. Портрет клиента»."""
    hooks, themes, _r = _hooks_and_themes(_HOOK_WORD_IN_PROSE_BIBLE)
    assert hooks == [], hooks
    assert themes == 0, themes


def test_flexible_headers_do_not_relax_meaning():
    """Оформление стало не важно — СМЫСЛОВЫЕ условия остались прежними.
    «Темы» без «постов»/«рубрик» секцией тем не становятся, «темп» — тем более."""
    text = ("### Темы недели\n\n1. Тема про что-то одно\n\n"
            "### Темп речи\n\n1. Говорить медленнее в начале\n")
    bib = bible.load_bible(bible_path=None, text=text)
    assert bib["themes"] == {}, bib["themes"]


def test_themes_section_named_by_number_only():
    """BOX-52, случай клиентки 11.08.2026: раздел назван «### 100 ТЕМ» — без
    слов «для постов» и «по рубрикам». Патч 1.3.5 научился видеть её «### 50
    ХУКОВ», а темы оставались нулём при полностью читаемом файле."""
    text = ("# КНИГА СМЫСЛОВ\n\n### 50 ХУКОВ\n\n"
            "1. Первый хук про деньги и рост\n"
            "2. Второй хук про время и силы\n\n"
            "### 100 ТЕМ\n\n**Здоровье**\n"
            "1. Как я вернула себе силы за месяц\n"
            "2. Почему усталость это не лень\n\n"
            "**Деньги**\n1. Сколько стоит моя работа и почему\n")
    bib = bible.load_bible(bible_path=None, text=text)
    assert len(bib["hooks"]) == 2, bib["hooks"]
    assert list(bib["themes"]) == ["Здоровье", "Деньги"], bib["themes"]
    assert sum(len(v) for v in bib["themes"].values()) == 3, bib["themes"]


def test_bullet_items_read_like_numbered():
    """BOX-52: книга, набранная дефисами, читалась как пустая при полностью
    распознанных заголовках — брались ТОЛЬКО строки вида «1. …»."""
    text = ("# КНИГА\n\n### 50 ХУКОВ\n\n"
            "- Первый хук про деньги и рост\n"
            "• Второй хук про время и силы\n\n"
            "### 100 ТЕМ\n\n**Здоровье**\n"
            "- Как я вернула себе силы за месяц\n")
    bib = bible.load_bible(bible_path=None, text=text)
    assert len(bib["hooks"]) == 2, bib["hooks"]
    assert bib["themes"] == {"Здоровье": ["Как я вернула себе силы за месяц"]}, bib["themes"]


def test_number_plus_word_starting_with_tem_is_not_themes():
    """Обратная сторона BOX-52: числом секцию открывает слово «тем» ЦЕЛИКОМ.
    «5 ТЕМНЫХ ПАТТЕРНОВ» секцией тем не становится."""
    text = ("# КНИГА\n\n### 5 ТЕМНЫХ ПАТТЕРНОВ\n\n**Раз**\n"
            "1. Совсем про другое здесь написано\n")
    bib = bible.load_bible(bible_path=None, text=text)
    assert bib["themes"] == {}, bib["themes"]


def _run_all():
    test_code_word_falls_back_with_warning_when_no_valeria_pattern()
    test_code_word_found_by_explicit_marker_in_any_niche()
    test_code_word_marker_variants_are_accepted()
    test_hooks_use_flexible_header_not_only_valeria_title()
    test_hooks_empty_not_garbage_when_no_hooks_header_at_all()
    test_themes_keep_client_own_rubric_names_verbatim()
    test_anchor_empty_not_fake_valeria_case_when_not_found()
    test_anchor_still_defaults_when_file_itself_is_missing()
    test_anchor_found_by_explicit_marker_in_any_niche()
    test_anchor_marker_variants_are_accepted()
    test_sections_found_under_h3_headers()
    test_sections_found_under_bold_headers()
    test_sections_found_under_bare_caps_lines()
    test_sections_found_with_leading_number_and_emoji()
    test_sections_found_by_section_word_with_number()
    test_hook_word_inside_paragraph_does_not_open_section()
    test_flexible_headers_do_not_relax_meaning()
    test_themes_section_named_by_number_only()
    test_bullet_items_read_like_numbered()
    test_number_plus_word_starting_with_tem_is_not_themes()


if __name__ == "__main__":
    _run_all()
    print("OK bible.py: книга смыслов другой ниши (Елена Сокольская) "
          "парсится без подмены данными Валерии")

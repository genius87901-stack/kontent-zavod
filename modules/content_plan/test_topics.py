# -*- coding: utf-8 -*-
"""Регресс-тест topics.py — банк тем контент-плана, подключаемый ПЕР-КЛИЕНТ
(BUG-2026-08-08, баг-репорт Елены Сокольской).

Корневая причина: до этого патча planner.TOPIC_BANK был захардкожен в Python
прямо под книгу смыслов Валерии, и переключателя (аналога script_bible_file у
script_writer) для контент-плана не существовало вообще — подключить книгу
смыслов другого клиента к контент-плану было невозможно в принципе.

Гоняется двумя способами:
    python3 modules/content_plan/test_topics.py      # автономно
    pytest modules/content_plan/test_topics.py        # если стоит pytest
"""
from __future__ import annotations

import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, _REPO_ROOT)

from modules.content_plan import topics  # noqa: E402

# Та же книга смыслов клиента другой ниши, что и в test_bible.py (см. там же
# развёрнутый разбор бага) — здесь важна только секция тем по рубрикам.
ELENA_BIBLE = """# Книга смыслов — Елена Сокольская (астрология)

# Темы для постов

## Здоровье
1. Что карта рождения говорит о твоём иммунитете
2. Почему одни болеют часто, а другие — нет: взгляд астролога

## Кармические сценарии
1. Как родовые программы влияют на здоровье
"""


def test_client_rubrics_appear_verbatim_and_take_priority():
    bank, meta = topics.load_topic_bank(bible_text=ELENA_BIBLE)
    assert "Здоровье" in bank
    assert "Кармические сценарии" in bank
    topics_health = [t[0] for t in bank["Здоровье"]]
    assert "Что карта рождения говорит о твоём иммунитете" in topics_health
    assert meta["source"]["Здоровье"] == "client"
    assert meta["source"]["Кармические сценарии"] == "client"


def test_default_rubrics_stay_available_as_fallback():
    """Рубрики заготовки не пропадают — иначе у клиента с двумя рубриками план
    просто не наберётся. Но заготовка обязана быть УНИВЕРСАЛЬНОЙ."""
    bank, meta = topics.load_topic_bank(bible_text=ELENA_BIBLE)
    assert "Кейсы" in bank
    assert len(bank["Кейсы"]) > 0
    assert meta["source"]["Кейсы"] == "default"


def test_default_bank_carries_no_foreign_niche_content():
    """Главный инвариант файла-заготовки: ни цифр, ни ниши, ни чужих кейсов.

    Заготовка уезжает КАЖДОМУ клиенту. Всё конкретное в ней станет чужой
    конкретикой в чужом плане — так и выглядел BUG-2026-08-08: астролог получал
    «дополнительные 500 000 ₽» и «136 точек роста» как свои темы."""
    bank, _ = topics.load_topic_bank(bible_path="/no/such/file.md")
    flat = json.dumps(bank, ensure_ascii=False)
    for foreign in ("500 000", "136", "бизнес", "предприним", "Валери"):
        assert foreign not in flat, "в заготовке осталось чужое: %s" % foreign


def test_no_bible_at_all_falls_back_to_default_bank_fully():
    bank, meta = topics.load_topic_bank(bible_path="/no/such/file.md")
    assert "Ошибки" in bank
    assert all(v == "default" for v in meta["source"].values())
    assert meta["client_bible_ok"] is False


def test_valeria_own_file_is_not_broken_by_the_fix():
    """Регресс: реальный файл Валерии по-прежнему даёт непустой банк по всем
    её рубрикам (не только фолбэк на дефолт)."""
    # Путь считается от самого теста, а не зашивается: абсолютный путь машины
    # разработчика нарушает CONTRACT.md §3 (линт коробки останавливает сборку) и
    # молча превращал бы этот регресс в пустышку на любой другой машине.
    real_path = os.path.join(_REPO_ROOT, "CONTENT_BIBLE_Valeria_Chirkova.md")
    if not os.path.exists(real_path):
        return  # файл донора недоступен в этом окружении — тест не применим
    bank, meta = topics.load_topic_bank(bible_path=real_path)
    assert len(bank) >= 7
    assert any(src == "client" for src in meta["source"].values())


def _run_all():
    test_client_rubrics_appear_verbatim_and_take_priority()
    test_default_rubrics_stay_available_as_fallback()
    test_default_bank_carries_no_foreign_niche_content()
    test_no_bible_at_all_falls_back_to_default_bank_fully()
    test_valeria_own_file_is_not_broken_by_the_fix()


if __name__ == "__main__":
    _run_all()
    print("OK topics.py: банк тем контент-плана подключается пер-клиент, "
          "не только под Валерию")

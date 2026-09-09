# -*- coding: utf-8 -*-
"""story-publisher — автопубликация сторис в Instagram / Telegram / ВКонтакте.

Публикует ГОТОВОЕ медиа (рендер 9:16 делают движки проекта, не этот модуль).
Единая точка входа — publish_story. По умолчанию dry_run=True: ничего не
отправляется, только валидируется вход и логируется намерение.
"""
from __future__ import annotations

from .publisher import publish_story
from .story_builder import build_story

__all__ = ["publish_story", "build_story"]

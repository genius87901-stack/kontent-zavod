# -*- coding: utf-8 -*-
"""Точка входа модуля: python -m story_publisher ...

Делегирует в CLI сборки+выкладки. По умолчанию — пробный прогон без сети."""
from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())

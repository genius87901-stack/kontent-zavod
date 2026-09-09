# -*- coding: utf-8 -*-
"""Тесты драйвера MAX и того, как доктор про него отчитывается.

ЧТО ИМЕННО СТОРОЖИМ. Драйвер MAX добавлен 01.09.2026 и отличается от соседей
двумя вещами, на которых легко соврать клиенту:

  1. В MAX НЕТ ФОРМАТА «ИСТОРИИ» вообще. Всё, что уходит, — пост в канал.
     Если из ответа драйвера когда-нибудь пропадёт пометка `limitation`,
     «accepted» прочитается как «история вышла», а её не было и быть не может.
  2. ПУСТОЙ MAX — НЕ ПОЛОМКА. У клиента, которому MAX не нужен, доктор обязан
     возвращать 0. Иначе код 2 становится вечным, и владелец каждый день видит
     деградацию, на которую нечем реагировать (тот же довод записан в доктора
     про непроверенные сторис ВК). А вот ПОЛОВИНА ответа — настоящая недоделка:
     публикации не будет, и закрывается это одним ответом мастеру.

Гоняется двумя способами:
    python modules/story_publisher/test_max_driver.py
    pytest modules/story_publisher/test_max_driver.py
"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))


# ── БЛОКИРОВКА СЕТИ НА ВЕСЬ ФАЙЛ ─────────────────────────────────────────────
# Ровно по тому же поводу, что в test_doctor.py: на этой машине живут боевые
# ключи, а драйвер умеет публиковать по-настоящему. Ни один тест публикации не
# делает — но «умеет, но мы аккуратно» уже приводило к настоящему посту.
def _block_network():
    import socket
    import ssl  # noqa: F401

    def boom(*_a, **_kw):
        raise AssertionError(
            "ТЕСТ ПОШЁЛ В СЕТЬ. В тестах публикации не бывает — прогон "
            "остановлен намеренно.")

    socket.socket.connect = boom
    socket.socket.connect_ex = boom
    socket.create_connection = boom
    socket.getaddrinfo = boom
    for key in [k for k in os.environ
                if k.startswith(("VK_", "UPLOAD_POST_", "TG_", "MAX_"))]:
        os.environ.pop(key, None)


_block_network()

from modules.story_publisher import config, doctor  # noqa: E402
from modules.story_publisher.drivers import get_driver, known_platforms  # noqa: E402
from modules.story_publisher.drivers import max as mx  # noqa: E402


class _Cfg:
    """Конфиг клиента без файлов и окружения: что задали, то и вернём."""

    def __init__(self, **filled):
        self._filled = {k: v for k, v in filled.items() if v}

    def value(self, key, default=None):
        return self._filled.get(key, default)

    def secret(self, key, default=None):
        return self._filled.get(key, default)

    def has_secret(self, key):
        return bool(self.secret(key))

    def any_value(self, *keys, default=None):
        for key in keys:
            val = self.value(key)
            if val not in (None, ""):
                return val
        return default

    def has_any(self, *keys):
        return bool(self.any_value(*keys))


_ЗАПОЛНЕН = {"max_bot_token": "тест-токен", "max_chat_id": "-100500"}


@contextlib.contextmanager
def _кадр(имя="01.png", байт=1024):
    """Файл нужного расширения и размера — драйвер смотрит на то и другое."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / имя
        p.write_bytes(b"x" * байт)
        yield p


# ── 1. регистрация ───────────────────────────────────────────────────────────

def test_max_виден_как_площадка():
    assert "max" in known_platforms()
    for имя in ("max", "макс", "maks", "MAX"):
        assert get_driver(имя) is mx, "псевдоним %r не ведёт на драйвер MAX" % имя


# ── 2. честность про формат ──────────────────────────────────────────────────

def test_ответ_всегда_говорит_что_это_пост_а_не_история():
    """Пометка `limitation` обязана быть в ЛЮБОМ ответе, включая отказы."""
    случаи = []
    with _кадр() as p:
        случаи.append(mx.publish(p, "текст", None, _Cfg(), dry_run=True))
        случаи.append(mx.publish(p, "текст", None, _Cfg(**_ЗАПОЛНЕН), dry_run=True))
    with _кадр("story.pdf") as p:
        случаи.append(mx.publish(p, "", None, _Cfg(**_ЗАПОЛНЕН), dry_run=True))
    for res in случаи:
        assert "истории" in res.get("limitation", ""), (
            "из ответа пропала пометка про пост вместо истории: %r" % res)


# ── 3. поведение без ключей ──────────────────────────────────────────────────

def test_без_ключей_не_configured_и_названо_чего_нет():
    with _кадр() as p:
        res = mx.publish(p, "", None, _Cfg(), dry_run=True)
    assert res["status"] == "not_configured"
    assert any("max_bot_token" in m for m in res["missing"])
    assert any("max_chat_id" in m for m in res["missing"])


def test_половина_ключей_тоже_не_configured():
    with _кадр() as p:
        res = mx.publish(p, "", None, _Cfg(max_bot_token="т"), dry_run=True)
    assert res["status"] == "not_configured"
    assert res["missing"] == ["канал MAX (max_chat_id)"]


# ── 4. пробный прогон ────────────────────────────────────────────────────────

def test_проба_ничего_не_шлёт_и_показывает_оба_шага():
    with _кадр() as p:
        res = mx.publish(p, "подпись", None, _Cfg(**_ЗАПОЛНЕН), dry_run=True)
    assert res["status"] == "dry_run"
    assert res["kind"] == "image"
    assert "/uploads?type=image" in res["endpoint"]
    assert "/messages" in res["endpoint"]


def test_адрес_api_берётся_из_настройки():
    """MAX уже дважды переезжал — адрес обязан меняться ответом, а не патчем."""
    cfg = _Cfg(max_api_base="https://свой.адрес/", **_ЗАПОЛНЕН)
    with _кадр() as p:
        res = mx.publish(p, "", None, cfg, dry_run=True)
    assert res["endpoint"].startswith("https://свой.адрес/uploads"), res["endpoint"]


# ── 5. отказы ДО сети ────────────────────────────────────────────────────────

def test_чужое_расширение_отклоняется_с_понятной_причиной():
    with _кадр("story.pdf") as p:
        res = mx.publish(p, "", None, _Cfg(**_ЗАПОЛНЕН), dry_run=True)
    assert res["status"] == "error"
    assert ".pdf" in res["detail"] or "картинку" in res["detail"]


def test_слишком_большой_файл_ловится_до_сети():
    """51 МБ картинки MAX не примет. Сказать это надо ДО запроса: иначе отказ
    выглядит как «ошибка API», хотя лечится пересжатием."""
    with _кадр("01.png", байт=51 * 1024 * 1024) as p:
        res = mx.publish(p, "", None, _Cfg(**_ЗАПОЛНЕН), dry_run=True)
    assert res["status"] == "error"
    assert "50 МБ" in res["detail"]


# ── 6. поиск токена вложения ─────────────────────────────────────────────────

def test_токен_находится_на_любой_глубине_ответа():
    """У картинок MAX прячет токен внутрь photos/<ключ>, у видео отдаёт прямо.
    Гадать по типу нельзя — ищем поле по имени."""
    assert mx._find_token({"token": "прямо"}) == "прямо"
    assert mx._find_token({"photos": {"ключ": {"token": "внутри"}}}) == "внутри"
    assert mx._find_token([{"a": {"b": [{"token": "глубоко"}]}}]) == "глубоко"
    assert mx._find_token({"нет": "ничего"}) is None
    assert mx._find_token({"token": ""}) is None, "пустой токен — не токен"


# ── 7. как про MAX отчитывается доктор ───────────────────────────────────────

_ОСТАЛЬНОЕ_ЗАПОЛНЕНО = {
    "upload_post_api_key": "к", "upload_post_user": "п",
    "tg_bot_token": "т", "tg_story_chat_id": "@к",
    "vk_user_token_kate": "vk1.a.ключ",
}


@contextlib.contextmanager
def _коробка(**filled):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "КонтентЗавод"
        (root / "config").mkdir(parents=True)
        (root / "config" / "factory.config.json").write_text("{}", encoding="utf-8")
        было = (config.install_root, config.config_for, doctor._ffmpeg_ok)
        config.install_root = lambda: root
        config.config_for = lambda *a, **k: _Cfg(**filled)
        doctor._ffmpeg_ok = lambda: True
        try:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = doctor.run()
            yield code, buf.getvalue()
        finally:
            config.install_root, config.config_for, doctor._ffmpeg_ok = было


def test_пустой_max_не_роняет_доктора_в_урезанный_режим():
    """Клиенту, которому MAX не нужен, код 2 навсегда — бесполезный шум."""
    with _коробка(**_ОСТАЛЬНОЕ_ЗАПОЛНЕНО) as (code, out):
        assert code == 0, "пустой MAX не должен давать урезанный режим:\n%s" % out
        assert "MAX: не подключён" in out
        assert "MAX (подключён наполовину" not in out


def test_половина_max_это_ограничение():
    """Начал подключать и не закончил — публикации нет, и это закрывается
    одним ответом мастеру. Вот это доктор обязан считать ограничением."""
    with _коробка(max_bot_token="т", **_ОСТАЛЬНОЕ_ЗАПОЛНЕНО) as (code, out):
        assert code == 2, "половина ответа — обязан быть код 2:\n%s" % out
        assert "наполовину" in out


def test_полный_max_объявлен_готовым_и_назван_постом():
    with _коробка(**dict(_ОСТАЛЬНОЕ_ЗАПОЛНЕНО, **_ЗАПОЛНЕН)) as (code, out):
        assert code == 0, out
        assert "MAX: готов" in out
        assert "пост, а не история" in out


# ── 8. доктор не расходится с драйвером по Telegram ──────────────────────────

def test_доктор_знает_про_настоящую_историю_telegram():
    """Драйвер при business connection шлёт НАСТОЯЩУЮ Story через postStory.
    Доктор эту ветку когда-то не знал и рапортовал «пост в канал» — клиент
    верит доктору, а происходило другое."""
    with _коробка(tg_business_connection_id="biz",
                  **_ОСТАЛЬНОЕ_ЗАПОЛНЕНО) as (_code, out):
        assert "НАСТОЯЩЕЙ истории" in out, out


if __name__ == "__main__":
    провалы = 0
    for имя, фн in sorted(globals().items()):
        if имя.startswith("test_") and callable(фн):
            try:
                фн()
                print("  ✔ %s" % имя)
            except AssertionError as err:
                провалы += 1
                print("  ✘ %s\n    %s" % (имя, err))
    print("\nИтог: провалено %d" % провалы)
    sys.exit(1 if провалы else 0)

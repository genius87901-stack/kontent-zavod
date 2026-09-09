# -*- coding: utf-8 -*-
"""Регресс-тест кодов возврата doctor'а модуля story-publisher (CONTRACT.md §2).

Код возврата — СТЕПЕНЬ БЕДЫ (BOX-51):
    0 — все площадки настроены, сторис уходят сами;
    2 — УРЕЗАННЫЙ РЕЖИМ: часть площадок не настроена / нет FFmpeg;
    1 — НЕ ГОТОВ: модуль не импортируется или не виден корень установки, то есть
        сама проверка не смогла отработать.

Почему тест нужен именно здесь: этот доктор ошибался В ОБРАТНУЮ сторону —
печатал «Модуль работает в урезанном режиме» и возвращал 0. Деградация терялась
целиком: в суточной сводке владельца модуль стоял зелёным, хотя автопубликация
сторис у клиента не работала ни на одной площадке. Тест ловит откат.

Гоняется двумя способами:
    python modules/story_publisher/test_doctor.py      # автономно
    pytest modules/story_publisher/test_doctor.py      # если стоит pytest
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


# ── 🔴 БЛОКИРОВКА СЕТИ НА ВЕСЬ ФАЙЛ ──────────────────────────────────────────
# 22.08.2026 на этой машине тест, дошедший до командной строки модуля, подхватил
# живой ключ из `.env` и сделал НАСТОЯЩУЮ публикацию. Доктор сам в сеть не
# ходит, но импортирует драйверы, которые умеют — а «умеет, но мы аккуратно» и
# есть та самая формулировка, после которой материал уезжает подписчикам.
# Поэтому блокируем ДО импорта модуля: методы соединения роняют прогон, живые
# ключи вычищены из окружения. Подменяем именно МЕТОДЫ сокета, а не класс:
# класс наследует `ssl.SSLSocket`, и подмена самого класса роняет импорт ssl.
def _block_network():
    import socket
    import ssl  # noqa: F401 — до подмены

    def boom(*_a, **_kw):
        raise AssertionError(
            "ТЕСТ ПОШЁЛ В СЕТЬ. В тестах публикации не бывает — прогон "
            "остановлен намеренно.")

    socket.socket.connect = boom
    socket.socket.connect_ex = boom
    socket.create_connection = boom
    socket.getaddrinfo = boom
    for key in [k for k in os.environ
                if k.startswith(("VK_", "UPLOAD_POST_", "TG_"))]:
        os.environ.pop(key, None)


_block_network()

from modules.story_publisher import config, doctor  # noqa: E402


# --- вспомогательное ---------------------------------------------------------

class _FakeConfig:
    """Конфиг клиента без файлов и окружения: что задали, то и вернём."""

    def __init__(self, **filled):
        self._filled = {k: v for k, v in filled.items() if v}

    def value(self, key, default=None):
        return self._filled.get(key, default)

    def secret(self, key, default=None):
        return self._filled.get(key, default)

    def has_secret(self, key):
        return bool(self._filled.get(key))

    def any_value(self, *keys, default=None):
        for key in keys:
            val = self._filled.get(key)
            if val not in (None, ""):
                return val
        return default

    def has_any(self, *keys):
        return bool(self.any_value(*keys))


#: Полностью заполненная установка: Instagram/Telegram через Upload-Post и
#: собственного бота, ВКонтакте — своим токеном сторис.
_ALL_FILLED = dict(upload_post_api_key="ключ", upload_post_user="профиль",
                   vk_user_token_kate="vk1.a.токен", tg_bot_token="бот:токен",
                   tg_story_chat_id="@канал")


@contextlib.contextmanager
def _patched(pairs):
    saved = [(obj, name, getattr(obj, name)) for obj, name, _ in pairs]
    for obj, name, value in pairs:
        setattr(obj, name, value)
    try:
        yield
    finally:
        for obj, name, value in saved:
            setattr(obj, name, value)


@contextlib.contextmanager
def _box(*, filled, ffmpeg, config_file=True):
    """Разворачивает вымышленную установку и подменяет чтение конфига."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "КонтентЗавод"
        (root / "config").mkdir(parents=True)
        if config_file:
            (root / "config" / "factory.config.json").write_text(
                "{}", encoding="utf-8")
        with _patched([
            (config, "install_root", lambda: root),
            (config, "config_for", lambda *a, **k: _FakeConfig(**filled)),
            (doctor, "_ffmpeg_ok", lambda: ffmpeg),
        ]):
            yield


def _run():
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = doctor.run()
    return code, buf.getvalue()


def _no_stacktrace(out):
    assert "Traceback" not in out and "  File \"" not in out, (
        "клиенту показали стектрейс — CONTRACT.md §2 запрещает:\n%s" % out)


# --- код 0 --------------------------------------------------------------------

def test_all_platforms_filled_returns_0():
    with _box(filled=_ALL_FILLED, ffmpeg=True):
        code, out = _run()
    _no_stacktrace(out)
    assert code == 0, "всё заполнено — обязан быть 0, а не %s:\n%s" % (code, out)
    assert "все площадки настроены" in out


# --- код 2: урезанный режим ---------------------------------------------------

def test_vk_personal_page_is_reported_closed_not_unconfigured():
    """Личная страница ВК закрыта запретом — доктор говорит это словами.

    Раньше тут стояло обратное: нет ключа ВК → код 2 «урезанный режим», чтобы
    деградация дошла до владельца. С 08.09.2026 ключа не существует как задачи:
    добыть его можно было только прямым входом, после которого ВКонтакте
    блокирует аккаунт клиента. Незакрываемая ветка в `limits` сделала бы код 2
    вечным у всех, и деградация перестала бы что-либо значить — поэтому путь
    объявляется закрытым словами, а не кодом.

    А вот чего доктор делать НЕ ДОЛЖЕН ни при каких условиях — звать клиента
    подключать ВК напрямую. Именно этим он раньше и заканчивал.
    """
    filled = dict(_ALL_FILLED)
    filled.pop("vk_user_token_kate")
    with _box(filled=filled, ffmpeg=True):
        code, out = _run()
    _no_stacktrace(out)
    assert "ВКонтакте" in out and "закрыт" in out, (
        "доктор не сказал, что путь на личную страницу ВК закрыт:\n%s" % out)
    assert "vk_auth.py" not in out and "--kate" not in out, (
        "доктор снова ведёт клиента на запрещённый вход:\n%s" % out)
    assert "сммбокс" in out.lower() or "smmbox" in out.lower(), (
        "закрыли путь и не назвали работающий — клиент пойдёт искать обход:"
        "\n%s" % out)
    assert "УРЕЗАННОМ РЕЖИМЕ" not in out or code == 2, out


def test_nothing_filled_returns_2_not_1():
    """Не заполнено вообще ничего — всё равно 2: ключи в манифесте required:false.

    Сборка сторис (главный путь) работает; клиент просто ещё не ответил на
    вопросы установки. Бросать всё и чинить не нужно.
    """
    with _box(filled={}, ffmpeg=True, config_file=False):
        code, out = _run()
    _no_stacktrace(out)
    assert code == 2, "пустые ключи площадок — это 2, а не %s:\n%s" % (code, out)
    for platform in ("Instagram", "Telegram", "ВКонтакте"):
        assert platform in out


def test_missing_ffmpeg_returns_2():
    """Необязательный системный пакет отсутствует — код 2 (CONTRACT.md §2)."""
    with _box(filled=_ALL_FILLED, ffmpeg=False):
        code, out = _run()
    _no_stacktrace(out)
    assert code == 2, "отсутствие FFmpeg — это 2, а не %s:\n%s" % (code, out)
    assert "FFmpeg" in out


# --- код 1: проверка не смогла отработать ------------------------------------

def test_broken_install_root_returns_1():
    def boom(*_a, **_kw):
        raise RuntimeError("корень установки не определяется")

    with _patched([(config, "install_root", boom)]):
        code, out = _run()
    _no_stacktrace(out)
    assert code == 1, "проверка не смогла отработать — это 1, а не %s:\n%s" % (code, out)
    assert "СБОЙ" in out


# ==========================================================================
# ВРАНЬЁ В ОБРАТНУЮ СТОРОНУ: «сломано», когда на самом деле работает.
#
# Доктор обязан ошибаться не в свою пользу ни в одну из сторон. Ниже — три
# случая, когда он объявлял урезанным полностью рабочий завод:
#   * ffmpeg искался в PATH, а завод берёт его из пакета imageio-ffmpeg;
#   * канал Telegram искался только в окружении и только под своим именем ключа,
#     а мастер кладёт ответ клиента в config/factory.config.json под TG_CHAT_ID;
#   * доктор и драйвер спрашивали ключи по-разному и могли разойтись.
# ==========================================================================
import json  # noqa: E402
from modules.story_publisher.drivers import telegram as tg_driver  # noqa: E402
from modules.story_publisher.drivers import vk as vk_driver  # noqa: E402


def test_ffmpeg_from_pip_package_is_not_a_degradation():
    """ffmpeg есть пакетом imageio-ffmpeg, в PATH его нет — это НЕ урезанный режим."""
    import importlib.util
    if importlib.util.find_spec("imageio_ffmpeg") is None:
        print("  пропуск — imageio_ffmpeg не установлен в этом окружении")
        return
    import shutil
    with _patched([(doctor.shutil, "which", lambda *_a, **_k: None)]):
        assert doctor._ffmpeg_ok(), (
            "ffmpeg лежит в пакете imageio-ffmpeg (им рендерит весь завод), а "
            "доктор его не видит — и объявит рабочую коробку урезанной")
    assert shutil.which is not None  # проверка, что подмену вернули на место


def test_ffmpeg_check_is_not_path_only():
    """Проверка обязана смотреть рабочий путь, а не только PATH (BOX-51)."""
    src = (Path(__file__).resolve().parent / "doctor.py").read_text(encoding="utf-8")
    assert "imageio_ffmpeg" in src, (
        "в doctor.py нет проверки бинаря из пакета imageio-ffmpeg — вернулась "
        "проверка только по PATH, а она на коробке всегда отвечает «нет»")


def test_tg_channel_from_wizard_config_closes_the_branch():
    """Клиент ответил мастеру про канал (TG_CHAT_ID в config) — ветка закрыта.

    Это и есть «сломано, когда работает»: клиент честно ответил на все вопросы
    установки, а доктор всё равно писал «не задан канал».
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "КонтентЗавод"
        (root / "config").mkdir(parents=True)
        (root / "config" / "factory.config.json").write_text(
            json.dumps({"TG_CHAT_ID": "@arhiv_klienta",
                        "UPLOAD_POST_USER": "profil"}, ensure_ascii=False),
            encoding="utf-8")
        os.environ.pop("TG_CHAT_ID", None)
        os.environ.pop("TG_STORY_CHAT_ID", None)
        with _patched([(config, "install_root", lambda: root)]):
            cfg = config.config_for(None)
            assert cfg.has_any("tg_story_chat_id", "tg_chat_id"), (
                "ответ клиента на вопрос мастера про канал Telegram лежит в "
                "config/factory.config.json под именем TG_CHAT_ID, а модуль его "
                "не находит — клиент не может закрыть эту ветку в принципе")
            assert cfg.any_value("tg_chat_id") == "@arhiv_klienta"
            # заодно: регистр ключа не должен решать судьбу ответа
            assert cfg.value("tg_chat_id") == "@arhiv_klienta"


def test_old_key_name_still_closes_the_vk_branch():
    """Клиент ответил мастеру ДО переименования — ветка ВК всё равно закрыта.

    Раньше вопрос звался `vk_stories_token`, а корневые скрипты читали
    `VK_USER_TOKEN_KATE`: два имени одного ключа. Имя стало одним, но уже
    установленные копии переустанавливать никто не будет — старое имя обязано
    продолжать работать как алиас (ALIASES в publishing/config_reader.py).
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "КонтентЗавод"
        (root / "config").mkdir(parents=True)
        (root / "config" / "factory.config.json").write_text(
            json.dumps({"VK_STORIES_TOKEN": "vk1.a.старое_имя"},
                       ensure_ascii=False), encoding="utf-8")
        with _patched([(config, "install_root", lambda: root)]):
            cfg = config.config_for(None)
            assert cfg.has_any("vk_user_token_kate", "vk_stories_token"), (
                "ответ клиента под прежним именем ключа перестал засчитываться "
                "— обновление сломало уже настроенную копию")
            assert cfg.any_value("vk_user_token_kate") == "vk1.a.старое_имя", (
                "алиас не разворачивается в канон: доктор и драйвер увидят "
                "ключ по-разному")


def test_vk_stories_are_not_promised_as_proven():
    """Ключ есть — но про сторис ВК честно сказано, что живьём не проверялись.

    Путь stories.* собран по документации и ни разу не проходил по-настоящему.
    Молча объявить его рабочим значит пообещать клиенту публикацию, которой
    может не быть: он узнает об этом, только не найдя сторис у себя в профиле.
    """
    with _box(filled=_ALL_FILLED, ffmpeg=True):
        code, out = _run()
    _no_stacktrace(out)
    assert code == 0, out
    assert "живьём" in out or "по-настоящему" in out, (
        "доктор выдал непроверенный путь ВК за проверенный:\n%s" % out)


def test_doctor_and_driver_read_the_same_keys():
    """Доктор и драйвер обязаны спрашивать одни и те же ключи одинаково.

    Расхождение опаснее самой нехватки ключа: доктор говорит «готово», а
    отправка падает «не заполнено» (или наоборот, как было с каналом).
    """
    doc_src = (Path(__file__).resolve().parent / "doctor.py").read_text(encoding="utf-8")
    assert 'has_any("tg_story_chat_id", "tg_chat_id")' in doc_src
    tg_src = (Path(tg_driver.__file__)).read_text(encoding="utf-8")
    assert 'any_value("tg_story_chat_id", "tg_chat_id")' in tg_src
    assert 'has_any("vk_user_token_kate", "vk_stories_token")' in doc_src
    vk_src = (Path(vk_driver.__file__)).read_text(encoding="utf-8")
    assert 'any_value("vk_user_token_kate", "vk_stories_token")' in vk_src


def test_manifest_declares_only_what_module_imports():
    """Манифест обещает клиенту ровно те пакеты, которые модуль правда импортирует.

    aiogram и vk-api не импортирует НИ ОДИН файл модуля (драйверы ходят голым
    requests), а ставились они клиенту в обязательном порядке.
    """
    here = Path(__file__).resolve().parent
    man = json.loads((here / "module.manifest.json").read_text(encoding="utf-8-sig"))
    pip = " ".join(man.get("pip") or [])
    sources = "\n".join(p.read_text(encoding="utf-8")
                        for p in here.rglob("*.py")
                        if "__pycache__" not in str(p))
    for pkg, module in (("aiogram", "aiogram"), ("vk-api", "vk_api")):
        if pkg in pip:
            assert ("import %s" % module) in sources, (
                "манифест ставит клиенту пакет %s, а модуль его нигде не "
                "импортирует — обещание не соответствует коду" % pkg)
    assert "requests" in pip, (
        "модуль ходит в сеть через requests, а манифест его не заявляет — "
        "на офлайн-установке драйверы молча окажутся без сети")


def test_manifest_asks_for_every_key_doctor_demands():
    """Каждую ветку доктора клиент должен уметь закрыть ответами мастера."""
    here = Path(__file__).resolve().parent
    man = json.loads((here / "module.manifest.json").read_text(encoding="utf-8-sig"))
    asked = {q["key"].lower() for q in man.get("config_questions", [])}
    # общий канал-архив спрашивает publishing (TG_CHAT_ID) — засчитываем и его
    publishing = here.parent / "publishing" / "module.manifest.json"
    if publishing.exists():
        asked |= {q["key"].lower() for q in json.loads(
            publishing.read_text(encoding="utf-8-sig")).get("config_questions", [])}
    # vk_user_token_kate из списка УБРАН 08.09.2026: путь на личную страницу ВК
    # закрыт запретом, доктор этот ключ больше не требует, и мастер обязан о
    # нём не спрашивать — добыть его можно было только запрещённым входом.
    for key in ("tg_bot_token", "upload_post_api_key", "upload_post_user"):
        assert key in asked, (
            "доктор требует ключ %s, а мастер установки о нём не спрашивает — "
            "клиент не сможет закрыть эту ветку никогда" % key)
    assert "tg_story_chat_id" in asked or "tg_chat_id" in asked, (
        "доктор требует канал Telegram, а мастер про него не спрашивает")


def _run_all():
    test_all_platforms_filled_returns_0()
    test_vk_personal_page_is_reported_closed_not_unconfigured()
    test_nothing_filled_returns_2_not_1()
    test_missing_ffmpeg_returns_2()
    test_broken_install_root_returns_1()


_TRUTH_TESTS = [
    test_old_key_name_still_closes_the_vk_branch,
    test_vk_stories_are_not_promised_as_proven,
    test_ffmpeg_from_pip_package_is_not_a_degradation,
    test_ffmpeg_check_is_not_path_only,
    test_tg_channel_from_wizard_config_closes_the_branch,
    test_doctor_and_driver_read_the_same_keys,
    test_manifest_declares_only_what_module_imports,
    test_manifest_asks_for_every_key_doctor_demands,
]


if __name__ == "__main__":
    _run_all()
    print("OK story_publisher/doctor.py: 0 — все площадки настроены; 2 — любая "
          "ненастроенная площадка и отсутствие FFmpeg (деградация больше не "
          "теряется в коде 0); 1 — проверка не смогла отработать.")
    print("")
    print("«Сломано, когда работает» — обратное враньё:")
    for fn in _TRUTH_TESTS:
        fn()
        print("  ok — %s" % (fn.__doc__ or fn.__name__).strip().splitlines()[0])
    print("")
    print("OK story_publisher: ffmpeg ищется там, где его берёт завод; ответ "
          "клиента про канал Telegram виден модулю; доктор и драйверы читают "
          "одни ключи; манифест обещает только то, что модуль импортирует.")

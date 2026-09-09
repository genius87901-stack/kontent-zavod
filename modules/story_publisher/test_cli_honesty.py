# -*- coding: utf-8 -*-
"""Регресс-тест: сторис не отчитываются об успехе, которого не было.

Три случая вранья, которые тест закрепляет закрытыми:

1. ГЕЙТ АКТИВАЦИИ ПАДАЛ ОТКРЫТЫМ. `import activation_gate` не удался → `pass`,
   и выкладка шла дальше. Достаточно было сломать импорт (переименовать файл,
   положить пустышку), чтобы проверки активации не стало вовсе. Правило проекта
   обратное (activation_gate: «отсутствие проверки не считается прохождением
   проверки»), и render_reels.py его уже соблюдает.
2. `--send` БЕЗ ТОКЕНОВ ВЫГЛЯДЕЛ УДАЧЕЙ. Печатались построчные `not_configured`,
   итоговой строки не было вовсе, код возврата — 0. Со стороны (и для Claude,
   читающего вывод) это неотличимо от успешной выкладки.
3. HTTP 200 ВЫДАВАЛСЯ ЗА ПУБЛИКАЦИЮ. Upload-Post отвечает 200 и на отказ внутри
   тела; без номера задачи (request_id) подтвердить публикацию нечем. Статус
   «accepted» в этом случае — обещание, которого никто не давал.

Гоняется двумя способами:
    python modules/story_publisher/test_cli_honesty.py      # автономно
    pytest modules/story_publisher/test_cli_honesty.py      # если стоит pytest
"""
from __future__ import annotations

import contextlib
import io
import re
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from modules.story_publisher import cli  # noqa: E402
from modules.story_publisher.drivers import instagram, telegram  # noqa: E402

_КОРЕНЬ = Path(__file__).resolve().parents[2]


class _Resp:
    def __init__(self, payload, status=200, json_ok=True):
        self._payload, self.status_code = payload, status
        self._json_ok = json_ok

    def json(self):
        if not self._json_ok:
            raise ValueError("не JSON")
        return self._payload


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
def _media():
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "story.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\n")
        yield f


#: Ключи площадок, которые модуль читает из окружения. На машине разработки они
#: приезжают из .env и ЖИВЫЕ.
_PLATFORM_KEYS = ("UPLOAD_POST_API_KEY", "UPLOAD_POST_USER", "TG_BOT_TOKEN",
                  "TG_CHAT_ID", "TG_STORY_CHAT_ID", "TG_BUSINESS_CONNECTION_ID",
                  "VK_STORIES_TOKEN", "VK_USER_TOKEN_KATE", "VK_USER_TOKEN")


class _NoNetwork:
    """Подставной `requests`, который ГРОМКО падает на любой попытке в сеть.

    ПРЕДОХРАНИТЕЛЬ, а не удобство. Первый прогон этого файла БЕЗ него сделал
    настоящую отправку: `--send` подхватил живой ключ Upload-Post из .env машины
    разработки и материал ушёл на площадку. Тест про честность не имеет права
    публиковать. Теперь так не выйдет ни при каких ключах: сеть подменена, а
    попытка ей воспользоваться проваливает тест, а не уезжает наружу.
    """

    @staticmethod
    def post(*_a, **_kw):
        raise AssertionError(
            "ТЕСТ ПОШЁЛ В СЕТЬ. В тестах отправки не бывает — значит проверяется "
            "не то, что задумано, либо предохранитель ключей перестал работать.")

    get = post


@contextlib.contextmanager
def _offline():
    """Ни ключей площадок, ни сети — как на свежей машине без настроек."""
    import modules.story_publisher.config as sp_config
    saved_env = {k: os.environ.pop(k, None) for k in _PLATFORM_KEYS}
    saved_req = sys.modules.get("requests", "нет")
    saved_loaded = sp_config._ENV_LOADED
    sys.modules["requests"] = _NoNetwork
    sp_config._ENV_LOADED = True          # .env больше не подмешивается
    try:
        yield
    finally:
        sp_config._ENV_LOADED = saved_loaded
        if saved_req == "нет":
            sys.modules.pop("requests", None)
        else:
            sys.modules["requests"] = saved_req
        for k, v in saved_env.items():
            if v is not None:
                os.environ[k] = v


def _run_cli(argv):
    buf, err = io.StringIO(), io.StringIO()
    with _offline():
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            code = cli.main(argv)
    return code, buf.getvalue() + err.getvalue()


# --- 1. гейт активации падает ЗАКРЫТЫМ ---------------------------------------

def test_missing_activation_gate_stops_send_on_client_build():
    """Гейта нет, режим жёсткий → выкладка ОСТАНОВЛЕНА, а не «поехали дальше»."""
    saved = sys.modules.get("activation_gate", "нет")
    sys.modules["activation_gate"] = None      # так `import` бросит ImportError
    os.environ["KZ_ENFORCE_ACTIVATION"] = "1"
    try:
        with _media() as f:
            code, out = _run_cli(["--media", str(f), "--to", "tg", "--send"])
    finally:
        os.environ.pop("KZ_ENFORCE_ACTIVATION", None)
        if saved == "нет":
            sys.modules.pop("activation_gate", None)
        else:
            sys.modules["activation_gate"] = saved
    assert code == 2, (
        "гейт активации сломан, копия клиентская, а выкладка всё равно пошла "
        "(код %s) — защиту снимает одно переименование файла:\n%s" % (code, out))
    assert "ОСТАНОВЛЕНА" in out


def test_missing_activation_gate_does_not_stop_dev_checkout():
    """В мягком режиме (наш checkout) сломанный гейт работу не останавливает."""
    saved = sys.modules.get("activation_gate", "нет")
    sys.modules["activation_gate"] = None
    os.environ.pop("KZ_ENFORCE_ACTIVATION", None)
    try:
        with _media() as f:
            code, out = _run_cli(["--media", str(f), "--to", "tg", "--send"])
    finally:
        if saved == "нет":
            sys.modules.pop("activation_gate", None)
        else:
            sys.modules["activation_gate"] = saved
    assert code != 2, "разработка встала из-за отсутствия гейта:\n%s" % out
    assert "мягкий режим" in out


# --- 2. итог реального прогона называет вещи своими именами -------------------

def test_send_without_tokens_says_it_went_nowhere():
    """`--send` без ключей обязан сказать «НИКУДА», а не промолчать."""
    with _media() as f:
        code, out = _run_cli(["--media", str(f), "--to", "tg,vk", "--send"])
    assert "Ушло на площадки: НИКУДА" in out, (
        "прогон без токенов не отличить от удачной выкладки:\n%s" % out)
    assert "выложить руками" in out
    assert code != 2, "выкладка была остановлена не тем предохранителем"


def test_dry_run_still_says_nothing_was_sent():
    """Пробный прогон по-прежнему предупреждает — прежнее правило цело."""
    with _media() as f:
        _code, out = _run_cli(["--media", str(f), "--to", "tg"])
    assert "ПРОБНЫЙ ПРОГОН" in out and "Ничего не отправлено" in out


# --- 3. HTTP 200 без подтверждения ≠ публикация -------------------------------

@contextlib.contextmanager
def _fake_requests(resp):
    """Подменяет `requests` заранее заданным ответом. Сеть не трогается."""
    import types
    saved = sys.modules.get("requests", "нет")
    sys.modules["requests"] = types.SimpleNamespace(post=lambda *a, **k: resp)
    try:
        yield
    finally:
        if saved == "нет":
            sys.modules.pop("requests", None)
        else:
            sys.modules["requests"] = saved


def test_smmbox_path_can_never_say_accepted():
    """🔴 Главный инвариант нового пути (04.09.2026).

    Сторис через SmmBox уходят «вслепую»: сервис отвечает «принято», а прочитать,
    вышла ли история, у него нечем — шесть методов, чтения нет. В самом
    smmbox_post это записано словами «отказ выглядит как успех», и там же случай,
    когда появившаяся через три минуты история оказалась ЧУЖОЙ.

    Раньше честность проверялась в каждой функции отправки по отдельности.
    Теперь она обеспечена УСТРОЙСТВОМ: у общего слоя нет ветки, которая
    возвращала бы `accepted`. Проверяем это по исходнику — так проверка не
    зависит от того, дойдёт ли выполнение до конкретной строки.
    """
    src = io.open(_КОРЕНЬ / "modules" / "story_publisher" / "drivers" /
                  "smmbox_story.py", encoding="utf-8").read()
    тело = src[src.index("def publish_story("):]
    assert '"accepted"' not in тело, (
        "в пути через SmmBox появился статус accepted — сервис не умеет "
        "подтверждать доставку, и говорить «опубликовано» мы не вправе")
    assert '"unconfirmed"' in тело, "успешный ответ обязан быть unconfirmed"
    assert "посмотрите глазами" in тело, (
        "человеку не сказано, что проверить надо самому")


def test_telegram_without_bot_token_goes_nowhere():
    """Запасного пути через сторонний сервис больше нет — и это видно.

    Раньше незаполненный токен бота МОЛЧА уводил публикацию в чужой сервис, и
    клиент никогда не узнавал, что его собственный канал не подключён.
    """
    class _Cfg:
        client_id = "тест"
        def secret(self, *a, **k): return None
        def value(self, *a, **k): return None
        def any_value(self, *a, **k): return None

    with _media() as f:
        intent = telegram.publish(f, "подпись", None, _Cfg(), dry_run=False)
    assert intent["status"] == "not_configured", intent
    assert "нечем" in intent["detail"], intent["detail"]


def test_drivers_have_no_third_party_service_left():
    """В едущих клиенту драйверах сервиса быть не должно — решение владельца."""
    папка = _КОРЕНЬ / "modules" / "story_publisher" / "drivers"
    задеты = []
    for f in sorted(папка.glob("*.py")):
        if re.search(r"upload[_-]?post", io.open(f, encoding="utf-8").read(), re.I):
            задеты.append(f.name)
    assert not задеты, "сервис всё ещё упоминается: %s" % ", ".join(задеты)


def test_unconfirmed_is_not_counted_as_sent():
    """В итоговой строке «unconfirmed» не попадает в «ушло на площадки»."""
    assert cli._UNCONFIRMED not in cli._SENT_STATUSES, (
        "неподтверждённая отправка снова считается успешной")


_TESTS = [
    test_missing_activation_gate_stops_send_on_client_build,
    test_missing_activation_gate_does_not_stop_dev_checkout,
    test_send_without_tokens_says_it_went_nowhere,
    test_dry_run_still_says_nothing_was_sent,
    test_smmbox_path_can_never_say_accepted,
    test_telegram_without_bot_token_goes_nowhere,
    test_drivers_have_no_third_party_service_left,
    test_unconfirmed_is_not_counted_as_sent,
]


if __name__ == "__main__":
    for fn in _TESTS:
        fn()
        print("  ok — %s" % (fn.__doc__ or fn.__name__).strip().splitlines()[0])
    print("")
    print("OK story_publisher/cli.py: гейт активации падает закрытым, реальный "
          "прогон честно называет, куда ничего не ушло, а HTTP 200 без "
          "подтверждения больше не выдаётся за публикацию.")

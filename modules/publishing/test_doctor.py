# -*- coding: utf-8 -*-
"""Регресс-тесты шва «настройки клиента → публикация» (модуль publishing).

ЧТО ЗДЕСЬ ЛОВИТСЯ (аудит 22.08.2026, перед тем как клиенты сели заводить ключи).

1. КЛЮЧИ УЕЗЖАЛИ НЕ ТУДА. Мастер установки раскладывает ответы клиента ПО ДВУМ
   файлам: помеченные `"secret": true` — в `.env`, остальные — в
   `config/factory.config.json`. Корневые скрипты публикации читали только
   `.env`. Значит `UPLOAD_POST_USER` (имя профиля, помечено «не секрет»),
   `VK_GROUP_ID`, `VK_APP_ID` и `TG_CHAT_ID` не доходили до боевого кода ВООБЩЕ,
   и первая же публикация падала на настроенной коробке. При этом doctor читал
   оба файла и на той же раскладке отвечал «готово» — клиент видел зелёный
   отчёт и падающую публикацию.

2. ПРОВАЛ ВЫГЛЯДЕЛ КАК УСПЕХ. autopost.py печатал «НЕ ВЫШЛО: {…}» и уходил с
   нулём. release.py судит об исходе только по коду возврата, поэтому отказанная
   площадка попадала в отчёт выпуска как «ОК» — просто без ссылки.

НИКАКОЙ СЕТИ. Все прогоны идут на заглушках: `post_with_retry` и `wait_done`
подменяются, `_run` в release.py подменяется. Ни одного реального запроса и ни
одной реальной публикации — главный закон проекта.

Гоняется двумя способами:
    python modules/publishing/test_doctor.py      # автономно
    pytest modules/publishing/test_doctor.py      # если стоит pytest
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import autopost                                    # noqa: E402
import release                                     # noqa: E402


# ── песочница: коробка клиента с двумя файлами настроек ─────────────────────

def _box(tmp: str, config: dict, env: dict) -> Path:
    """Коробка завода: config/factory.config.json + .env + маркер корня."""
    box = Path(tmp) / "коробка"
    (box / "config").mkdir(parents=True)
    (box / "config" / "factory.config.json").write_text(
        json.dumps(config, ensure_ascii=False), encoding="utf-8")
    (box / ".env").write_text(
        "".join("%s=%s\n" % (k, v) for k, v in env.items()), encoding="utf-8")
    (box / ".factory_root").write_text("маркер корня", encoding="utf-8")
    return box


def _py(code: str, box: Path):
    """Прогон кода в ОТДЕЛЬНОМ процессе с корнем установки = коробка.

    Отдельный процесс здесь не роскошь: и `.env` завода-владельца, и кеш
    install/factory_config.py живут в переменных процесса. В одном процессе
    настоящие ключи владельца протекли бы в проверку и она бы «прошла» на
    пустой коробке. Заодно вычищаем из окружения всё, что могло бы подменить
    ответ клиента.
    """
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("UPLOAD_POST", "VK_", "TG_"))}
    env["FACTORY_ROOT"] = str(box)
    env["PYTHONPATH"] = str(REPO)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    p = subprocess.run([sys.executable, "-c", code], cwd=str(REPO), env=env,
                       text=True, encoding="utf-8", errors="replace",
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=180)
    return p.returncode, p.stdout


# ══════════════════════════════════════════════════════════════════════════
# ЗАДАЧА 1. Ключи из ОБОИХ файлов доходят до боевого кода.
# ══════════════════════════════════════════════════════════════════════════

def test_несекретный_ключ_доходит_до_creds():
    """UPLOAD_POST_USER лежит в конфиге (не в .env) — и всё равно доходит.

    Ровно та раскладка, которую делает мастер установки по манифесту:
    ключ помечен секретом, имя профиля — нет.
    """
    with tempfile.TemporaryDirectory() as tmp:
        box = _box(tmp,
                   {"UPLOAD_POST_USER": "профиль_клиента"},
                   {"UPLOAD_POST_API_KEY": "ключ-из-кабинета"})
        code, out = _py(
            "import autopost;"
            "k,u = autopost.creds();"
            "print('КЛЮЧ', k); print('ПРОФИЛЬ', u)", box)
        assert code == 0, (
            "creds() остановил публикацию на полностью настроенной коробке:\n"
            + out)
        assert "КЛЮЧ ключ-из-кабинета" in out, out
        assert "ПРОФИЛЬ профиль_клиента" in out, (
            "имя профиля из config/factory.config.json не доехало до creds() — "
            "это и есть баг, из-за которого падала первая публикация:\n" + out)


def test_старое_чтение_только_env_этот_ключ_не_видит():
    """Доказательство «до»: чтение одного лишь .env профиля НЕ находит.

    Это прежнее поведение autopost.load_env(). Тест закрепляет, что баг был
    настоящим, а не показался: тот же самый файл настроек, тот же клиент.
    """
    with tempfile.TemporaryDirectory() as tmp:
        box = _box(tmp,
                   {"UPLOAD_POST_USER": "профиль_клиента"},
                   {"UPLOAD_POST_API_KEY": "ключ-из-кабинета"})
        code, out = _py(
            "import autopost, os;"
            "env = autopost.load_env(os.environ['FACTORY_ROOT'] + os.sep + '.env');"
            "print('ТОЛЬКО_ENV', sorted(env));"
            "print('ЕСТЬ_ПРОФИЛЬ', 'UPLOAD_POST_USER' in env)", box)
        assert code == 0, out
        assert "ЕСТЬ_ПРОФИЛЬ False" in out, (
            "в .env вдруг оказалось имя профиля — песочница собрана неверно:\n"
            + out)


def test_доктор_и_боевой_код_видят_одно_и_то_же():
    """Корень проблемы: доктор говорил «готово», а публикация падала.

    Проверяем не текст отчёта, а сами значения: у доктора и у creds() они
    обязаны совпадать до буквы на одной и той же раскладке файлов.
    """
    with tempfile.TemporaryDirectory() as tmp:
        box = _box(tmp,
                   {"UPLOAD_POST_USER": "профиль_клиента", "VK_GROUP_ID": "12345",
                    "TG_CHAT_ID": "@архив"},
                   {"UPLOAD_POST_API_KEY": "ключ-из-кабинета",
                    "VK_USER_TOKEN": "vk1.a.токен", "TG_BOT_TOKEN": "бот:токен"})
        code, out = _py(
            "import os, autopost;"
            "from modules.publishing import doctor;"
            "vals = doctor.read_config(__import__('pathlib').Path("
            "os.environ['FACTORY_ROOT']));"
            "k,u = autopost.creds();"
            "print('СОГЛАСНЫ', vals.get('UPLOAD_POST_API_KEY') == k"
            " and vals.get('UPLOAD_POST_USER') == u);"
            "print('ДОКТОР_ГРУППА', vals.get('VK_GROUP_ID'));"
            "print('ДОКТОР_ЧАТ', vals.get('TG_CHAT_ID'))", box)
        assert code == 0, out
        assert "СОГЛАСНЫ True" in out, (
            "доктор и боевой код читают ключи по-разному — это и есть корень "
            "проблемы «доктор говорит готово, публикация падает»:\n" + out)
        assert "ДОКТОР_ГРУППА 12345" in out, out
        assert "ДОКТОР_ЧАТ @архив" in out, out


def test_vk_и_telegram_берут_свои_номера_из_конфига():
    """VK_GROUP_ID / VK_APP_ID / TG_CHAT_ID — несекретные, лежат в конфиге."""
    with tempfile.TemporaryDirectory() as tmp:
        box = _box(tmp,
                   {"VK_GROUP_ID": "12345", "VK_APP_ID": "777", "TG_CHAT_ID": "@архив"},
                   {"VK_USER_TOKEN": "vk1.a.токен", "TG_BOT_TOKEN": "бот:токен"})
        code, out = _py(
            "import vkpost, vkcarousel, vk_wall_post, tg_send;"
            "print('VKPOST', vkpost.load_env().get('VK_GROUP_ID'),"
            " vkpost.load_env().get('VK_APP_ID'));"
            "print('VKCAROUSEL', vkcarousel.load_env().get('VK_GROUP_ID'));"
            "print('VKWALL', vk_wall_post.load_env().get('VK_GROUP_ID'));"
            "print('TG', tg_send.load_env().get('TG_CHAT_ID'),"
            " tg_send.load_env().get('TG_BOT_TOKEN'))", box)
        assert code == 0, out
        assert "VKPOST 12345 777" in out, (
            "vkpost не видит номер сообщества/приложения из конфига:\n" + out)
        assert "VKCAROUSEL 12345" in out, out
        assert "VKWALL 12345" in out, out
        assert "TG @архив бот:токен" in out, out


def test_env_сильнее_конфига():
    """Один ключ в обоих файлах — выигрывает .env (там его держит установщик)."""
    with tempfile.TemporaryDirectory() as tmp:
        box = _box(tmp, {"VK_GROUP_ID": "из-конфига"}, {"VK_GROUP_ID": "из-env"})
        code, out = _py(
            "import vkpost; print('ГРУППА', vkpost.load_env()['VK_GROUP_ID'])", box)
        assert code == 0, out
        assert "ГРУППА из-env" in out, out


def test_число_в_конфиге_не_теряется():
    """Номер сообщества, записанный числом, читается как заполненный.

    Мастер спрашивает «только цифры», и значение вполне может лечь в json
    числом. Прежний doctor такие ключи молча выбрасывал и объявлял заполненное
    пустым.
    """
    with tempfile.TemporaryDirectory() as tmp:
        box = _box(tmp, {"VK_GROUP_ID": 12345}, {})
        code, out = _py(
            "import vkpost; print('ГРУППА', vkpost.load_env().get('VK_GROUP_ID'))",
            box)
        assert code == 0, out
        assert "ГРУППА 12345" in out, out


def test_пустой_ответ_считается_незаполненным():
    """Клиент прошёл вопрос, ничего не введя → это «нет значения», а не ''."""
    with tempfile.TemporaryDirectory() as tmp:
        box = _box(tmp, {"UPLOAD_POST_USER": "   "},
                   {"UPLOAD_POST_API_KEY": "ключ"})
        code, out = _py("import autopost; autopost.creds()", box)
        assert code != 0, ("пустое имя профиля принято за заполненное — "
                           "публикация уйдёт в никуда:\n" + out)
        assert "мастер установки" in out, (
            "человеку не сказали, как чинить:\n" + out)


# ══════════════════════════════════════════════════════════════════════════
# ЗАДАЧА 2. Провал даёт ненулевой код возврата.
# ══════════════════════════════════════════════════════════════════════════

def test_verdict_разбирает_исход():
    """Таблица исходов: что считается успехом, отказом и неизвестностью."""
    ok = {"status": "completed", "results": [{"success": True, "post_url": "ссылка"}]}
    assert autopost.verdict(ok)[0] == autopost.EXIT_OK

    отказ = {"status": "completed",
             "results": [{"success": True}, {"success": False, "error": "нет"}]}
    assert autopost.verdict(отказ)[0] == autopost.EXIT_REJECTED, (
        "площадка ответила отказом, а исход посчитан успешным — ровно тот "
        "случай, из-за которого провал попадал в отчёт выпуска как «ОК»")

    for st in ("failed", "error"):
        assert autopost.verdict({"status": st})[0] == autopost.EXIT_REJECTED, st

    assert autopost.verdict({"status": "timeout", "waited": 600})[0] == \
        autopost.EXIT_UNCONFIRMED
    assert autopost.verdict({"status": "status_error", "http": 502, "body": ""})[0] == \
        autopost.EXIT_UNCONFIRMED
    assert autopost.verdict({"status": "completed", "results": []})[0] == \
        autopost.EXIT_UNCONFIRMED, "нечем подтвердить публикацию — это не успех"
    assert autopost.verdict({"status": "новое-слово"})[0] == \
        autopost.EXIT_UNCONFIRMED


def test_коды_возврата_не_сталкиваются():
    """Код 2 занят гейтом активации — autopost его не выдаёт."""
    assert autopost.EXIT_OK == 0
    assert autopost.EXIT_REJECTED == 1
    assert autopost.EXIT_UNCONFIRMED == 3
    assert 2 not in (autopost.EXIT_REJECTED, autopost.EXIT_UNCONFIRMED)


# Прогон autopost.main() ЦЕЛИКОМ на заглушках: ни одного сетевого вызова.
_ПРОГОН = """
import json, sys, types
# --short-ok: подпись здесь — заглушка на 13 символов, а с 25.08 (патч 1.3.29)
# autopost останавливает недожатую подпись ДО отправки. Без клапана прогон
# обрывался на гейте и ни один код выхода доктора не проверялся вовсе —
# четыре теста были красными у каждого клиента с 25.08 по 09.09.2026.
sys.argv = ["autopost.py", "--dir", %(dir)s, "--caption-file", %(cap)s,
            "--to", "instagram", "--send", "--short-ok"]
import autopost

# Гейт активации — заглушка: наружу и так ничего не уходит (сеть подменена).
gate = types.ModuleType("activation_gate")
gate.allow = lambda action: True
sys.modules["activation_gate"] = gate

class Ответ:
    status_code = 200
    text = "{}"
    def json(self):
        return {"request_id": "заявка-1"}

# СЕТИ НЕТ: обе точки выхода наружу подменены.
autopost.post_with_retry = lambda *a, **k: Ответ()
autopost.wait_done = lambda *a, **k: json.loads(%(result)s)
autopost.persist_published = lambda *a, **k: None

autopost.main()
"""


def _прогон(box: Path, tmp: str, result: dict):
    """autopost.main() от начала до конца на заглушках → (код, вывод)."""
    d = Path(tmp) / "out_проба"
    d.mkdir(exist_ok=True)
    (d / "01.png").write_bytes("не настоящий png".encode("utf-8"))
    cap = Path(tmp) / "caption_проба.txt"
    cap.write_text("подпись поста", encoding="utf-8")
    code = _ПРОГОН % {"dir": json.dumps(str(d)), "cap": json.dumps(str(cap)),
                      "result": json.dumps(json.dumps(result, ensure_ascii=False))}
    return _py(code, box)


def _рабочая_коробка(tmp: str) -> Path:
    return _box(tmp, {"UPLOAD_POST_USER": "профиль_клиента"},
                {"UPLOAD_POST_API_KEY": "ключ-из-кабинета"})


def test_отказ_площадки_даёт_ненулевой_код():
    """Сервис ответил success:false — процесс обязан уйти с ошибкой."""
    with tempfile.TemporaryDirectory() as tmp:
        box = _рабочая_коробка(tmp)
        code, out = _прогон(box, tmp, {
            "status": "completed",
            "results": [{"success": False, "error": "profile not found"}]})
        assert "НЕ ВЫШЛО" in out, out
        assert code == autopost.EXIT_REJECTED, (
            "процесс ушёл с кодом %s при отказе площадки — release.py посчитает "
            "эту ногу успешной:\n%s" % (code, out))


def test_успех_остаётся_нулём():
    """Всё вышло — код 0, иначе выпуск начнёт кричать на рабочей публикации."""
    with tempfile.TemporaryDirectory() as tmp:
        box = _рабочая_коробка(tmp)
        code, out = _прогон(box, tmp, {
            "status": "completed",
            "results": [{"success": True, "post_url": "https://пример/пост"}]})
        assert code == 0, out
        assert "ОПУБЛИКОВАНО" in out, out


def test_молчание_сервиса_даёт_код_неизвестности():
    """timeout — это не успех и не отказ: код 3, и словами про дубль."""
    with tempfile.TemporaryDirectory() as tmp:
        box = _рабочая_коробка(tmp)
        code, out = _прогон(box, tmp, {"status": "timeout", "waited": 600})
        assert code == autopost.EXIT_UNCONFIRMED, out
        assert "НЕ ПОДТВЕРЖДЁН" in out, out
        assert "дубль" in out, ("человеку не сказали, почему нельзя просто "
                                "повторить:\n" + out)


def test_отложенная_публикация_осталась_нулевой():
    """--queue и --at выходят сразу после ответа сервиса — код 0, как и было.

    Это осознанное поведение: мы отвечаем за ПРИЁМ заявки, а он состоялся, и
    статуса на этот момент ещё не существует. Тест держит его на месте, чтобы
    правка кодов возврата случайно не превратила отложенный выпуск в «сбой».
    Честность здесь половинчатая (см. отчёт), поэтому проверяем ещё и то, что
    человеку сказано словами: пост не подтверждён, автоматом никто не проверит.
    """
    with tempfile.TemporaryDirectory() as tmp:
        box = _рабочая_коробка(tmp)
        d = Path(tmp) / "out_проба"
        d.mkdir()
        (d / "01.png").write_bytes("не настоящий png".encode("utf-8"))
        cap = Path(tmp) / "caption_проба.txt"
        cap.write_text("подпись поста", encoding="utf-8")
        код = _ПРОГОН % {"dir": json.dumps(str(d)), "cap": json.dumps(str(cap)),
                         "result": json.dumps("{}")}
        код = код.replace('"--to", "instagram", "--send"',
                          '"--to", "instagram", "--send", "--queue"')
        rc, out = _py(код, box)
        assert rc == 0, out
        assert "ОТЛОЖЕНА" in out, out
        assert "НИКТО не проверит" in out, (
            "промолчали о том, что исход отложенного поста никто не проверяет:\n"
            + out)


def test_подсказка_длинного_заголовка_не_выдаёт_себя_за_оплату():
    """«title is too long» — не про деньги, и путать эти две беды нельзя."""
    h = autopost.service_hint(400, "title is too long (1234), maximum 1024")
    assert "оплат" not in h.lower() and "тариф" not in h.lower(), h
    assert "подпись" in h.lower(), h


def test_предохранитель_send_на_месте():
    """Без --send не уходит ничего — главный закон проекта.

    Проверяем не текстом в исходнике, а поведением: тот же прогон без --send
    обязан ЗАКОНЧИТЬСЯ пробным прогоном, ни разу не спросив ключи и не дойдя до
    отправки.
    """
    with tempfile.TemporaryDirectory() as tmp:
        # Коробка ПУСТАЯ: если скрипт полезет за ключами, он упадёт — и мы это
        # увидим. Пробный прогон за ключами ходить не должен вовсе.
        box = _box(tmp, {}, {})
        d = Path(tmp) / "out_проба"
        d.mkdir()
        (d / "01.png").write_bytes("не настоящий png".encode("utf-8"))
        cap = Path(tmp) / "caption_проба.txt"
        cap.write_text("подпись поста", encoding="utf-8")
        code, out = _py(
            "import sys; sys.argv = ['autopost.py', '--dir', %s,"
            " '--caption-file', %s, '--to', 'instagram'];"
            "import autopost; autopost.main()"
            % (json.dumps(str(d)), json.dumps(str(cap))), box)
        assert code == 0, out
        assert "ПРОБНЫЙ ПРОГОН" in out, out
        assert "Ничего не отправлено" in out, out


def test_предохранитель_объявлен_во_всех_скриптах():
    """Флаг --send остался нетронутым в каждом файле публикации."""
    for имя in ("autopost.py", "vkpost.py", "vkcarousel.py", "vk_wall_post.py",
                "tg_send.py", "release.py"):
        текст = (REPO / имя).read_text(encoding="utf-8")
        assert '"--send"' in текст, "предохранитель --send пропал из " + имя


# ══════════════════════════════════════════════════════════════════════════
# ЗАДАЧА 2 (продолжение). release.py показывает провал как провал.
# ══════════════════════════════════════════════════════════════════════════

def _план(tmp: str) -> Path:
    """Манифест выпуска из двух карусельных единиц (только основная нога)."""
    d = Path(tmp)
    (d / "out_проба").mkdir(exist_ok=True)
    (d / "out_проба" / "01.png").write_bytes("не настоящий png".encode("utf-8"))
    (d / "caption_проба.txt").write_text("подпись", encoding="utf-8")
    план = {"выпуск": "проба", "единицы": [
        {"имя": "c1", "тип": "карусель", "папка": "out_проба",
         "подпись": "caption_проба.txt", "ноги": ["основная"]},
        {"имя": "c2", "тип": "карусель", "папка": "out_проба",
         "подпись": "caption_проба.txt", "ноги": ["основная"]},
    ]}
    p = d / "release.json"
    p.write_text(json.dumps(план, ensure_ascii=False), encoding="utf-8")
    return p


def _выпуск(tmp: str, коды):
    """release.main() с ПОДМЕНЁННЫМ запуском дочерних скриптов → (код, вывод).

    Ни один дочерний процесс не стартует: `_run` заменён на выдачу заранее
    заданных кодов возврата. Сети нет по построению.
    """
    план = _план(tmp)
    очередь = list(коды)

    def подменённый_run(cmd, label):
        код = очередь.pop(0)
        return {"нога": label, "код": код, "ссылки": [], "пропущено": [],
                "сек": 1, "лог": ""}

    buf = io.StringIO()
    итог = 0
    with mock.patch.object(release, "_run", подменённый_run), \
            mock.patch.object(release, "HERE", tmp), \
            mock.patch.object(sys, "argv",
                              ["release.py", "--plan", str(план), "--send"]), \
            contextlib.redirect_stdout(buf):
        try:
            release.main()
        except SystemExit as e:
            итог = e.code if isinstance(e.code, int) else 1
    return итог, buf.getvalue()


def test_выпуск_видит_отказ_как_отказ():
    """Нога с кодом 1 → «СБОЙ» в отчёте и ненулевой код у самого выпуска."""
    with tempfile.TemporaryDirectory() as tmp:
        код, out = _выпуск(tmp, [0, 1])
        assert "СБОЙ" in out, out
        assert "НЕ ВЫШЛО: 1" in out, out
        assert код == 1, "выпуск завершился успехом при упавшей ноге:\n" + out


def test_выпуск_отделяет_неподтверждённое_от_отказа():
    """Код 3 показывается отдельно — повторять его вслепую нельзя."""
    with tempfile.TemporaryDirectory() as tmp:
        код, out = _выпуск(tmp, [0, 3])
        assert "НЕ ПОДТВЕРЖДЕНО" in out, out
        assert "дубль" in out, ("не предупредили про дубль при повторе:\n" + out)
        assert "НЕ ВЫШЛО" not in out, (
            "неизвестный исход выдан за отказ — человек повторит и получит "
            "дубль:\n" + out)
        assert код == 1, out


def test_выпуск_молчит_когда_всё_вышло():
    """Все ноги нулевые → ни «СБОЙ», ни «НЕ ПОДТВЕРЖДЕНО», код 0."""
    with tempfile.TemporaryDirectory() as tmp:
        код, out = _выпуск(tmp, [0, 0])
        assert код == 0, out
        assert "СБОЙ" not in out and "НЕ ПОДТВЕРЖДЕНО" not in out, out
        assert "ПОСМОТРИ ГЛАЗАМИ" in out, "пропала просьба проверить глазами"


# ══════════════════════════════════════════════════════════════════════════
# ЗАДАЧА 3. Отказ сервиса объяснён по-русски.
# ══════════════════════════════════════════════════════════════════════════

def test_подсказка_профиль_не_найден():
    """Самый частый отказ у нового клиента — и раньше подсказки на него не было."""
    h = autopost.service_hint(400, '{"error": "Profile not found"}')
    assert "профил" in h.lower(), h
    assert "upload-post.com" in h, "не сказали, куда идти чинить: " + h
    assert "БУКВА В БУКВУ" in h, "не сказали, что название важно до буквы: " + h
    assert "API" not in h, "в подсказке для человека появилось слово API: " + h


def test_подсказка_соцсеть_не_подключена():
    h = autopost.service_hint(400, '{"error": "instagram account not connected"}')
    assert "подключ" in h.lower(), h
    assert "API" not in h, h


def test_подсказка_неверный_ключ():
    for статус, тело in ((401, "Unauthorized"), (200, "Invalid API key")):
        h = autopost.service_hint(статус, тело)
        assert "ключ" in h.lower(), (статус, h)
        assert "upload-post.com" in h, (статус, h)


def test_подсказка_оплата_осталась():
    h = autopost.service_hint(402, "payment required")
    assert "оплат" in h.lower(), h


def test_подсказка_всегда_говорит_что_делать():
    """Любая подсказка отвечает на два вопроса: что случилось и что сделать."""
    случаи = [(400, "Profile not found"),
              (400, "account not connected"),
              (401, "Unauthorized"),
              (402, "payment required"),
              (400, "something odd happened")]
    for статус, тело in случаи:
        h = autopost.service_hint(статус, тело)
        assert "ЧТО СЛУЧИЛОСЬ" in h and "ЧТО СДЕЛАТЬ" in h, (статус, тело, h)
        assert len(h.splitlines()) <= 6, (
            "подсказка длиннее шести строк — release.py покажет только хвост "
            "и обрежет её: %r" % h)


def test_незнакомый_отказ_не_выдумывает_причину():
    """На 500 подсказки нет — врать про причину хуже, чем молчать."""
    assert autopost.service_hint(500, "internal server error") == ""


# ── прогон ──────────────────────────────────────────────────────────────────

_ТЕСТЫ = [
    test_несекретный_ключ_доходит_до_creds,
    test_старое_чтение_только_env_этот_ключ_не_видит,
    test_доктор_и_боевой_код_видят_одно_и_то_же,
    test_vk_и_telegram_берут_свои_номера_из_конфига,
    test_env_сильнее_конфига,
    test_число_в_конфиге_не_теряется,
    test_пустой_ответ_считается_незаполненным,
    test_verdict_разбирает_исход,
    test_коды_возврата_не_сталкиваются,
    test_отказ_площадки_даёт_ненулевой_код,
    test_успех_остаётся_нулём,
    test_молчание_сервиса_даёт_код_неизвестности,
    test_отложенная_публикация_осталась_нулевой,
    test_предохранитель_send_на_месте,
    test_предохранитель_объявлен_во_всех_скриптах,
    test_выпуск_видит_отказ_как_отказ,
    test_выпуск_отделяет_неподтверждённое_от_отказа,
    test_выпуск_молчит_когда_всё_вышло,
    test_подсказка_профиль_не_найден,
    test_подсказка_соцсеть_не_подключена,
    test_подсказка_неверный_ключ,
    test_подсказка_оплата_осталась,
    test_подсказка_длинного_заголовка_не_выдаёт_себя_за_оплату,
    test_подсказка_всегда_говорит_что_делать,
    test_незнакомый_отказ_не_выдумывает_причину,
]


if __name__ == "__main__":
    провалы = 0
    for fn in _ТЕСТЫ:
        первая = (fn.__doc__ or fn.__name__).strip().splitlines()[0]
        try:
            fn()
        except AssertionError as e:
            провалы += 1
            print("  ПРОВАЛ — %s\n           %s" % (первая, e))
        else:
            print("  ok — %s" % первая)
    print("")
    if провалы:
        print("ПРОВАЛОВ: %d" % провалы)
        sys.exit(1)
    print("OK publishing: ключи из ОБОИХ файлов настроек доходят до боевого "
          "кода, доктор и публикация читают их одинаково, отказ даёт ненулевой "
          "код и виден в отчёте выпуска, а отказ сервиса объяснён по-русски.")

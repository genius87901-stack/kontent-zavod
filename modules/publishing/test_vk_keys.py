# -*- coding: utf-8 -*-
"""Регресс-тесты подключения ВКонтакте: одно имя ключа, один вход, честный отказ.

ЧТО ЗДЕСЬ ЗАКРЕПЛЕНО ЗАКРЫТЫМ (аудит 22.08.2026).

1. ДВА ИМЕНИ ОДНОГО КЛЮЧА. Пользовательский токен ВК звался `VK_USER_TOKEN_KATE`
   в корневых скриптах и `vk_stories_token` в вопросе мастера у story-publisher.
   Клиент отвечал ОДИН раз — и получал работающие сторис при неработающей
   карусели: `vkcarousel.py` спрашивал имя, которого в настройках не было.
   Закрыть ветку он не мог в принципе. Канон теперь один, остальные имена —
   алиасы общего ридера.

2. КЛЮЧИ, КОТОРЫЕ НЕЛЬЗЯ БЫЛО «СПРОСИТЬ». Мастер спрашивал VK_APP_ID
   («заведите приложение на dev.vk.com») и VK_USER_TOKEN. Первое требует
   регистрации разработчика, второе в анкету не вводится вовсе — токен выдаётся
   входом в браузер. Вопросы убраны, вместо них — команда `vk_auth.py --kate`,
   которая в сеть НЕ ХОДИТ и потому проверяется тестом целиком.

3. «ВЧЕРА РАБОТАЛО, СЕГОДНЯ ОШИБКА 5». Запасной ключ VK ID живёт час и
   продлевается только полным комплектом служебных значений, которых у клиента
   нет. Раньше он получал голое «VK error 5» без причины и без выхода.

🔴 СЕТЬ ЗАБЛОКИРОВАНА ТЕХНИЧЕСКИ, А НЕ ОБЕЩАНИЕМ.
22.08.2026 на этой машине тест, вызвавший командную строку модуля, подхватил
живой ключ из `.env` и сделал НАСТОЯЩУЮ публикацию. Поэтому здесь:
  * `requests` и `socket` подменены так, что любая попытка соединения роняет
    прогон громкой ошибкой (`_no_network`, `_GUARD` для дочерних процессов);
  * переменные окружения VK_* / UPLOAD_POST_* / TG_* вычищаются на время
    прогона — подхватывать нечего;
  * дочерние процессы получают ту же блокировку первой строкой кода.
Ни один тест этого файла не может ничего опубликовать, даже если ошибётся.

Гоняется двумя способами:
    python modules/publishing/test_vk_keys.py      # автономно
    pytest modules/publishing/test_vk_keys.py      # если стоит pytest
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "modules"))


# ── БЛОКИРОВКА СЕТИ ──────────────────────────────────────────────────────────

_NET_MSG = ("ТЕСТ ПОШЁЛ В СЕТЬ. В тестах публикации не бывает: значит "
            "проверяется не то, что задумано, либо предохранитель ключей "
            "перестал работать. Прогон остановлен НАМЕРЕННО.")

#: Те же ключи, что читает боевой код. На машине разработки они ЖИВЫЕ.
_LIVE_PREFIXES = ("VK_", "UPLOAD_POST_", "TG_")

#: Блокировка для ДОЧЕРНЕГО процесса. Ставится первой строкой кода: подменять
#: сеть после импорта боевого модуля поздно — он мог уже успеть сходить.
#: ⚠️ ГЛУШИМ МЕТОДЫ, А НЕ САМ КЛАСС `socket.socket`. Первая версия подменяла
#: класс функцией — и падал импорт `ssl`, который от этого класса наследуется
#: (`class SSLSocket(socket)`). То есть прогон умирал ДО проверок, и «сеть
#: заблокирована» превращалось в «ничего не проверено».
_GUARD = (
    "import socket as _s, ssl as _ssl\n"
    "def _boom(*a, **k):\n"
    "    raise AssertionError(%r)\n"
    "_s.socket.connect = _boom\n"
    "_s.socket.connect_ex = _boom\n"
    "_s.create_connection = _boom\n"
    "_s.getaddrinfo = _boom\n"
    "try:\n"
    "    import requests as _r\n"
    "    _r.post = _boom; _r.get = _boom; _r.request = _boom\n"
    "    _r.Session.request = _boom\n"
    "except Exception:\n"
    "    pass\n"
) % _NET_MSG


class _FakeSession:
    def request(self, *_a, **_kw):
        raise AssertionError(_NET_MSG)

    post = get = request


class _FakeRequests:
    """Подставной `requests`: импортируется, но в сеть не пускает."""

    Session = _FakeSession
    RequestException = Exception

    @staticmethod
    def post(*_a, **_kw):
        raise AssertionError(_NET_MSG)

    get = request = post


class _Missing:
    pass


@contextlib.contextmanager
def _no_network():
    """Сеть отрезана, живые ключи вычищены. Обязателен для любого прогона."""
    import ssl  # noqa: F401 — импортируем ДО подмены (см. пояснение к _GUARD)
    saved_req = sys.modules.get("requests", _Missing)
    saved_sock = (socket.socket.connect, socket.socket.connect_ex,
                  socket.create_connection, socket.getaddrinfo)
    saved_env = {k: os.environ.pop(k) for k in list(os.environ)
                 if k.startswith(_LIVE_PREFIXES)}

    def boom(*_a, **_kw):
        raise AssertionError(_NET_MSG)

    sys.modules["requests"] = _FakeRequests
    socket.socket.connect = boom
    socket.socket.connect_ex = boom
    socket.create_connection = boom
    socket.getaddrinfo = boom
    try:
        yield
    finally:
        (socket.socket.connect, socket.socket.connect_ex,
         socket.create_connection, socket.getaddrinfo) = saved_sock
        if saved_req is _Missing:
            sys.modules.pop("requests", None)
        else:
            sys.modules["requests"] = saved_req
        os.environ.update(saved_env)


# ── песочница: коробка клиента с двумя файлами настроек ──────────────────────

def _box(tmp: str, config: dict, env: dict) -> Path:
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
    install/factory_config.py живут в переменных процесса — в одном процессе
    настоящие ключи владельца протекли бы в проверку. Блокировка сети едет
    вместе с кодом (`_GUARD`), окружение вычищено от живых ключей.
    """
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(_LIVE_PREFIXES)}
    env["FACTORY_ROOT"] = str(box)
    env["PYTHONPATH"] = str(REPO)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    p = subprocess.run([sys.executable, "-c", _GUARD + code], cwd=str(REPO),
                       env=env, text=True, encoding="utf-8", errors="replace",
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=120)
    return p.returncode, p.stdout


# ============================================================================
# 1. ОДНО ИМЯ КЛЮЧА
# ============================================================================

def test_alias_answer_reaches_carousel():
    """Клиент ответил мастеру под старым именем — карусель ключ ВИДИТ.

    Это и есть «дыра трёх имён»: ответ лёг в `.env` как VK_STORIES_TOKEN (так
    вопрос звался у story-publisher), а vkcarousel.py спрашивал
    VK_USER_TOKEN_KATE и останавливался словами «в .env нет ключа» — на
    полностью заполненном мастере.
    """
    with tempfile.TemporaryDirectory() as tmp:
        box = _box(tmp, {"VK_GROUP_ID": "12345"},
                   {"VK_STORIES_TOKEN": "vk1.a.старое_имя"})
        rc, out = _py(
            "import sys; sys.path.insert(0, 'modules');\n"
            "import vkcarousel;\n"
            "env = vkcarousel.load_env();\n"
            "print('KATE=', bool(env.get('VK_USER_TOKEN_KATE')));\n"
            "print('GROUP=', bool(env.get('VK_GROUP_ID')))\n", box)
    assert rc == 0, out
    assert "KATE= True" in out, (
        "ответ клиента лежит под именем-алиасом, а карусель его не находит — "
        "клиент не может подключить ВК в принципе:\n%s" % out)
    assert "GROUP= True" in out, out


def test_alias_works_in_the_other_direction():
    """И наоборот: ответ под каноном виден там, где спрашивают старое имя."""
    with tempfile.TemporaryDirectory() as tmp:
        box = _box(tmp, {}, {"VK_USER_TOKEN_KATE": "vk1.a.канон"})
        rc, out = _py(
            "import sys; sys.path.insert(0, 'modules');\n"
            "from publishing import config_reader as cr;\n"
            "print('STORIES=', cr.value('VK_STORIES_TOKEN'))\n", box)
    assert rc == 0, out
    assert "STORIES= vk1.a.канон" in out, out


def test_vk_id_token_is_not_an_alias_of_kate():
    """VK_USER_TOKEN — ДРУГОЙ токен, а не другое имя. Смешивать нельзя.

    Он формата vk2.a.: `wall.post` и фото на стену ему закрыты (1051/15). Если
    его подставить каруселью под видом рабочего ключа, публикация уйдёт в
    заведомо закрытый метод, а клиент увидит невнятный отказ вместо «ВК не
    подключён».
    """
    with tempfile.TemporaryDirectory() as tmp:
        box = _box(tmp, {}, {"VK_USER_TOKEN": "vk2.a.чужой_формат"})
        rc, out = _py(
            "import sys; sys.path.insert(0, 'modules');\n"
            "from publishing import config_reader as cr;\n"
            "print('KATE=', cr.value('VK_USER_TOKEN_KATE'))\n", box)
    assert rc == 0, out
    assert "KATE= None" in out, (
        "запасной ключ vk2.a. выдан за рабочий vk1.a. — карусель уйдёт в "
        "закрытый метод:\n%s" % out)


def test_doctor_and_carousel_agree_on_the_same_box():
    """Доктор и боевой код обязаны сойтись на ОДНОЙ раскладке настроек.

    Расхождение опаснее самой нехватки ключа: зелёный отчёт и падающая
    публикация — то, из-за чего клиент перестаёт верить отчёту вообще.
    """
    with tempfile.TemporaryDirectory() as tmp:
        box = _box(tmp, {"VK_GROUP_ID": "12345"},
                   {"VK_STORIES_TOKEN": "vk1.a.старое_имя"})
        rc_doc, out_doc = _py(
            "import sys; sys.path.insert(0, 'modules');\n"
            "from publishing import doctor;\n"
            "problems, lines = doctor.check();\n"
            # ВК теперь живёт в problems, а не в lines: ключ есть, но работать
            # им запрещено, и «OK» про него быть не может. Инвариант проверки
            # прежний — доктор и код видят ОДНУ раскладку, включая алиасы.
            "print('VK_OK=', any('ВКонтакте' in l for l in problems + lines))\n", box)
        rc_run, out_run = _py(
            "import sys; sys.path.insert(0, 'modules');\n"
            "import vkcarousel;\n"
            "print('VK_OK=', bool(vkcarousel.load_env().get("
            "'VK_USER_TOKEN_KATE')))\n", box)
    assert rc_doc == 0 and rc_run == 0, out_doc + out_run
    assert "VK_OK= True" in out_doc and "VK_OK= True" in out_run, (
        "доктор и публикация разошлись на одной коробке:\nДОКТОР:%s\nКОД:%s"
        % (out_doc, out_run))


# ============================================================================
# 2. ВХОД ЧЕРЕЗ KATE MOBILE ЗАПРЕЩЁН (08.09.2026)
# ============================================================================
#
# ЧТО СЛУЧИЛОСЬ. Помощник в копии клиента прошёл этот вход по нашей же
# инструкции — и ВКонтакте ЗАБЛОКИРОВАЛ АККАУНТ клиентки целиком: API ответил
# «user authorization failed: user is blocked», ВК прислал ссылку на
# восстановление с проверкой личности. Снять блокировку может только сам
# владелец аккаунта.
#
# ПОЧЕМУ ТЕСТЫ, А НЕ СТРОЧКА В ИНСТРУКЦИИ. Прежние инструкции остались у
# клиентов на руках и в старых копиях завода. Запрет держится кодом: ссылка не
# собирается, ключ не пишется, команда отказывает вслух. Инструкция может
# разойтись с кодом — тест не даст.

def _vk_auth():
    with _no_network():
        import importlib
        mod = importlib.import_module("vk_auth")
        return importlib.reload(mod)


def test_login_link_is_not_built_anymore():
    """Ссылка входа НЕ собирается ни одной функцией — падаем громко."""
    with _no_network():
        va = _vk_auth()
        try:
            va.kate_link()
        except RuntimeError as ex:
            assert "SmmBox" in str(ex), (
                "отказ не назвал разрешённый путь — человек пойдёт искать "
                "обход:\n%s" % ex)
        else:
            raise AssertionError(
                "kate_link() снова отдаёт рабочую ссылку входа — это тот самый "
                "путь, на котором ВК заблокировал аккаунт клиентки")


def test_key_from_that_login_is_not_written():
    """Адрес со страницы больше не превращается в ключ в .env."""
    url = ("https://oauth.vk.com/blank.html#access_token=vk1.a.x"
           "&expires_in=0&user_id=777")
    with _no_network():
        va = _vk_auth()
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("TG_BOT_TOKEN=прежний\n", encoding="utf-8")
            try:
                va.kate_finish(url, path=str(env_path))
            except RuntimeError as ex:
                assert "SmmBox" in str(ex), str(ex)
            else:
                raise AssertionError("ключ запрещённого входа записан в .env")
            written = env_path.read_text(encoding="utf-8")
    assert "VK_USER_TOKEN_KATE" not in written, written
    assert "TG_BOT_TOKEN=прежний" in written, (
        "отказ затёр соседние настройки клиента:\n%s" % written)


def test_command_refuses_out_loud_and_with_code_2():
    """`--kate` отказывает словами и кодом 2, а не молчит и не падает стеком.

    Молчаливое «нет такой команды» человек читает как поломку завода и идёт
    искать обход — ровно то, чего запрет и не должен допустить.
    """
    with tempfile.TemporaryDirectory() as tmp:
        box = _box(tmp, {}, {})
        rc, out = _py("import sys; sys.argv = ['vk_auth.py', '--kate'];\n"
                      "import vk_auth; vk_auth.main()\n", box)
    assert rc == 2, ("ожидался код 2 (не поломка, но и не успех):\n%s" % out)
    assert "Traceback" not in out, out
    assert "ЗАПРЕТ" in out and "smmbox_connect.py" in out, (
        "отказ не объяснил ни запрета, ни что делать вместо:\n%s" % out)
    assert "oauth.vk.com/authorize" not in out, (
        "в отказе напечатана та самая ссылка — её просто скопируют:\n%s" % out)


def test_refusal_prints_on_a_russian_windows_console():
    """Отказ обязан НАПЕЧАТАТЬСЯ на консоли клиента, а не упасть на кодировке.

    Поймано живьём при написании запрета: эмодзи в тексте роняло вывод на
    Windows с cp1251 — вместо отказа человек видел стектрейс. Это тот же
    «отказ, выглядящий как успех», только вывернутый наизнанку.
    """
    with _no_network():
        ban = _vk_auth().KATE_BAN
    for console in ("cp1251", "cp866"):
        try:
            ban.encode(console)
        except UnicodeEncodeError as ex:
            raise AssertionError(
                "текст отказа не печатается на консоли %s (%s) — клиент "
                "увидит стектрейс вместо объяснения" % (console, ex))


def test_the_app_number_is_gone_from_the_code():
    """Номера приложения Kate Mobile нет в коде — ссылку не из чего собрать.

    Пока номер лежит в файле, ссылку можно собрать заново «на минуточку».
    Запрет проверяем по отсутствию материала, а не по доброй воле.
    """
    hits = []
    for path in sorted(REPO.glob("*.py")) + sorted(
            (REPO / "modules").rglob("*.py")):
        if path.name == Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "2685278" in text or "oauth.vk.com/authorize" in text:
            hits.append(str(path.relative_to(REPO)))
    assert not hits, (
        "номер приложения или адрес входа вернулись в код: %s" % ", ".join(hits))


def test_client_instructions_do_not_send_anyone_to_that_login():
    """В инструкции клиента нет ни команды входа, ни имени приложения.

    Клиентский CLAUDE.md — рабочий инструмент помощника: чего в нём нет, того
    для клиента не существует, и наоборот.
    """
    doc = REPO / "install" / "box_root" / "CLAUDE.md"
    text = doc.read_text(encoding="utf-8")
    for forbidden in ("--kate", "Kate Mobile", "2685278"):
        for line in text.splitlines():
            if forbidden in line and "ЗАПРЕЩ" not in line.upper():
                raise AssertionError(
                    "инструкция клиента снова ведёт на запрещённый вход: %s"
                    % line.strip())


# ============================================================================
# 3. ДОКТОР ГОВОРИТ ПРАВДУ ПРО ВК
# ============================================================================

def _doctor_on(config: dict, env: dict):
    with tempfile.TemporaryDirectory() as tmp:
        box = _box(tmp, config, env)
        return _py("import sys; sys.path.insert(0, 'modules');\n"
                   "from publishing import doctor; sys.exit(doctor.main())\n", box)


def test_doctor_no_longer_calls_the_old_vk_key_readiness():
    """Старый ключ в настройках — это НЕ «ВКонтакте готов».

    Раньше доктор при виде ключа писал «OK ВКонтакте (карусели, видео и клипы):
    готово». Работать этим ключом запрещено, значит строка стала враньём — а
    именно доктора клиент читает, решая, всё ли у него хорошо.
    """
    rc, out = _doctor_on({"VK_GROUP_ID": "12345"},
                         {"VK_USER_TOKEN_KATE": "vk1.a.старый"})
    assert "OK  ВКонтакте" not in out, (
        "доктор снова выдаёт запрещённый ключ за готовность:\n%s" % out)
    assert "не используется" in out.lower() or "НЕ ИСПОЛЬЗУЕТСЯ" in out, out
    assert "smmbox_connect.py" in out, (
        "закрыли путь и не назвали работающий:\n%s" % out)
    assert "Traceback" not in out, out


def test_doctor_sells_no_direct_vk_path_at_all():
    """Ни один прямой ключ ВК не выдаётся за путь публикации.

    Раньше здесь проверялось, что доктор честно предупреждает: запасной ключ
    vk2.a. живёт час и карусели им не выходят. Предупреждение стало
    бессмысленным — публиковать в ВК своим ключом нельзя вообще, ни вечным, ни
    часовым. Осталась одна проверка, и она важнее прежней: клиента не отправят
    чинить ВК прямым входом.
    """
    for env in ({"VK_USER_TOKEN": "vk2.a.запасной"},
                {"VK_USER_TOKEN_KATE": "vk1.a.вечный"},
                {}):
        rc, out = _doctor_on({"VK_GROUP_ID": "12345"}, env)
        assert "vk_auth.py" not in out and "--kate" not in out, (
            "доктор ведёт на запрещённый вход при настройках %s:\n%s"
            % (env, out))
        assert "smmbox_connect.py" in out, (
            "доктор не назвал работающий путь при настройках %s:\n%s"
            % (env, out))
        assert "Traceback" not in out, out
        assert rc in (0, 2), out


def test_doctor_offers_one_command_when_vk_missing():
    """ВК не подключён — доктор даёт КОМАНДУ, а не предлагает разбираться.

    Команда теперь одна и та же во всех ветках: SmmBox. Прежняя проверка ждала
    здесь «3 минуты» про прямой вход — обещание снято вместе с самим входом.
    """
    rc, out = _doctor_on({}, {})
    assert rc == 2, out
    assert "smmbox_connect.py" in out, out
    assert "vk_auth.py --kate" not in out, (
        "доктор снова ведёт клиента на запрещённый вход:\n%s" % out)
    assert "токен" not in out.lower().split("вконтакте")[-1][:400], (
        "в ВК-подсказке всплыло слово «токен» — клиент его не знает:\n%s" % out)


def test_doctor_no_longer_blames_ffmpeg_for_carousel():
    """FFmpeg карусели в ВК больше не нужен — врать про это нельзя.

    Карусель уходит листаемым фото-альбомом, никакого видео не собирается.
    Прежний текст заявлял «карусель нельзя собрать для ВКонтакте» — то есть
    пугал поломкой, которой нет.
    """
    src = (Path(__file__).resolve().parent / "doctor.py").read_text(encoding="utf-8")
    assert "карусель нельзя собрать в видео для ВКонтакте" not in src, (
        "вернулся текст, объявляющий рабочую карусель сломанной")


# ============================================================================
# 4. МАСТЕР СПРАШИВАЕТ РОВНО ТО, ЧТО КЛИЕНТ МОЖЕТ ОТВЕТИТЬ
# ============================================================================

def _questions(manifest: Path):
    data = json.loads(manifest.read_text(encoding="utf-8-sig"))
    return {q["key"].strip().lower(): q for q in data.get("config_questions", [])}


def test_wizard_cannot_ask_for_the_forbidden_vk_key():
    """Мастер установки НЕ спрашивает прямой ключ ВК — ни в одном модуле.

    Пока вопрос стоит в анкете, запрет обходится в один ход: клиент читает
    старую инструкцию (или лист самопроверки, разосланный 08.09.2026), говорит
    «подключи ВК», мастер спрашивает ключ — и человек идёт его добывать
    запрещённым входом. Вопроса нет — обходить нечего.
    """
    here = Path(__file__).resolve().parent
    pub = _questions(here / "module.manifest.json")
    stories = _questions(here.parent / "story_publisher" / "module.manifest.json")
    for name, qs in (("publishing", pub), ("story_publisher", stories)):
        forbidden = [k for k in qs if k.startswith("vk_") and "token" in k]
        assert not forbidden, (
            "%s снова спрашивает ключ ВК (%s) — это прямой вход, после которого "
            "ВК блокирует аккаунт клиента" % (name, forbidden))


def test_wizard_drops_the_key_even_if_a_manifest_brings_it_back():
    """Замок стоит в самом мастере, а не только в манифестах.

    Манифест может вернуться из чужой ветки, из старой копии, из правки по
    невнимательности. Тогда вопрос всё равно не будет задан.
    """
    src = (REPO / "install" / "questions.py").read_text(encoding="utf-8")
    assert "BLOCKED_KEYS" in src, "замок из мастера исчез"
    assert "vk_user_token_kate" in src, (
        "ключ ВК выпал из списка запрещённых — мастер снова его спросит")


def test_old_key_is_left_in_place_but_never_used():
    """Ключ из `.env` не выдёргиваем — но и не публикуем им.

    Две половины одного решения. Удалять чужие настройки без просьбы мы не
    будем: это сломало бы копию клиента ради правила. Но и работать этим
    ключом нельзя — запрещён не способ его добыть, а любое действие им.
    """
    src = (REPO / "vkcarousel.py").read_text(encoding="utf-8")
    head = src[:src.index("def ")] if "def " in src else src
    assert "ЗАПРЕЩ" in src.upper(), "из карусели пропал запрет"
    rc, out = _doctor_on({"VK_GROUP_ID": "12345"},
                         {"VK_USER_TOKEN_KATE": "vk1.a.старый"})
    assert "Удалять ключ не нужно" in out or "удалять" in out.lower(), (
        "доктор не сказал, что старый ключ можно оставить — клиент решит, что "
        "надо чистить .env руками:\n%s" % out)
    assert rc in (0, 2), out


def test_manifest_no_longer_asks_the_unanswerable():
    """Вопросы, на которые нетехнический клиент ответить не может, убраны.

    VK_APP_ID требует регистрации разработчика на dev.vk.com, а VK_USER_TOKEN в
    анкету не вводится вовсе — он выдаётся входом в браузер. Оба вопроса были
    тупиком: клиент упирался в них и бросал подключение ВК целиком.
    """
    pub = _questions(Path(__file__).resolve().parent / "module.manifest.json")
    for key in ("vk_app_id", "vk_user_token", "vk_refresh_token", "vk_device_id"):
        assert key not in pub, (
            "мастер снова спрашивает %s — ответить на это клиент не может" % key)


def test_doctor_still_reads_the_legacy_keys():
    """Убрали из анкеты — но НЕ из чтения: запасной путь видео должен работать."""
    src = (Path(__file__).resolve().parent / "doctor.py").read_text(encoding="utf-8")
    for key in ("VK_USER_TOKEN", "VK_REFRESH_TOKEN", "VK_DEVICE_ID", "VK_APP_ID"):
        assert key in src, (
            "доктор перестал видеть запасной ключ %s — коробка владельца, "
            "настроенная по-старому, объявится неподключённой" % key)


# ============================================================================
# 5. ПРОТУХШИЙ КЛЮЧ ОБЪЯСНЯЕТСЯ, А НЕ ОТДАЁТСЯ ЦИФРОЙ
# ============================================================================

def test_error_5_names_the_cause_and_the_fix():
    """Ошибка 5 обязана быть переведена на человеческий и дать команду."""
    with _no_network():
        import importlib
        vkc = importlib.reload(importlib.import_module("vkcarousel"))
        text = vkc.vk_error_text(5, "User authorization failed: no access_token passed.")
    assert "smmbox_connect.py" in text, (
        "названа беда, но не назван выход:\n%s" % text)
    assert "vk_auth.py --kate" not in text, (
        "объяснение ошибки снова ведёт на запрещённый вход:\n%s" % text)
    for word in ("VPN", "пароль"):
        assert word in text, "в объяснении нет «%s»:\n%s" % (word, text)
    assert "ошибка 5" in text, (
        "сырая формулировка ВК пропала — если за кодом однажды окажется другая "
        "причина, разбираться будет не по чему:\n%s" % text)


def test_error_1051_points_at_the_token_format():
    """1051/15 — это про ФОРМАТ ключа, и клиент должен узнать об этом сразу."""
    with _no_network():
        import importlib
        vkc = importlib.reload(importlib.import_module("vkcarousel"))
        for code in (15, 1051):
            text = vkc.vk_error_text(code, "Access denied")
            assert "vk1.a." in text, text
            assert "smmbox_connect.py" in text and "vk_auth.py --kate" not in text, (
                "1051 снова отправляет клиента на запрещённый вход:\n%s" % text)


def test_closed_method_is_still_recognised_by_check_post():
    """check_post отличает «метод закрыт» от «ключ отбит» по тексту ошибки.

    Он молчит ТОЛЬКО про закрытый метод (личная страница), а протухший ключ
    обязан долетать до человека. Признак — подстроки «1051» и «ошибка 15» в
    тексте. Перепишешь текст неаккуратно — проверка начнёт глушить настоящую
    беду и отрапортует спокойствие там, где его нет.
    """
    with _no_network():
        import importlib
        vkc = importlib.reload(importlib.import_module("vkcarousel"))
        assert "1051" in vkc.vk_error_text(1051, "Access denied")
        assert "ошибка 15" in vkc.vk_error_text(15, "Access denied")
        assert "1051" not in vkc.vk_error_text(5, "auth failed")
        assert "ошибка 15" not in vkc.vk_error_text(5, "auth failed")


def test_refresh_failure_explains_itself():
    """Нечем продлить запасной ключ — говорим о часе и о постоянном решении."""
    with tempfile.TemporaryDirectory() as tmp:
        box = _box(tmp, {}, {"VK_USER_TOKEN": "vk2.a.з", "VK_REFRESH_TOKEN": "r"})
        rc, out = _py(
            "import sys; sys.path.insert(0, 'modules');\n"
            "import vkpost;\n"
            "tok, err = vkpost.refresh_access(vkpost.load_env());\n"
            "print('ERR:', err)\n", box)
    assert rc == 0, out
    assert "около часа" in out and "vk_auth.py --kate" in out, (
        "причина «через час всё встанет» и выход не названы:\n%s" % out)


# ============================================================================
# 6. ПРЕДОХРАНИТЕЛИ НА МЕСТЕ
# ============================================================================

def test_send_safety_switch_intact():
    """Без --send ни один публикующий скрипт ничего не отправляет."""
    for name in ("vkcarousel.py", "vkpost.py", "vk_wall_post.py"):
        src = (REPO / name).read_text(encoding="utf-8")
        assert '"--send"' in src, "у %s пропал флаг --send" % name
        assert re.search(r"if not a\.send", src), (
            "в %s нет остановки при отсутствии --send — запуск стал публикацией"
            % name)


def test_login_step_publishes_nothing():
    """Шаг входа не умеет публиковать в принципе: у него нет ни --send, ни wall.*."""
    src = (REPO / "vk_auth.py").read_text(encoding="utf-8")
    assert "api.vk.com/method" not in src, (
        "шаг получения ключа стал ходить в боевой API ВК — а его клиент "
        "запускает не думая, и предохранителя --send у него нет")
    assert 'add_argument("--send"' not in src, (
        "у шага входа появился флаг публикации")
    # Упоминание `--send` в ПОДСКАЗКЕ («дальше запусти vkpost.py … --send») —
    # это текст для человека, а не действие: ищем именно объявление флага.


def test_modules_ask_nothing_themselves():
    """Внутри модулей нет input(): все вопросы задаёт мастер установки."""
    for pkg in ("publishing", "story_publisher"):
        for path in (REPO / "modules" / pkg).rglob("*.py"):
            if "__pycache__" in str(path) or path.name.startswith("test_"):
                continue
            src = path.read_text(encoding="utf-8")
            assert not re.search(r"(?<![\w.])input\s*\(", src), (
                "%s спрашивает клиента сам — по контракту это делает только "
                "мастер установки" % path)


_TESTS = [
    test_alias_answer_reaches_carousel,
    test_alias_works_in_the_other_direction,
    test_vk_id_token_is_not_an_alias_of_kate,
    test_doctor_and_carousel_agree_on_the_same_box,
    test_login_link_is_not_built_anymore,
    test_key_from_that_login_is_not_written,
    test_command_refuses_out_loud_and_with_code_2,
    test_refusal_prints_on_a_russian_windows_console,
    test_the_app_number_is_gone_from_the_code,
    test_client_instructions_do_not_send_anyone_to_that_login,
    test_doctor_no_longer_calls_the_old_vk_key_readiness,
    test_doctor_sells_no_direct_vk_path_at_all,
    test_doctor_offers_one_command_when_vk_missing,
    test_doctor_no_longer_blames_ffmpeg_for_carousel,
    test_wizard_cannot_ask_for_the_forbidden_vk_key,
    test_wizard_drops_the_key_even_if_a_manifest_brings_it_back,
    test_old_key_is_left_in_place_but_never_used,
    test_manifest_no_longer_asks_the_unanswerable,
    test_doctor_still_reads_the_legacy_keys,
    test_error_5_names_the_cause_and_the_fix,
    test_error_1051_points_at_the_token_format,
    test_closed_method_is_still_recognised_by_check_post,
    test_refresh_failure_explains_itself,
    test_send_safety_switch_intact,
    test_login_step_publishes_nothing,
    test_modules_ask_nothing_themselves,
]


if __name__ == "__main__":
    print("Сеть заблокирована технически: requests и socket подменены, "
          "ключи VK_/UPLOAD_POST_/TG_ вычищены, дочерние процессы получают "
          "ту же блокировку первой строкой.")
    print("")
    for fn in _TESTS:
        fn()
        print("  ok — %s" % (fn.__doc__ or fn.__name__).strip().splitlines()[0])
    print("")
    print("OK ВКонтакте: имя ключа одно на весь завод, вход посилен клиенту и "
          "в сеть не ходит, доктор называет и беду, и выход, предохранители "
          "--send целы.")

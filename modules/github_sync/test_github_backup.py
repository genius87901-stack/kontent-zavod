# -*- coding: utf-8 -*-
"""
Тесты github_sync — резервная копия завода в личном репозитории клиента.

Сеть здесь не нужна ни одной проверке: всё, что решает СУДЬБУ файла (уедет он
или нет, ключ в нём или не ключ, не ведёт ли путь из репозитория за пределы
завода), считается локально. Именно это и проверяем.

Главная проверка — `test_blob_sha_совпадает_с_настоящим_git`: весь смысл
локального счёта отпечатков в том, что номер обязан совпасть с тем, который
покажет GitHub. Сверяемся не с собственным представлением, а с настоящим
`git hash-object`; нет git в системе — проверка честно говорит, что пропущена,
а не притворяется зелёной.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# github_backup.py лежит в КОРНЕ установки, а этот тест — внутри модуля.
# run_tests.py зовёт файлы как скрипты, поэтому sys.path[0] — папка модуля, и
# без этой строки импорт падает у клиента (у нас — нет, у нас корень в cwd).
if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import github_backup as gs


def _завод() -> Path:
    """Временный корень завода со скелетом клиентской установки."""
    root = Path(tempfile.mkdtemp(prefix="gs_"))
    (root / "config").mkdir()
    (root / "modules" / "intake").mkdir(parents=True)
    (root / "out_c1").mkdir()
    (root / ".venv" / "Lib").mkdir(parents=True)
    (root / "installer" / "vendor").mkdir(parents=True)
    (root / "engine4.py").write_text("# движок\n", encoding="utf-8")
    (root / "CLAUDE.md").write_text("# инструкция\n", encoding="utf-8")
    (root / ".env").write_text("VK_TOKEN=vk1.a." + "z" * 60 + "\n", encoding="utf-8")
    (root / "license.key").write_text("подпись\n", encoding="utf-8")
    (root / "modules" / "intake" / "doctor.py").write_text("ok\n", encoding="utf-8")
    (root / "out_c1" / "slide.jpg").write_bytes(b"\xff\xd8\xff")
    (root / ".venv" / "Lib" / "site.py").write_text("x\n", encoding="utf-8")
    (root / "installer" / "vendor" / "wheel.py").write_text("x\n", encoding="utf-8")
    (root / "config" / "factory.config.json").write_text("{}\n", encoding="utf-8")
    return root


# ── отпечатки ────────────────────────────────────────────────────────────────


def test_blob_sha_совпадает_с_настоящим_git():
    """Номер блоба обязан совпадать с git — иначе сравнение с сервером ложь."""
    git = shutil.which("git")
    if not git:
        print("   ⚠️ git в системе нет — проверка ПРОПУЩЕНА, а не пройдена")
        return
    for проба in (b"", b"hello\n", "смыслы\n".encode("utf-8"), os.urandom(1000)):
        d = tempfile.mkdtemp()
        p = Path(d) / "f.bin"
        p.write_bytes(проба)
        настоящий = subprocess.run([git, "hash-object", str(p)],
                                   capture_output=True, text=True).stdout.strip()
        assert gs.blob_sha(проба) == настоящий, (gs.blob_sha(проба), настоящий)
    print("   ok  отпечаток блоба сходится с настоящим git на четырёх пробах")


# ── белый и чёрный списки ────────────────────────────────────────────────────


def test_секреты_и_ключи_не_уезжают_никогда():
    for путь in (".env", ".env.local", "license.key", "config/my.pem",
                 "modules/x/private.key"):
        assert gs.is_denied(путь), путь
    print("   ok  .env, license.key и любые *.key/*.pem отсечены чёрным списком")


def test_служебные_каталоги_и_результаты_не_уезжают():
    for путь in (".venv/Lib/site.py", "python/python.exe", "out_c1/slide.jpg",
                 "installer/vendor/wheel.py", "photos/a.jpg", "inbox/video/a.mp4",
                 "modules/__pycache__/x.pyc", ".update_backup/before/x.py",
                 "config/github.json", "install/.answers.json"):
        assert gs.is_denied(путь), путь
    print("   ok  .venv, python, out_*, vendor, медиа и рантайм-файлы отсечены")


def test_чёрный_список_сильнее_белого():
    """Разрешённое расширение в разрешённом каталоге не спасает от чёрного списка."""
    assert gs.is_included("installer/vendor/wheel.py")     # белым списком проходит
    assert gs.is_denied("installer/vendor/wheel.py")       # но чёрным — нет
    print("   ok  .py внутри vendor проходит белый список и всё равно не едет")


def test_медиа_и_бинарники_не_едут_даже_из_разрешённых_каталогов():
    for путь in ("modules/intake/demo.mp4", "styles/шрифт.ttf",
                 "ученикам/BANK_SHABLONOV.jpg", "docs/архив.zip"):
        assert not gs.is_included(путь), путь
    print("   ok  mp4, ttf, jpg и zip не проходят белый список расширений")


def test_код_и_смыслы_уезжают():
    for путь in ("engine4.py", "CLAUDE.md", "modules/intake/doctor.py",
                 "styles/lyubov.json", "ученикам/02_РАСПАКОВКА.md",
                 "config/factory.config.json"):
        assert not gs.is_denied(путь), путь
        assert gs.is_included(путь), путь
    print("   ok  код, документы, пресеты и смыслы проходят оба списка")


def test_чужой_каталог_клиента_не_уезжает():
    """Белый список каталогов: папка, о которой мы не договаривались, не едет."""
    assert not gs.is_included("Мои документы/личное.txt")
    assert not gs.is_included("Downloads/x.py")
    print("   ok  посторонние каталоги клиента в репозиторий не попадают")


def test_обход_дерева_берёт_нужное_и_отсеивает_лишнее():
    root = _завод()
    files, skipped = gs.collect(root)
    assert "engine4.py" in files, sorted(files)
    assert "modules/intake/doctor.py" in files, sorted(files)
    assert "config/factory.config.json" in files, sorted(files)
    for нельзя in (".env", "license.key", "out_c1/slide.jpg",
                   ".venv/Lib/site.py", "installer/vendor/wheel.py"):
        assert нельзя not in files, нельзя
    отсеяно = {путь for путь, _ in skipped}
    assert ".env" in отсеяно and "license.key" in отсеяно, отсеяно
    print(f"   ok  обход взял {len(files)} файлов и назвал причину отказа для "
          f"{len(skipped)}")


def test_крупный_файл_не_уезжает_и_назван():
    root = _завод()
    (root / "docs").mkdir()
    (root / "docs" / "толстый.json").write_bytes(b"x" * (gs.MAX_FILE_BYTES + 10))
    files, skipped = gs.collect(root)
    assert "docs/толстый.json" not in files
    assert any(п == "docs/толстый.json" for п, _ in skipped), skipped
    print("   ok  файл крупнее потолка отсечён и назван поимённо, а не молча")


# ── гейт секретов ────────────────────────────────────────────────────────────


def test_значение_из_env_в_коде_останавливает_отправку():
    """Ключ БЕЗ узнаваемой формы: найтись он обязан именно сверкой с .env.

    ⚠️ Первая редакция брала образцом `vk1.a.…` и была бесполезна: такой ключ
    ловится готовым шаблоном, поэтому проверка оставалась зелёной даже с
    ВЫКЛЮЧЕННОЙ сверкой значений. Поймано контрольным внесением дефекта, а не
    чтением. Отсюда правило: образец здесь не должен подходить ни под один
    шаблон из `_HARD_SECRET_PATTERNS`.
    """
    ключ = "Kq7wRt2mZx9Lb4Nv8Hs3Pd6Ty1Uc5Ae"      # ни под один шаблон не подходит
    assert not any(п.search(ключ) for п, _ in gs._HARD_SECRET_PATTERNS), \
        "образец стал узнаваемым — проверка снова перестала проверять сверку"
    hard, _soft, _s = gs.scan_secrets({"build.py": f'TOKEN = "{ключ}"\n'.encode()},
                                  {"VK_TOKEN": ключ})
    assert hard, "живой ключ из .env обязан быть найден"
    assert hard[0][0] == "build.py" and hard[0][1] == 1, hard
    assert hard[0][2] == "значение ключа из .env", hard
    print("   ok  значение живого ключа из .env найдено сверкой, а не шаблоном")


def test_известные_формы_ключей_находятся():
    пробы = {
        "a.py": b"k = 'sk-ant-" + b"a" * 40 + b"'\n",
        "b.py": b"t = 'ghp_" + b"b" * 36 + b"'\n",
        "c.py": b"g = 'AIza" + b"c" * 35 + b"'\n",
        "d.py": "бот = '123456789:AA{}'\n".format("d" * 33).encode("utf-8"),
    }
    hard, _soft, _s = gs.scan_secrets(пробы, {})
    найдено = {путь for путь, _, _ in hard}
    assert найдено == set(пробы), найдено
    print("   ok  ключи Anthropic, GitHub, Google и бота Telegram найдены все четыре")


def test_короткие_значения_из_env_не_дают_ложных_находок():
    """«yes», «1», «true» встречаются в любом коде — по ним отказывать нельзя."""
    hard, _soft, _s = gs.scan_secrets({"a.py": b"flag = 'yes'\nn = 1\n"},
                              {"DEBUG": "yes", "N": "1", "MODE": "true"})
    assert not hard, hard
    print("   ok  короткие значения из .env ложных отказов не дают")


def test_подозрение_исключает_файл_а_не_всю_отправку():
    hard, soft, _s = gs.scan_secrets(
        {"ок.py": b"x = 1\n", "странный.py": b"api_key = '" + b"q" * 40 + b"'\n"}, {})
    assert not hard, hard
    assert [п for п, _, _ in soft] == ["странный.py"], soft
    print("   ok  общий шаблон исключает один файл, а не отменяет копию целиком")


def test_отметка_образца_снимает_находку_и_остаётся_видимой():
    """Живой прогон 02.09.2026 нашёл в дереве ПРИМЕР токена бота из документации
    Telegram (фикстура теста). Без исключения одна такая строка останавливала бы
    резервную копию навсегда — и человек выключил бы весь механизм."""
    # Отметка ниже стоит для гейта, который читает ЭТОТ файл: тест едет клиенту
    # в коробке, и без неё строка-образец блокировала бы резервную копию на
    # каждой клиентской машине — тем самым дефектом, который тест и проверяет.
    # На саму проверку она не влияет: в `строку` попадают только байты слева от
    # неё, комментарий в литерал не входит.
    строка = b"BOEVOY_TOKEN = '8123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw'"  # github_sync: образец
    hard, _soft, samples = gs.scan_secrets({"t.py": строка}, {})
    assert hard, "без отметки образец обязан ловиться как находка"
    with_mark = строка + ("  # " + gs.SAMPLE_MARK).encode("utf-8")
    hard2, _soft2, samples2 = gs.scan_secrets({"t.py": with_mark}, {})
    assert not hard2, hard2
    assert samples2 and samples2[0][0] == "t.py", samples2
    assert not samples, "без отметки исключений быть не должно"
    print("   ok  отметка снимает находку и сама попадает в список исключений")


def test_отметка_образца_НЕ_прощает_живой_ключ_из_env():
    """Иначе комментарием можно было бы выпустить наружу рабочий ключ."""
    ключ = "Kq7wRt2mZx9Lb4Nv8Hs3Pd6Ty1Uc5Ae"
    строка = f"TOKEN = '{ключ}'  # {gs.SAMPLE_MARK}\n".encode("utf-8")
    hard, _soft, _s = gs.scan_secrets({"build.py": строка}, {"VK_TOKEN": ключ})
    assert hard, "живой ключ из .env отметкой не прощается"
    print("   ok  отметка не прощает значение, которым завод работает прямо сейчас")


def test_свой_токен_github_не_ищется_в_коде():
    """GITHUB_TOKEN лежит только в .env, который и так не едет: искать его в
    собственном коде значит отказывать себе же на ровном месте."""
    hard, _soft, _s = gs.scan_secrets({"github_backup.py": b"GITHUB_TOKEN = 'x'\n"},
                              {"GITHUB_TOKEN": "x" * 40})
    assert not hard, hard
    print("   ok  собственный доступ к GitHub в набор поиска не берётся")


# ── приём правок ─────────────────────────────────────────────────────────────


def test_приём_отвергает_пути_за_пределы_завода():
    """Репозиторий может подсунуть что угодно: он у клиента, и правит его клиент."""
    for путь in ("../../etc/passwd", "/etc/passwd", "C:/Windows/x.py",
                 "modules/../../x.py", ".env", ".ssh/id_rsa"):
        assert gs.is_denied(путь) or not gs.is_included(путь), путь
    print("   ok  «..», абсолютные пути, буква диска и .env при приёме отвергнуты")


def test_слепок_коробки_читается_в_обоих_видах():
    root = _завод()
    (root / "config" / "box_files.json").write_text(
        json.dumps({"files": {"engine4.py": {"sha256": "aa"}}}), encoding="utf-8")
    assert gs.load_baseline(root) == {"engine4.py": "aa"}
    (root / "config" / "box_files.json").write_text(
        json.dumps({"engine4.py": "bb"}), encoding="utf-8")
    assert gs.load_baseline(root) == {"engine4.py": "bb"}
    print("   ok  слепок коробки читается и вложенным, и плоским")


def test_нет_слепка_не_отказ():
    assert gs.load_baseline(_завод()) == {}
    print("   ok  отсутствие слепка коробки не роняет приём")


# ── настройки и токен ────────────────────────────────────────────────────────


def test_токен_ложится_в_env_и_не_трогает_чужие_ключи():
    root = _завод()
    было = (root / ".env").read_text(encoding="utf-8")
    gs.save_token("ghp_" + "t" * 36, root)
    стало = (root / ".env").read_text(encoding="utf-8")
    assert было.strip().splitlines()[0] in стало, "чужой ключ пропал из .env"
    assert gs.get_token(root) == "ghp_" + "t" * 36
    gs.save_token("ghp_" + "u" * 36, root)      # повтор заменяет, а не плодит
    assert стало.count("GITHUB_TOKEN=") == 1
    assert (root / ".env").read_text(encoding="utf-8").count("GITHUB_TOKEN=") == 1
    print("   ok  доступ лёг в .env, чужие ключи целы, повтор строку не плодит")


def test_ненастроенность_видна():
    root = _завод()
    assert not gs.configured(root)
    gs.save_config({"owner": "кто-то", "repo": "kontent-zavod"}, root)
    gs.save_token("ghp_" + "v" * 36, root)
    assert gs.configured(root)
    print("   ok  «не настроено» отличается от «настроено» без обращения к сети")


def test_напоминание_раз_в_сутки_а_не_при_каждом_запуске():
    """Молчать нельзя (BOX-73), но нытьё при каждом старте перестают читать."""
    root = _завод()
    сейчас = 1_700_000_000.0
    assert gs.should_nag(root, сейчас), "первое напоминание обязано прозвучать"
    assert not gs.should_nag(root, сейчас + 60), "через минуту — молчим"
    assert not gs.should_nag(root, сейчас + gs.NAG_INTERVAL_SEC - 1)
    assert gs.should_nag(root, сейчас + gs.NAG_INTERVAL_SEC + 1), "через сутки — снова"
    print("   ok  напоминание звучит раз в сутки, а не при каждом запуске")


def test_напоминание_переживает_отсутствие_файла_настроек():
    root = _завод()
    assert not (root / gs.CONFIG_FILE).exists()
    assert gs.should_nag(root, 1_700_000_000.0)
    assert (root / gs.CONFIG_FILE).exists(), "отметка обязана сохраниться"
    print("   ok  отметка о напоминании заводится, даже когда настроек ещё нет")


def test_журнал_отправок_сам_не_уезжает():
    """Иначе каждая отправка меняла бы файл и порождала следующую — вечно."""
    assert gs.is_denied(gs.CONFIG_FILE), gs.CONFIG_FILE
    print("   ok  config/github.json в репозиторий не попадает")


def test_правки_с_телефона_не_перетираются_отправкой():
    """🔴 Найдено замером 03.09.2026, ДО того как дошло до клиента.

    Отправка считает «изменившимся» всё, что на диске отличается от репозитория,
    и ей всё равно, КТО отличается. Человек правит контент-план с телефона,
    садится за компьютер, хук отправляет копию — и старая местная версия молча
    ложится поверх телефонной. Правка исчезает, никто не замечает.

    Отличить можно точно: мы помним номер СВОЕГО последнего коммита. Голова
    репозитория ушла от него — писал кто-то ещё, и отправлять поверх нельзя.
    Проверяем по исходнику: сравнение с `last_push.sha` обязано стоять ДО того,
    как считается список изменившихся файлов.
    """
    корень = Path(__file__).resolve().parents[2]
    текст = io.open(корень / "github_backup.py", encoding="utf-8").read()
    тело = текст[текст.index("def push("):текст.index("# ── Приём правок")]
    сторож = тело.index('было = (cfg.get("last_push") or {}).get("sha")')
    счёт = тело.index("changed = sorted(")
    assert сторож < счёт, "сторож обязан стоять ДО подсчёта изменившихся файлов"
    assert 'parent_sha and было and parent_sha != было' in тело
    assert '"чужие_правки": True' in тело
    # И об этом обязаны сказать вслух — в том числе в тихом режиме хука.
    хвост = текст[текст.index("if result.get(\"чужие_правки\"):"):]
    assert "забери правки с телефона" in хвост[:600],         "человеку не сказано, что делать"
    print("   ok  отправка не перетирает правки с телефона и говорит об этом вслух")


def _прогнать_всё():
    тесты = [(и, ф) for и, ф in sorted(globals().items())
             if и.startswith("test_") and callable(ф)]
    for и, ф in тесты:
        print(и)
        ф()
    return len(тесты)


if __name__ == "__main__":
    n = _прогнать_всё()
    print("\nOK (%d проверок) резервная копия завода: отпечаток блоба сходится с "
          "настоящим git, секреты и медиа не уезжают ни при каком раскладе, "
          "путь из репозитория не выводит за пределы завода." % n)

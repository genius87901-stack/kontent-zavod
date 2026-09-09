# -*- coding: utf-8 -*-
"""
updater — тихое обновление коробки клиента подписанными патчами (BOX-30).

ЗАЧЕМ. Завод улучшается после продажи: качество монтажа Reels, новые форматы,
правки движка. Пересылать 690 МБ установщика на каждую правку и просить клиента
переустанавливать — неприемлемо. Этот модуль приносит ТОЛЬКО изменившиеся файлы.

МОДЕЛЬ ДОВЕРИЯ. Патч подписан тем же ключом Ed25519, что и лицензии; ПУБЛИЧНЫЙ
ключ уже лежит в каждой коробке (`installer/license_pubkey.py`). Подпись
проверяется ДО распаковки: подменённый на канале архив не применится, даже если
злоумышленник владеет сервером раздачи. Приватное семя — только у владельца.

ЧТО НИКОГДА НЕ ТРОГАЕМ (жёсткий чёрный список, см. _FORBIDDEN):
    .venv/ · config/ · .env · license.key · медиа-папки · out_*/ · .state/
Обновляются только КОД, пресеты и наши картинки-подсказки (витрина шаблонов) —
то, что мы же и написали. Личные данные,
ключи и результаты работы клиента патч затронуть не может ПО УСТРОЙСТВУ, а не
по договорённости.

БЕЗОПАСНОСТЬ ПРИМЕНЕНИЯ. Патч применяется атомарно: сначала все файлы
проверяются и распаковываются во временный каталог, затем делается резервная
копия заменяемых файлов, и только потом идёт замена. Любая ошибка на любом шаге
→ полный откат к состоянию до обновления. Нет сети / нет обновлений / битая
подпись → тихо выходим с кодом 0: обновление НИКОГДА не ломает рабочий день.

ЧУЖИЕ ПРАВКИ В НАШИХ ФАЙЛАХ (найдено 15.08.2026 живым клиентом). Патч возит
КОД, и клиент имеет право этот код под себя доработать — расписание, объёмы,
свои правила. Раньше обновление молча клало новую версию поверх: правка
исчезала, человек узнавал об этом случайно и только если помнил, что правил.
Резервная копия была, но о ней никто не знал в нужный момент. Теперь коробка
помнит, ЧТО ИМЕННО она доставила (`config/box_files.json`, слепок sha256), и
перед заменой сравнивает файл на диске с этим слепком. Отличается — значит его
меняли на стороне клиента: копия клиентской версии откладывается в
`.update_backup/local-edits/<версия>/`, рядом кладётся `.diff` (чем клиентская
версия отличается от НОВОЙ официальной — то есть что именно надо перенести), и
о каждом таком файле говорится ВСЛУХ, в том числе в `--auto`. Обновление при
этом не отменяется: исправления доехать обязаны, а решение «переносить правку
или нет» принимает человек, зная факт, а не гадая. Проверить заранее, до всякого
обновления, можно `--local-changes` (сеть не нужна).

ПОСЛЕУСТАНОВОЧНЫЕ ШАГИ (BOX-73, 01.09.2026). Иногда патча мало: 1.3.44 привёз
новый контракт папок Google Диска, но сами папки создаёт генератор, и без одного
ручного прогона приём материалов умирал. Сказать об этом было нечем — механизм
знал только «файлы заменены», и отчитался успехом у всех троих клиентов. Теперь
манифест может НАЗВАТЬ шаг по имени из белого списка `POST_STEPS` (ни команды,
ни аргументов он не несёт — см. подробный разбор у самого списка), коробка
выполняет его сама, а если шаг не удался — говорит вслух, пишет в журнал,
оставляет отметку `config/post_steps_pending.json` и отдаёт код 1. Патч при этом
НЕ откатывается: файлы уже доехали. Поля нет в манифесте — всё как раньше.

ЗАПУСК:
    python updater.py --check          # есть ли новая версия (сеть, ничего не меняет)
    python updater.py --apply          # скачать и применить
    python updater.py --auto           # тихо: для агента/планировщика (молчит, если нечего)
    python updater.py --status         # текущая версия и канал, без сети
    python updater.py --local-changes  # какие наши файлы правили на этой копии
    python updater.py --rollback       # вернуть предыдущую версию из резервной копии

Коды возврата: 0 — успех ИЛИ обновлять нечего ИЛИ сети нет (не повод падать);
1 — обновление найдено, но применить не удалось (откат сделан); 2 — плохой ввод.
Только stdlib. Без input(). Секретов не печатает.
"""
from __future__ import annotations

import argparse
import base64
import difflib
import hashlib
import json
import os
import shutil
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent

#: где записана установленная версия коробки (создаётся первым же обновлением)
VERSION_FILE = "config/box_version.json"
#: настройка канала обновлений: {"url": "...", "enabled": true}
CHANNEL_FILE = "config/update_channel.json"
#: куда кладём резервную копию заменяемых файлов (для --rollback)
BACKUP_DIR = ".update_backup"
#: журнал обновлений — что и когда приезжало
LOG_FILE = "config/update_log.jsonl"
#: слепок ДОСТАВЛЕННОГО: {путь: sha256} на момент последнего применения патча.
#: Единственный способ отличить «файл просто старый» от «файл правил клиент»:
#: без него обновление видит лишь «на диске не то, что в патче» — а это верно и
#: для нетронутого файла прошлой версии.
BASELINE_FILE = "config/box_files.json"
#: подкаталог в BACKUP_DIR, куда откладываются КЛИЕНТСКИЕ версии файлов, поверх
#: которых легло обновление. В отличие от before-<версия>/ его никто не чистит
#: и не перезаписывает: правки человека дороже места на диске.
LOCAL_EDITS_DIR = "local-edits"

#: ПРИВАТНЫЙ репозиторий-канал. Публичный отдавал бы исходники завода кому угодно:
#: патч — это обычные .py, а подпись защищает от подмены, но не от чтения.
#: Приватный репозиторий читается только по токену, который кладёт сборщик.
DEFAULT_REPO = "aevkone/kz-updates"
#: Запасной путь: прямой https-адрес без токена (если канал переедет на свой хостинг).
DEFAULT_MANIFEST_URL = ""

#: ЧТО ПАТЧ НЕ ИМЕЕТ ПРАВА ТРОГАТЬ. Проверяется на КАЖДОМ пути внутри архива:
#: сюда не попадут ни личные ключи клиента, ни его медиа, ни его результаты.
#: ⚠️ Найдено 11.08.2026 при разборе живого клиентского инцидента: правило
#: «.state/» — это каталог-зона (проверяется как префикс/сегмент пути), а
#: реальный рантайм-файл прогресса установки лежит РЯДОМ с обычным кодом —
#: `install/.state.json` (без вложенного каталога, см. install/stages.py) —
#: и «.state/» его НЕ ловит: "install/.state.json" не начинается на ".state/"
#: и не содержит "/.state/" подстрокой. Тот же провал у `install/.answers.json`
#: (install/questions.py) — там же могут лежать ответы клиента. Пока оба файла
#: не попадали в патч только потому, что они в .gitignore и никогда не
#: коммитились — но `_path_allowed` объявляет себя ЕДИНСТВЕННОЙ защитой
#: клиента, и полагаться на gitignore как на вторую линию, которой формально
#: нет, нельзя. Перечислены явно, точным путём (не префиксом), чтобы не
#: зацепить остальной install/*.py.
_FORBIDDEN = (
    ".venv/", "config/", ".env", "license.key", ".state/", ".git/",
    "photos/", "video/", "talking/", "music/", "stock/", "primer/",
    "music_cache/", "music_preview/", "out_", ".cache_models/",
    "installer/vendor/",          # wheelhouse — гигабайты, патчем не возим
    "install/.state.json", "install/.answers.json",
    BACKUP_DIR + "/",
)
#: разрешённые расширения — патч возит КОД, пресеты и КАРТИНКИ-ПОДСКАЗКИ, но не
#: произвольные бинарники и не архивы.
#: Картинки (.jpg/.jpeg/.png) добавлены 09.08.2026: без них патчем нельзя
#: доставить `ученикам/BANK_SHABLONOV.jpg` — витрину шаблонов каруселей, которую
#: клиентский CLAUDE.md велит агенту открыть ПЕРЕД вопросом об оформлении
#: (install/box_root/CLAUDE.md:66). Пара «картинка + описание» бессмысленна
#: половинками: текстом стили неразличимы.
#: ⚠️ Порядок выпуска обязателен: сначала патч с ЭТИМ файлом (updater.py) и БЕЗ
#: картинок, и только следующим — сами картинки. Коробка проверяет архив СВОИМ
#: updater.py, а `inspect_patch` роняет патч ЦЕЛИКОМ из-за одного недопустимого
#: пути. Картинка в патче, который применяет старая коробка, = отказ от всего
#: патча, а не потеря одного файла.
# 03.09.2026, патч 1.3.50: добавлены .cmd и .sh. Резервная копия завода вся
# ходит через пусковые файлы `zavod.cmd` (Windows) и `zavod.sh` (мак) — их зовёт
# и помощник по инструкции, и человек двойным щелчком по `РЕЗЕРВНАЯ_КОПИЯ.cmd`.
# Без этих типов патч довозил инструкцию к файлам, которых у клиента не
# появится. `.bat` в списке был изначально, то есть пусковой файл Windows и
# раньше считался безопасным: `.cmd` — он же под другим расширением.
# ⚠️ Список читает updater, УЖЕ СТОЯЩИЙ у клиента, поэтому сами `.cmd`/`.sh`
# уехали СЛЕДУЮЩИМ патчем (1.3.51), а не тем же самым.
_ALLOWED_SUFFIX = (".py", ".json", ".md", ".txt", ".ttf", ".ps1", ".bat", ".toml",
                   ".cmd", ".sh",
                   ".jpg", ".jpeg", ".png")

_LAST_FETCH_ERROR = None   # чем кончилась последняя попытка скачивания (см. cert_failure)
_TIMEOUT = 30          # сек на сетевой запрос: клиент не должен ждать долго
_MAX_PATCH_MB = 100    # разумный потолок патча; больше — это уже новая коробка


# ── версия и канал ───────────────────────────────────────────────────────────

def find_root(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    for cand in (HERE, *HERE.parents):
        if (cand / "engine4.py").exists() or (cand / "modules").exists():
            return cand
    return HERE


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return default


def current_version(root: Path) -> str:
    """Установленная версия. Нет файла — считаем 1.0.0 (первая коробка)."""
    return str(_read_json(root / VERSION_FILE, {}).get("version") or "1.0.0")


# ── слепок доставленного и клиентские правки ─────────────────────────────────
#
# ЗАЧЕМ ЭТО ЕСТЬ. 15.08.2026 клиентка сама заметила, что обновление 1.3.8→1.3.14
# затёрло её настройку расписания в modules/content_plan/{config,planner}.py.
# Она восстановила правки вручную, потому что заранее сделала свои копии, — но
# узнала о потере случайно, а до этого точно так же чуть не пропал
# carousel_bridge.py. Механизм был устроен так, что молчал: файлы заменялись
# целиком, а сообщить, что в заменённом были ЧУЖИЕ изменения, коробка не могла —
# ей не с чем было сравнивать. Слепок ниже и есть это «с чем сравнивать».

def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    """sha256 файла. Нечитаемый файл → пустая строка (не повод падать)."""
    try:
        return _sha256_bytes(path.read_bytes())
    except OSError:
        return ""


def _text_sha(data: bytes):
    """sha256 текста БЕЗ учёта перевода строк. None — это не текст (шрифт, jpg).

    Обязательная поправка, иначе защита кричала бы волками. Патч распаковывает
    файлы как есть (LF), а любой редактор на Windows — и python's write_text, и
    Блокнот — сохраняет CRLF. Без нормализации файл, к которому человек не
    прикасался по существу, отличается от доставленного КАЖДОЙ строкой: и в
    списке правок, и в .diff. Предупреждение, которое срабатывает не по делу,
    перестают читать — а тогда оно не спасёт и в настоящий раз.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return _sha256_bytes(text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"))


def _fingerprint(data: bytes) -> dict:
    """Отпечаток доставленного файла: точный и «без перевода строк»."""
    return {"sha": _sha256_bytes(data), "txt": _text_sha(data)}


def baseline(root: Path) -> dict:
    """{путь: отпечаток} — что завод доставил сюда своими руками. Нет файла →
    пусто: на копии, которая ещё ни разу не обновлялась после этого нововведения,
    сравнивать не с чем, и мы честно ничего не утверждаем."""
    data = _read_json(root / BASELINE_FILE, {})
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict):
        return {}
    # Голая строка — формат первого дня (только точный sha, без нормализации
    # переводов строк). Читаем его как есть, чтобы обновление самого updater.py
    # не обнулило уже накопленный слепок.
    return {rel: (val if isinstance(val, dict) else {"sha": val, "txt": None})
            for rel, val in files.items() if isinstance(val, (str, dict))}


def save_baseline(root: Path, delivered: dict) -> None:
    """Дописать в слепок то, что доставил этот патч.

    Именно ДОПИСАТЬ: патчи кумулятивные, но состав между версиями меняется, и
    файл, уехавший год назад и не вошедший в сегодняшний архив, всё равно наш —
    выбросив его из слепка, мы потеряли бы способность заметить правку в нём.
    """
    merged = baseline(root)
    merged.update(delivered)
    try:
        (root / BASELINE_FILE).parent.mkdir(parents=True, exist_ok=True)
        (root / BASELINE_FILE).write_text(
            json.dumps({"updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "files": merged}, ensure_ascii=False, indent=2),
            encoding="utf-8")
    except OSError:
        pass          # слепок — страховка, а не условие работы обновления


def local_changes(root: Path, only=None) -> list:
    """НАШИ файлы, изменённые на этой копии после доставки.

    only — ограничить проверку этим набором путей (при обновлении интересны
    только те файлы, которые патч сейчас собирается заменить). Файл, которого
    нет на диске, не считается изменённым: его отсутствие — отдельная история
    (см. BOX-60), и обновление его просто вернёт.
    """
    out = []
    for rel, delivered in sorted(baseline(root).items()):
        if only is not None and rel not in only:
            continue
        path = root / rel
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if _sha256_bytes(data) == delivered.get("sha"):
            continue                       # байт в байт наш файл
        want_txt = delivered.get("txt")
        if want_txt and _text_sha(data) == want_txt:
            continue                       # разошлись только переводы строк
        out.append(rel)
    return out


def _diff_text(new_data: bytes, local_data: bytes, rel: str, version: str):
    """Чем КЛИЕНТСКАЯ версия отличается от НОВОЙ официальной. None — не текст.

    Направление выбрано не случайно: клиенту (и его агенту) нужно перенести свои
    изменения ПОВЕРХ приехавшего кода, а не вернуться к своей старой копии — в
    ней нет исправлений, ради которых обновление и приходило. Поэтому «слева»
    новая официальная версия, «справа» — его.
    """
    try:
        new_text = new_data.decode("utf-8")
        local_text = local_data.decode("utf-8")
    except UnicodeDecodeError:
        return None                     # шрифт/картинка — просто сохраняем копию
    # splitlines() без keepends + lineterm="" — тот же приём, что и в _text_sha:
    # сравниваем содержательные строки, а не CRLF против LF.
    lines = difflib.unified_diff(
        new_text.splitlines(), local_text.splitlines(),
        fromfile=f"{rel} — новая версия {version}",
        tofile=f"{rel} — ВАША версия (была на диске до обновления)",
        lineterm="")
    return "\n".join(lines)


def channel(root: Path) -> dict:
    """Настройки канала. Токен НИКОГДА не печатаем и не логируем."""
    cfg = _read_json(root / CHANNEL_FILE, {})
    return {"repo": cfg.get("repo") or DEFAULT_REPO,
            "token": cfg.get("token") or os.environ.get("KZ_UPDATE_TOKEN", ""),
            "url": cfg.get("url") or DEFAULT_MANIFEST_URL,
            "enabled": cfg.get("enabled", True)}


def channel_problem(ch: dict) -> str:
    """Почему канал НЕ СМОЖЕТ работать. Пусто — канал исправен.

    10.08.2026, по следам живого случая: у клиента не оказалось `bible_setup.py`,
    который уехал ещё патчем 1.2.0 и с тех пор входит в союз КАЖДОГО
    кумулятивного патча. То есть коробка не приняла ни одного обновления — а
    узнали мы об этом от самого клиента, через две недели и по косвенному
    признаку.
    Причина, почему не узнали раньше: `--auto` молчал и отдавал 0 ОДИНАКОВО во
    всех случаях — и когда обновлять нечего, и когда канал не настроен, и когда
    в коробке нет ключа доступа. Для агента клиента это неразличимо, и он
    честно шёл дальше. «Тихо» должно значить «нет новостей», а не «я сломан».
    Здесь мы отделяем ПОЛОМКУ НАСТРОЙКИ (о ней говорим вслух) от отсутствия
    новостей и от обрыва сети (о них по-прежнему молчим — это не вина коробки).
    """
    if not ch["repo"] and not ch["url"]:
        return ("канал обновлений не настроен: в коробке нет "
                f"{CHANNEL_FILE} с адресом")
    if ch["repo"] and not ch["token"]:
        return (f"в коробке нет ключа доступа к каналу {ch['repo']} — "
                f"репозиторий приватный, без ключа обновления не придут НИКОГДА")
    # Вторая немая смерть, найдена тем же опытом 10.08: без installer/_ed25519.py
    # подпись проверить нечем, а НЕПРОВЕРЕННЫЙ патч мы не применяем никогда —
    # значит коробка отвергает КАЖДЫЙ патч, и раньше делала это молча.
    # Файл едет довеском SFX_APP_EXTRA_FILES: у коробки, собранной не тем путём
    # (`build_box.py --out` вместо installer/build_macos.py), его просто нет —
    # ровно тот разрыв, что мак записал в BOX-47.
    try:
        _load_crypto()
    except Exception as e:                     # noqa: BLE001
        return (f"в коробке нечем проверить подпись патча ({e}) — установка "
                f"собрана не полностью, любой патч будет отвергнут")
    return ""


def _log_event(root: Path, **rec) -> None:
    """Дописать строку в журнал обновлений. Журнал — единственная местная улика.

    Раньше сюда писались только УСПЕХИ, поэтому на сломанной коробке файл был
    пуст, а `--status` бодро сообщал «Обновления ещё не применялись» — это
    выглядело как «ещё не было патчей», а не как «канал мёртв».
    """
    try:
        (root / LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
        with open(root / LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": int(time.time()), **rec},
                               ensure_ascii=False) + "\n")
    except OSError:
        pass                       # журнал — удобство, а не условие работы


def _gh_contents(repo: str, path: str) -> str:
    """Адрес файла в приватном репозитории через Contents API.

    raw.githubusercontent.com для приватного репо не отдаёт файл даже с токеном —
    работает именно этот путь (с заголовком Accept: application/vnd.github.raw).
    """
    return f"https://api.github.com/repos/{repo}/contents/{path.lstrip('/')}"


def describe_channel(ch: dict) -> str:
    """Человекочитаемое описание канала БЕЗ токена (для --status и сообщений)."""
    where = f"приватный репозиторий {ch['repo']}" if ch["repo"] else ch["url"] or "не задан"
    key = "ключ доступа вложен" if ch["token"] else "⚠ ключа доступа НЕТ"
    return f"{where} ({key})"


def _vtuple(v: str):
    """'1.2.10' → (1,2,10). Нечисловые части не роняют сравнение."""
    out = []
    for part in str(v).split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out + [0] * (3 - len(out)))[:3]


def is_newer(candidate: str, installed: str) -> bool:
    return _vtuple(candidate) > _vtuple(installed)


# ── подпись ──────────────────────────────────────────────────────────────────

def _load_crypto():
    """(модуль ed25519, публичный ключ). Тот же ключ, что проверяет лицензии."""
    inst = str(HERE / "installer")
    if inst not in sys.path:
        sys.path.insert(0, inst)
    import _ed25519 as ed                     # noqa: WPS433
    import license_pubkey as pk               # noqa: WPS433
    return ed, bytes.fromhex(pk.PUBLIC_KEY_HEX)


def canonical(payload: dict) -> bytes:
    """Байты для подписи. Тот же приём, что в license_gen: sort_keys + компактно."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def verify_manifest(manifest: dict) -> tuple[bool, str]:
    """Проверить подпись манифеста публичным ключом владельца."""
    try:
        ed, pub = _load_crypto()
    except Exception as e:                     # noqa: BLE001
        return False, f"в коробке нет проверки подписи ({e})"
    payload = manifest.get("payload")
    sig_b64 = manifest.get("sig_b64")
    if not isinstance(payload, dict) or not sig_b64:
        return False, "манифест без подписи — отвергнут"
    try:
        sig = base64.b64decode(sig_b64)
    except (ValueError, TypeError):
        return False, "подпись повреждена"
    if not ed.verify(pub, canonical(payload), sig):
        return False, "ПОДПИСЬ НЕ СОШЛАСЬ — патч не от владельца, отвергнут"
    return True, "подпись верна"


# ── сеть ─────────────────────────────────────────────────────────────────────

def _ssl_ctx() -> ssl.SSLContext:
    """Контекст проверки сертификата: корни certifi ПОВЕРХ системных.

    У портативного питона коробки системного хранилища корней может не быть
    вовсе, и тогда TLS-рукопожатие с GitHub падает CERTIFICATE_VERIFY_FAILED,
    а канал выглядит как «нет связи» — на этом шесть коробок простояли без
    обновлений две недели. certifi приезжает вместе с requests и лежит в .venv
    любой установленной коробки.

    Проверку НЕ ослабляем: корни только ДОБАВЛЯЮТСЯ к найденным в системе,
    режим проверки и хостнейм остаются как в create_default_context().
    Своей копии здесь нет намеренно — общий модуль пришлось бы возить патчем
    отдельным файлом, и коробка, не получившая его, падала бы на импорте.
    """
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except Exception:
        pass                      # certifi нет — остаёмся на системном хранилище
    return ctx


def _fetch(url: str, limit_mb: int = 5, token: str = "") -> bytes | None:
    """Скачать. None — сети нет / ошибка. Молча: офлайн не повод падать.

    Токен уходит ТОЛЬКО в заголовке Authorization и никогда не попадает ни в
    адрес, ни в вывод: в URL он утёк бы в логи прокси и историю.
    """
    ctx = _ssl_ctx()
    headers = {"User-Agent": "KZ-Updater/1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
        headers["Accept"] = "application/vnd.github.raw"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    req = urllib.request.Request(url, headers=headers)
    global _LAST_FETCH_ERROR
    _LAST_FETCH_ERROR = None
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT, context=ctx) as r:
            return r.read(limit_mb * 1024 * 1024 + 1)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as e:
        _LAST_FETCH_ERROR = e
        return None


def cert_failure() -> str:
    """Последняя сетевая неудача была отказом ДОВЕРИЯ, а не обрывом связи.

    Разница принципиальна и потому измеряется отдельно: при обрыве связи чинить
    нечего и молчать правильно, а здесь сеть есть, канал отвечает — и мы сами
    не можем проверить его сертификат. Так выглядит перехват трафика
    антивирусом или корпоративным прокси: корней certifi для этого мало,
    подписан такой сертификат локальным корнем из системного хранилища.
    Молчать об этом нельзя — именно неотличимость «сломан» от «новостей нет»
    стоила шести коробкам двух недель без обновлений.
    """
    e = _LAST_FETCH_ERROR
    if e is None:
        return ""
    reason = getattr(e, "reason", e)
    if isinstance(reason, ssl.SSLError) or isinstance(e, ssl.SSLError):
        return str(getattr(reason, "reason", "") or reason)
    return ""


def fetch_manifest(ch: dict) -> tuple[dict | None, str]:
    url = _gh_contents(ch["repo"], "latest.json") if ch["repo"] else ch["url"]
    if not url:
        return None, "канал обновлений не настроен"
    raw = _fetch(url, token=ch["token"])
    if raw is None:
        return None, "нет связи с каналом обновлений (это не ошибка — работаем дальше)"
    try:
        return json.loads(raw.decode("utf-8")), "манифест получен"
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "канал вернул не манифест"


# ── проверка содержимого патча ───────────────────────────────────────────────

def _path_allowed(rel: str) -> tuple[bool, str]:
    """Можно ли патчу трогать этот путь. Здесь живёт вся защита клиента."""
    # ⚠️ НЕ lstrip("./") — он срезает ЛЮБЫЕ ведущие точки и слэши, превращая
    # «../чужое.py» в «чужое.py», и проверка на «..» ниже становится слепой
    # (поймано тестом test_updater). Снимаем ровно префикс «./».
    p = rel.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    p = p.lstrip("/")
    if not p or p.endswith("/"):
        return False, "пустой путь"
    if ".." in Path(p).parts or Path(p).is_absolute() or ":" in p:
        return False, f"выход за пределы коробки: {rel}"   # zip-slip
    low = p.lower()
    for bad in _FORBIDDEN:
        if low.startswith(bad.lower()) or f"/{bad.lower()}" in f"/{low}":
            return False, f"запрещённая зона: {rel}"
    if Path(p).suffix.lower() not in _ALLOWED_SUFFIX:
        return False, f"недопустимый тип файла: {rel}"
    return True, ""


def inspect_patch(zf: zipfile.ZipFile, files_meta: dict) -> tuple[bool, list[str], list[str], list[str]]:
    """Проверить КАЖДЫЙ файл архива до распаковки: путь, зона, хеш.

    files_meta: {путь: sha256} из ПОДПИСАННОГО манифеста. Файл, которого нет в
    манифесте, не применяется — иначе подпись защищала бы только часть архива.

    Возвращает (ok, ok_files, problems, skipped). BOX-45: файл НЕДОПУСТИМОГО
    ТИПА (`_path_allowed` → «недопустимый тип файла: …») не роняет патч
    целиком — уходит в `skipped`, остальной патч применяется как обычно.
    Иначе кумулятивный патч, догоняющий давно не обновлявшуюся коробку,
    никогда не пройдёт: старый клиентский `updater.py` не пропустит ни один
    путь типа, добавленного в белый список ПОСЛЕ его собственной сборки — а
    без применённого патча он никогда и не получит свежий `updater.py` с
    расширенным списком. Живой случай 13.08: копия 1.0.4 (белый список без
    .jpg) отвергала ВЕСЬ патч 1.3.9 из-за одной картинки. Остальные причины
    (zip-slip, запретная зона, файл вне подписи, несошедшийся хеш) — это
    защита клиента, её не ослаблять: они по-прежнему роняют патч целиком.
    """
    problems, ok_files, skipped, seen = [], [], [], set()
    for info in zf.infolist():
        if info.is_dir():
            continue
        rel = info.filename
        seen.add(rel)
        good, why = _path_allowed(rel)
        if not good:
            if why.startswith("недопустимый тип файла:"):
                skipped.append(why)
            else:
                problems.append(why)
            continue
        if rel not in files_meta:
            problems.append(f"файла нет в подписанном списке: {rel}")
            continue
        digest = hashlib.sha256(zf.read(rel)).hexdigest()
        if digest != files_meta[rel]:
            problems.append(f"хеш не сошёлся: {rel}")
            continue
        ok_files.append(rel)
    # `problems` — уже готовые ЧЕЛОВЕЧЕСКИЕ фразы («недопустимый тип файла: X»),
    # а не голые пути. Раньше missing сверялся с этим списком напрямую
    # (`f not in problems`) — голый путь никогда не совпадал с фразой, и
    # КАЖДЫЙ файл, отклонённый по любой другой причине (тип, зона, хеш),
    # задваивался ещё и «обещан, но отсутствует в архиве». Сверяем с `seen`
    # (голые пути, которые вообще встретились в архиве) — только по-настоящему
    # отсутствующие в zip пути теперь попадают в missing.
    missing = [f for f in files_meta if f not in seen]
    if missing:
        problems.extend(f"обещан, но отсутствует в архиве: {m}" for m in missing)
    return (not problems), ok_files, problems, skipped


# ── применение ───────────────────────────────────────────────────────────────

def apply_patch(root: Path, zf: zipfile.ZipFile, files: list[str],
                version: str, quiet: bool = False) -> tuple[bool, str]:
    """Атомарно заменить файлы. Любая ошибка → полный откат.

    Файлы, которые на этой копии правили (см. local_changes), заменяются тоже —
    иначе исправление, ради которого патч и ехал, не доедет, — но клиентская
    версия каждого такого файла откладывается отдельно и о ней сообщается вслух.
    """
    backup = root / BACKUP_DIR / f"before-{version}"
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    backup.mkdir(parents=True, exist_ok=True)
    # ЧТО ПРАВИЛИ НА ЭТОЙ КОПИИ — считаем ДО первой замены, потом будет поздно.
    had_baseline = bool(baseline(root))
    edited = set(local_changes(root, only=set(files)))
    edits_dir = root / BACKUP_DIR / LOCAL_EDITS_DIR / version
    saved_edits: list[str] = []
    replaced: list[str] = []
    delivered: dict = {}
    try:
        for rel in files:
            dst = root / rel
            new_data = zf.read(rel)
            if dst.exists():                       # сохранить прежнюю версию
                bdst = backup / rel
                bdst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, bdst)
            if rel in edited:
                # Клиентскую версию кладём ОТДЕЛЬНО от before-<версия>/: та
                # резервная копия существует ради отката всего патча целиком и
                # чистится при повторном применении той же версии. Правки
                # человека должны пережить и то, и другое.
                local_data = dst.read_bytes()
                edst = edits_dir / rel
                edst.parent.mkdir(parents=True, exist_ok=True)
                edst.write_bytes(local_data)
                diff = _diff_text(new_data, local_data, rel, version)
                if diff:
                    edst.with_name(edst.name + ".diff").write_text(
                        diff, encoding="utf-8")
                saved_edits.append(rel)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(new_data)
            delivered[rel] = _fingerprint(new_data)
            replaced.append(rel)
    except Exception as e:                         # noqa: BLE001
        for rel in replaced:                       # откат
            bsrc = backup / rel
            if bsrc.exists():
                shutil.copy2(bsrc, root / rel)
            else:
                (root / rel).unlink(missing_ok=True)
        return False, f"обновление отменено и откачено: {e}"

    # Слепок доставленного обновляем ТОЛЬКО после успешной замены: иначе после
    # отката коробка считала бы своими файлы, которых на диске нет.
    save_baseline(root, delivered)
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / VERSION_FILE).write_text(
        json.dumps({"version": version,
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "files": len(files)}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    with open(root / LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": int(time.time()), "version": version,
                            "files": files}, ensure_ascii=False) + "\n")
    # Об УСПЕХЕ говорим всегда, в том числе в --auto. Раньше тихими были все три
    # исхода — «обновилось», «нечего обновлять» и «не смог», — и отличить их не
    # мог ни агент клиента, ни мы. Молчание оставляем только за «новостей нет».
    if quiet:
        print(f"Завод обновлён до версии {version} (файлов: {len(files)}).")
    else:
        print(f"Обновлено до версии {version}: файлов {len(files)}. "
              f"Прежние версии — в {BACKUP_DIR}/before-{version}/")
    if not had_baseline:
        # РОВНО ОДИН РАЗ на каждой уже живущей копии. Защита ниже опирается на
        # слепок, а он появляется только сейчас, вместе с этим самым патчем, —
        # значит именно это обновление ещё не могло знать о правках человека и
        # заменило их вслепую. Промолчать здесь означало бы повторить ту самую
        # ошибку последний раз, но зато на всех клиентах сразу.
        print("")
        print("Это первое обновление, после которого коробка запоминает, какие "
              "файлы она доставила (config/box_files.json).")
        print("Если вы или ваш помощник дорабатывали код завода под себя — "
              f"прежние версии всех заменённых сейчас файлов лежат в "
              f"{BACKUP_DIR}/before-{version}/, проверьте их сегодня.")
        print("Начиная со следующего обновления коробка назовёт такие файлы "
              "сама и сохранит ваши версии отдельно — вслепую это больше не "
              "повторится.")
    if saved_edits:
        # Про затёртые правки говорим ВСЕГДА, включая --auto: молчание здесь и
        # было всей проблемой. Это не ошибка обновления — это факт, о котором
        # человек обязан узнать в ту же минуту, а не через неделю по симптомам.
        _log_event(root, event="local_edits", version=version, files=saved_edits)
        print("")
        print("⚠️ ВАЖНО: эти файлы на вашей копии были ИЗМЕНЕНЫ ВРУЧНУЮ, и "
              "обновление положило поверх них новую версию:")
        for rel in saved_edits:
            print(f"  · {rel}")
        print(f"Ваши версии целиком сохранены в {BACKUP_DIR}/{LOCAL_EDITS_DIR}/"
              f"{version}/, рядом с каждой лежит файл .diff — в нём видно, чем "
              f"ваша версия отличалась от новой.")
        print("Ничего не потеряно, но правки НЕ перенеслись сами: откройте "
              ".diff и перенесите нужное в новый файл (или попросите об этом "
              "помощника). Откатывать всё обновление ради них не нужно.")
    return True, "ок"


# ── послеустановочные шаги (BOX-73) ──────────────────────────────────────────
#
# ЗАЧЕМ ЭТО ЕСТЬ. Патч 1.3.44 уехал трём живым клиентам. Он завёл в контракте
# новые папки Google Диска, но САМИ ПАПКИ создаёт генератор, а не патч: после
# установки нужен был один прогон `python -m intake.drive_layout --root ...`.
# Про шаг знали заранее (docs/ПАТЧ_1.3.43_НАРЕЗКА_ЭФИРОВ.md), но передать его
# клиенту было НЕЧЕМ — у механизма обновлений не существовало способа сказать
# «после установки нужно ещё вот это». Обновление отчиталось успехом, версия
# сменилась, а приём материалов умер у всех троих: `intake` был единственным
# работавшим модулем, было «1 из 8» — стало «0 из 8».
#
# Это четвёртый случай одного класса за неделю — ОТКАЗ, ВЫГЛЯДЯЩИЙ КАК УСПЕХ.
# Поэтому чиним не шаг, а механизм.
#
# ПОЧЕМУ НЕ ПРОИЗВОЛЬНАЯ КОМАНДА В МАНИФЕСТЕ. Это код, который выполняется на
# чужих машинах. Строка оболочки в манифесте — это удалённое выполнение чего
# угодно у клиента; подпись защищает от постороннего, но не от нашей же ошибки
# и не от утечки приватного семени. Поэтому манифест не несёт НИ КОМАНДЫ, НИ
# АРГУМЕНТОВ: он может только НАЗВАТЬ шаг по имени из белого списка ниже. Что
# именно делает шаг и на каких данных — записано здесь, в коробке клиента, и
# приезжает обычным обновлением этого файла. Аргументы (папка Google Диска)
# берутся из КОНФИГА КЛИЕНТА, а не из патча. Ничего не запускается через
# оболочку и ничего не уходит в сеть.
#
# ПОЧЕМУ ОТКАЗ ШАГА НЕ ОТКАТЫВАЕТ ПАТЧ. Файлы уже заменены и исправления уже
# доехали — откатывать их из-за несозданной папки хуже, чем оставить. Но
# промолчать нельзя: провал шага печатается вслух (в том числе в `--auto`),
# попадает в журнал, оставляет отметку `config/post_steps_pending.json` и
# делает код возврата 1. Тихого «успеха» здесь не бывает ни при каком исходе.

#: Отметка «после обновления остался невыполненный шаг». Существует ровно до
#: тех пор, пока шаг не отработает: `--status` о ней кричит, а любой модуль
#: завода может прочитать её при старте и не дать использовать себя молча в
#: урезанном режиме (страховка «минимум» внутри основного варианта).
POST_PENDING_FILE = "config/post_steps_pending.json"


def _factory_answers(root: Path) -> dict:
    """Несекретные ответы клиента (config/factory.config.json). Нет — {}."""
    data = _read_json(root / "config" / "factory.config.json", {})
    return data if isinstance(data, dict) else {}


def _gdrive_root(root: Path) -> str:
    """Папка Google Диска клиента из ЕГО конфига. Пусто — не задана.

    Ключ сверяем послабленно (регистр, пробелы, дефисы) — ровно как это делает
    install/factory_config.py: конфиг заполняет мастер установки, но человек
    имеет право поправить его руками.
    """
    for key, val in _factory_answers(root).items():
        canon = str(key).strip().lower().replace("-", "_").replace(" ", "_")
        if canon == "gdrive_root" and isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _step_intake_drive_layout(root: Path) -> tuple[bool, str]:
    """Довести структуру папок на диске клиента до текущего контракта.

    Выполнять автоматически безопасно, потому что генератор ИДЕМПОТЕНТЕН — это
    проверено по коду `modules/intake/drive_layout.py`, а не по обещанию в
    документации: папки создаются через `mkdir(exist_ok=True)`, подсказки
    пишутся только при отсутствии файла (`_write_if_absent`), карточка клиента
    не перезаписывается, а папки старой структуры лишь перечисляются в отчёте —
    ничего не удаляется и не переименовывается. Повторный прогон на уже готовом
    диске не меняет ни одного существующего файла.
    """
    base = _gdrive_root(root)
    if not base:
        return False, ("в config/factory.config.json не указана папка Google "
                       "Диска (ключ gdrive_root) — завод не знает, где создавать "
                       "папки")
    mods = str(root / "modules")
    if mods not in sys.path:
        sys.path.insert(0, mods)
    try:
        import importlib                                   # noqa: WPS433
        drive_layout = importlib.import_module("intake.drive_layout")
        report = drive_layout.ensure_structure(base)
    except Exception as e:                                 # noqa: BLE001
        return False, f"генератор структуры не отработал: {e}"
    errors = report.get("errors") or []
    if errors:
        return False, "; ".join(str(x) for x in errors)
    return True, (f"папок создано {len(report.get('created_dirs', []))}, "
                  f"уже было {len(report.get('kept_dirs', []))}")


#: БЕЛЫЙ СПИСОК ПОСЛЕУСТАНОВОЧНЫХ ШАГОВ. Манифест может назвать ТОЛЬКО ключ
#: отсюда — ни команды, ни аргументов он не несёт. Имя, которого здесь нет,
#: отвергается вслух (а не молча пропускается): это либо патч новее коробки,
#: либо подмена, и оба случая человек обязан увидеть.
POST_STEPS = {
    "intake.drive_layout": {
        "title": "создать новые папки на Google Диске (структура intake)",
        "manual": 'python -m intake.drive_layout --root "<папка Google Диска>"',
        "run": _step_intake_drive_layout,
    },
}


def post_steps_from(payload: dict) -> tuple[list[str], list[str]]:
    """(разрешённые шаги, отвергнутые имена) из ПОДПИСАННОЙ части манифеста.

    Читается исключительно `payload` — то есть ровно то, что покрыто подписью
    (см. `canonical()`/`verify_manifest`). Поле, дописанное рядом с payload,
    сюда не попадает никогда: подделать список шагов по дороге нельзя, а
    подмена внутри payload ломает подпись и патч не применяется вовсе.

    Поля нет → ([], []): патчи без послешагов ведут себя ровно как раньше.
    """
    raw = payload.get("post_steps")
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], [f"post_steps не список: {type(raw).__name__}"]
    steps, rejected = [], []
    for item in raw:
        # Только строка-имя. Словарь с «командой» или «аргументами» — это ровно
        # то, чего здесь не должно быть никогда, поэтому даже не разбираем.
        if not isinstance(item, str) or item not in POST_STEPS:
            rejected.append(str(item)[:80])
        elif item not in steps:
            steps.append(item)
    return steps, rejected


def _write_pending(root: Path, version: str, failures: list[dict]) -> None:
    """Записать (или снять) отметку о невыполненном шаге."""
    path = root / POST_PENDING_FILE
    if not failures:
        try:
            path.unlink()
        except OSError:
            pass
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(
            {"version": version, "ts": int(time.time()),
             "steps": [{"step": f["step"], "why": f["why"],
                        "manual": f.get("manual", "")} for f in failures]},
            ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass                      # отметка — страховка, а не условие работы


def run_post_steps(root: Path, payload: dict, version: str,
                   quiet: bool = False) -> list[dict]:
    """Выполнить шаги, объявленные патчем. Возвращает список ПРОВАЛОВ.

    Пустой список — либо шагов не было, либо все отработали. Непустой — клиент
    ОБЯЗАН это увидеть: печатаем всегда (включая `--auto`), пишем в журнал и
    оставляем отметку, а вызывающий превращает это в код возврата 1.
    """
    steps, rejected = post_steps_from(payload)
    if not steps and not rejected:
        return []                                  # старое поведение, без слов
    failures: list[dict] = []
    for name in rejected:
        why = ("шаг не из белого списка этой коробки — обновление могло быть "
               "новее её или подменено")
        failures.append({"step": name, "why": why, "manual": ""})
        _log_event(root, event="post_step", version=version, step=name,
                   ok=False, why=why)
    for name in steps:
        spec = POST_STEPS[name]
        try:
            ok, why = spec["run"](root)
        except Exception as e:                     # noqa: BLE001
            ok, why = False, f"шаг упал с ошибкой: {e}"
        _log_event(root, event="post_step", version=version, step=name,
                   ok=bool(ok), why=why)
        if ok:
            if not quiet:
                print(f"После обновления выполнено: {spec['title']} ({why}).")
        else:
            failures.append({"step": name, "why": why,
                             "manual": spec.get("manual", ""),
                             "title": spec.get("title", "")})
    _write_pending(root, version, failures)
    if failures:
        # Громко и всегда. Молчание здесь и было всей причиной BOX-73.
        print("")
        print("⚠️ ОБНОВЛЕНИЕ УСТАНОВЛЕНО, НО ОБЯЗАТЕЛЬНЫЙ ШАГ ПОСЛЕ НЕГО НЕ "
              "ВЫПОЛНЕН. Часть завода будет работать не полностью, пока это "
              "не исправлено:")
        for f in failures:
            print(f"  ✗ {f.get('title') or f['step']} — {f['why']}")
            if f.get("manual"):
                print(f"    выполните вручную: {f['manual']}")
        print(f"Файлы обновления уже установлены (версия {version}), откатывать "
              f"их не нужно. Отметка о невыполненном шаге сохранена в "
              f"{POST_PENDING_FILE} — покажите эту строку разработчику, если "
              f"команда выше не помогла.")
    return failures


def pending_post_steps(root: Path) -> dict:
    """Отметка о невыполненном послеустановочном шаге. Нет — {}."""
    data = _read_json(root / POST_PENDING_FILE, {})
    return data if isinstance(data, dict) and data.get("steps") else {}


def rollback(root: Path) -> int:
    """Вернуть предыдущую версию из последней резервной копии."""
    base = root / BACKUP_DIR
    backups = sorted((p for p in base.glob("before-*") if p.is_dir()),
                     key=lambda p: p.stat().st_mtime) if base.is_dir() else []
    if not backups:
        print("Резервных копий нет — откатывать нечего.")
        return 2
    last = backups[-1]
    n = 0
    restored = {}
    for src in last.rglob("*"):
        if src.is_file():
            rel = src.relative_to(last)
            dst = root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            try:
                restored[rel.as_posix()] = _fingerprint(dst.read_bytes())
            except OSError:
                pass
            n += 1
    # Откат — сознательный выбор человека: с этой минуты именно восстановленное
    # состояние считается исходным. Без этой строчки каждый откаченный файл
    # немедленно попал бы в «изменён вручную» и следующий патч завалил бы
    # человека предупреждениями обо всех файлах сразу.
    save_baseline(root, restored)
    print(f"Откат выполнен из {last.name}: восстановлено файлов {n}.")
    print("Версия в config/box_version.json осталась прежней — при следующем "
          "обновлении патч приедет заново.")
    return 0


# ── сценарии ─────────────────────────────────────────────────────────────────

def check(root: Path, quiet: bool = False) -> tuple[dict | None, str]:
    """Есть ли обновление. (payload или None, сообщение)."""
    ch = channel(root)
    if not ch["enabled"]:
        return None, "обновления отключены в config/update_channel.json"
    # Отличаем «не настроено» от «нет связи»: раньше коробка без ключа отвечала
    # «нет связи с каналом обновлений» — и человек шёл чинить интернет, который
    # был исправен. Причина видна БЕЗ сети, поэтому и говорим о ней сразу.
    why = channel_problem(ch)
    if why:
        _log_event(root, event="blocked", reason=why)
        return None, f"обновления не придут: {why}"
    manifest, msg = fetch_manifest(ch)
    if manifest is None:
        return None, msg
    good, why = verify_manifest(manifest)
    if not good:
        return None, why
    payload = manifest["payload"]
    have, want = current_version(root), str(payload.get("version", ""))
    if not is_newer(want, have):
        return None, f"установлена последняя версия ({have})"
    return payload, f"доступно обновление {have} → {want}"


def update(root: Path, quiet: bool = False) -> int:
    # Поломка НАСТРОЙКИ канала — единственный случай, когда `--auto` обязан
    # заговорить: сам себя такой клиент не починит и молчать о нём нельзя
    # (10.08.2026 это стоило нам двух недель слепоты). Код возврата остаётся 0:
    # обещание «обновление никогда не ломает рабочий день» в силе.
    ch0 = channel(root)
    if ch0["enabled"]:
        why = channel_problem(ch0)
        if why:
            print(f"ОБНОВЛЕНИЯ НЕ РАБОТАЮТ: {why}.")
            print("Это не ваша ошибка и работе завода не мешает, но новые "
                  "исправления до вас не доедут. Покажите эту строку "
                  "разработчику — чинится с его стороны.")
            _log_event(root, event="blocked", reason=why)
            return 0
    payload, msg = check(root, quiet=quiet)
    if payload is None:
        # Отказ ДОВЕРИЯ каналу — тоже поломка, о которой обязан знать человек:
        # сеть есть, канал отвечает, но сертификат проверить нечем. Тихий режим
        # тут не действует ровно по той же причине, что и для поломки настройки.
        cert = cert_failure()
        if cert:
            print("ОБНОВЛЕНИЯ НЕ РАБОТАЮТ: канал отвечает, но его сертификат не "
                  f"проверяется ({cert}).")
            print("Так бывает, когда трафик проверяет антивирус или рабочая "
                  "сеть. Работе завода не мешает, но исправления до вас не "
                  "доедут — покажите эту строку разработчику.")
            _log_event(root, event="blocked", reason=f"TLS: {cert}")
        elif not quiet:
            print(msg)
        return 0                                   # нечего/нет сети — не ошибка
    if not quiet:
        print(msg)
        for line in payload.get("notes", [])[:10]:
            print(f"  • {line}")

    ch, version = channel(root), str(payload["version"])
    # patch_path — файл в том же приватном репозитории (обычный случай);
    # patch_url — прямой адрес, если канал переехал на свой хостинг.
    if payload.get("patch_path") and ch["repo"]:
        url = _gh_contents(ch["repo"], payload["patch_path"])
    else:
        url = payload.get("patch_url")
    if not url:
        print("В манифесте нет ссылки на патч."); return 1
    blob = _fetch(url, limit_mb=_MAX_PATCH_MB, token=ch["token"])
    if blob is None:
        if not quiet:
            print("Патч скачать не удалось — попробуем в следующий раз.")
        return 0
    if hashlib.sha256(blob).hexdigest() != payload.get("patch_sha256"):
        print("Хеш патча не сошёлся с подписанным манифестом — обновление отвергнуто.")
        return 1

    with tempfile.TemporaryDirectory(prefix="kzupd_") as tmp:
        zpath = Path(tmp) / "patch.zip"
        zpath.write_bytes(blob)
        try:
            with zipfile.ZipFile(zpath) as zf:
                good, files, problems, skipped = inspect_patch(zf, payload.get("files", {}))
                if not good:
                    print("Патч отвергнут — содержимое не совпало с подписью:")
                    for p in problems[:10]:
                        print(f"  ✗ {p}")
                    return 1
                if skipped and not quiet:
                    print("Патч применён не целиком — пропущены файлы недопустимого "
                          "типа (BOX-45, это не авария, устаревший белый список "
                          "версии): их вернёт следующее обновление.")
                    for s in skipped[:10]:
                        print(f"  ⚠ {s}")
                ok, why = apply_patch(root, zf, files, version, quiet=quiet)
        except zipfile.BadZipFile:
            print("Патч повреждён (не архив) — обновление отменено.")
            return 1
    if not ok:
        print(why)
        return 1
    # Шаги, без которых обновление неполно (BOX-73). Их провал НЕ откатывает
    # патч — файлы уже заменены и исправления доехали, — но и успехом это не
    # называется: код возврата 1, и клиент увидел отказ на экране.
    if run_post_steps(root, payload, version, quiet=quiet):
        return 1
    return 0


def status(root: Path) -> int:
    ch = channel(root)
    print(f"Версия коробки: {current_version(root)}")
    print(f"Канал обновлений: {describe_channel(ch)}")
    print(f"Обновления: {'включены' if ch['enabled'] else 'ОТКЛЮЧЕНЫ'}")
    # Диагноз БЕЗ сети — это главная ценность --status: именно его просим
    # выполнить клиента, когда непонятно, доехало ли обновление.
    why = channel_problem(ch) if ch["enabled"] else ""
    if why:
        print(f"ПРОБЛЕМА: {why}.")
        print("Пока это так, ни одно обновление до коробки не дойдёт.")

    log = root / LOG_FILE
    recs = []
    if log.is_file():
        for l in log.read_text(encoding="utf-8", errors="replace").splitlines():
            if not l.strip():
                continue
            try:
                recs.append(json.loads(l))
            except json.JSONDecodeError:
                continue
    applied = [r for r in recs if r.get("version")]
    blocked = [r for r in recs if r.get("event") == "blocked"]
    if applied:
        print(f"Обновлений применено: {len(applied)}")
        for rec in applied[-3:]:
            print(f"  · {rec['version']} — файлов {len(rec.get('files', []))}")
    else:
        # Раньше здесь всегда стояло бодрое «обновления ещё не применялись» —
        # на мёртвой коробке это читалось как «патчей пока не было».
        print("Обновления НИ РАЗУ не применялись."
              + ("" if why else " Если патчи выпускались — это уже повод "
                                "проверить связь с каналом (--check)."))
    if blocked:
        print(f"Неудачных попыток (канал недоступен): {len(blocked)}, "
              f"последняя причина: {blocked[-1].get('reason', '?')}")
    pending = pending_post_steps(root)
    if pending:
        # Второй голос той же беды: если провал шага проговорили один раз в
        # момент обновления и на этом успокоились, человек про него забудет —
        # а завод так и останется в урезанном режиме.
        print("")
        print(f"⚠️ ПОСЛЕ ОБНОВЛЕНИЯ {pending.get('version', '?')} ОСТАЛСЯ "
              f"НЕВЫПОЛНЕННЫЙ ШАГ — часть завода работает не полностью:")
        for st in pending.get("steps", []):
            print(f"  ✗ {st.get('step')} — {st.get('why')}")
            if st.get("manual"):
                print(f"    выполните вручную: {st['manual']}")
    if baseline(root):
        changed = local_changes(root)
        if changed:
            print(f"Файлов завода изменено вручную на этой копии: {len(changed)} "
                  f"(подробно — updater.py --local-changes).")
    return 0


def show_local_changes(root: Path) -> int:
    """Что на этой копии правили руками. Без сети, ничего не меняет.

    Нужно ДО обновления: увидев список заранее, человек решает, переносить ли
    правку в конфиг или готовиться перенести её в новую версию файла.
    """
    base = baseline(root)
    if not base:
        print("Пока не с чем сравнивать: слепок доставленных файлов появится "
              "после первого же обновления (config/box_files.json).")
        print("До этого момента коробка честно не знает, какие файлы правили, "
              "и не будет этого выдумывать.")
        return 0
    changed = local_changes(root)
    if not changed:
        print(f"Файлы завода не изменялись вручную (сверено {len(base)} файлов).")
        return 0
    print(f"Изменены вручную (сверено {len(base)} файлов завода):")
    for rel in changed:
        print(f"  · {rel}")
    print("")
    print("Это не ошибка — так и должно быть, если код дорабатывали под себя. "
          "Но следующее обновление положит поверх них новую версию: ваши копии "
          f"оно сохранит в {BACKUP_DIR}/{LOCAL_EDITS_DIR}/<версия>/ и назовёт "
          "каждый такой файл вслух.")
    print("Если у этой настройки есть готовый ключ в config/factory.config.json — "
          "надёжнее держать её там: конфиг обновление не трогает вообще никогда. "
          "Если ключа нет, правка в коде законна — просто она переживает "
          "обновление не сама, а через .diff.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Тихое обновление коробки подписанными патчами.")
    ap.add_argument("--root", help="корень установки (по умолчанию — авто)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="есть ли новая версия")
    g.add_argument("--apply", action="store_true", help="скачать и применить")
    g.add_argument("--auto", action="store_true",
                   help="тихо применить (для агента/планировщика)")
    g.add_argument("--status", action="store_true", help="версия и канал, без сети")
    g.add_argument("--local-changes", action="store_true",
                   help="какие файлы завода правили на этой копии (без сети)")
    g.add_argument("--rollback", action="store_true", help="вернуть прежнюю версию")
    a = ap.parse_args(argv)

    root = find_root(a.root)
    if a.status:
        return status(root)
    if a.local_changes:
        return show_local_changes(root)
    if a.rollback:
        return rollback(root)
    if a.check:
        payload, msg = check(root)
        print(msg)
        return 0
    return update(root, quiet=a.auto)


if __name__ == "__main__":
    sys.exit(main())

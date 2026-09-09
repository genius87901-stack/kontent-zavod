# -*- coding: utf-8 -*-
"""Проверка готовности модуля publishing.

Печатает ПО-РУССКИ и человеческим языком, что готово, а что нет, отдельно по
каждой площадке. Не стектрейс. Код выхода (CONTRACT.md §2):
    0 — всё заполнено, публикация возможна на все площадки;
    2 — чего-то не хватает (модуль всё равно работает в урезанном режиме —
        каждая площадка независима, недостающий ключ гасит только свою);
    1 — сама проверка не смогла отработать.

Запуск (оба варианта работают):
    python -m publishing.doctor
    python modules/publishing/doctor.py
"""
import importlib.util as _ilu
import json
import shutil
import sys
import os
from pathlib import Path

# ЕДИНЫЙ РИДЕР НАСТРОЕК — тот же, которым пользуется боевой код (autopost.py,
# vkpost.py, vkcarousel.py, vk_wall_post.py, tg_send.py). Грузим ПО ПУТИ, а не
# импортом пакета: doctor запускают и как `python -m publishing.doctor`, и как
# `python modules/publishing/doctor.py` — во втором случае относительный импорт
# не работает.
_spec = _ilu.spec_from_file_location(
    "publishing_config_reader",
    Path(__file__).resolve().parent / "config_reader.py")
config_reader = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(config_reader)

# Все ключи, которые модуль умеет читать. Секретные из окружения (или .env),
# несекретные — из config/factory.config.json (или .env).
KEYS = (
    "UPLOAD_POST_API_KEY", "UPLOAD_POST_USER",
    # VK_USER_TOKEN_KATE — канон (vk1.a.): карусель, видео/клипы, стена, сторис.
    # Остальные VK_* — запасной путь VK ID (vk2.a.): только видео, живёт час.
    "VK_USER_TOKEN_KATE", "VK_GROUP_ID",
    "VK_USER_TOKEN", "VK_APP_ID", "VK_REFRESH_TOKEN", "VK_DEVICE_ID",
    "TG_BOT_TOKEN", "TG_CHAT_ID",
    # Ответ на вопрос «присылать отчёт за день?» (модуль publishing). Читается
    # тем же ридером, что и ключи: у уже установленных клиентов вопроса в
    # мастере не было — доктор обязан спросить их сам, иначе они о нём и не
    # узнают (патч 1.3.26).
    "daily_report_enabled",
)

#: Команда включения суточного отчёта. Одна на весь доктор и на CLAUDE.md
#: коробки — чтобы формулировки не разошлись.
REPORT_ON_CMD = "python installer/schedule_report.py --enable"

#: Команда, которой клиент подключает ВК. Одна на весь доктор — чтобы в разных
#: ветках не разошлись формулировки и человек не гадал, какая из них настоящая.
#:
#: 🔴 08.09.2026 ЗДЕСЬ СТОЯЛА `python vk_auth.py --kate` — прямой вход в ВК через
#: приложение Kate Mobile. Он ЗАПРЕЩЁН НАВСЕГДА: у клиентки после такого входа
#: ВКонтакте заблокировал аккаунт целиком, снять блокировку может только сам
#: владелец аккаунта проверкой личности. Доктор — место, где человек читает
#: «что делать дальше», и подсказка отсюда исполняется не думая. Разрешённый
#: путь во ВКонтакте один: SmmBox.
VK_CONNECT_CMD = "python smmbox_connect.py"


# ── «чем доставить» под ТЕКУЩУЮ ОС (BOX-18) ──────────────────────────────────
# На маке команды winget не существует, и клиент упирался в тупиковую подсказку.
# ОС определяем ЕДИНЫМ принятым в проекте способом — install/check_env.py
# (detect_os + manifest_hint + ключи <os>_install из module.manifest.json).
# Своего определения ОС не заводим.
# Windows-путь не меняется: там возвращается windows_install из манифеста, а он
# совпадает с текстом, что был зашит здесь раньше; при любой неудаче — прежний текст.
_CHECK_ENV_CACHE = []      # [] — ещё не грузили; [mod|None] — загружено


def _check_env_module():
    """install/check_env.py как модуль (он не пакет и не лежит на PYTHONPATH)."""
    if _CHECK_ENV_CACHE:
        return _CHECK_ENV_CACHE[0]
    mod = None
    try:
        import importlib.util
        for parent in Path(__file__).resolve().parents:
            cand = parent / "install" / "check_env.py"
            if cand.is_file():
                spec = importlib.util.spec_from_file_location("factory_check_env", cand)
                if spec is not None and spec.loader is not None:
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                break
    except Exception:
        mod = None
    _CHECK_ENV_CACHE.append(mod)
    return mod


def _manifest_binary(name):
    """Запись system_binaries своего модуля по имени бинарника (или None)."""
    try:
        man = Path(__file__).resolve().parent / "module.manifest.json"
        data = json.loads(man.read_text(encoding="utf-8-sig"))
        for b in data.get("system_binaries", []) or []:
            if (b.get("name") or "").strip().lower() == name:
                return dict(b)
    except Exception:
        pass
    return None


def _ffmpeg_ok():
    """Есть ли ffmpeg ТАМ, ГДЕ ЕГО БЕРЁТ РЕНДЕР (BOX-51).

    Порядок ровно как в build_slideshow.py: сначала бинарь из пакета
    imageio-ffmpeg (он едет в комплекте и является штатным путём завода), и
    только потом системный — как необязательный запасной вариант.
    """
    try:
        import imageio_ffmpeg
        if Path(imageio_ffmpeg.get_ffmpeg_exe()).exists():
            return True
    except Exception:          # пакета нет или бинарь не распаковался
        pass
    return shutil.which("ffmpeg") is not None


def _install_hint(name, windows_hint):
    """Команда установки `name` для текущей ОС: winget / brew / apt."""
    ce = _check_env_module()
    if ce is None:
        return windows_hint
    try:
        os_key = ce.detect_os()
        entry = _manifest_binary(name) or {"name": name}
        if os_key == "windows":
            return (entry.get("windows_install") or "").strip() or windows_hint
        return ce.manifest_hint(entry, os_key)
    except Exception:
        return windows_hint


def find_root():
    """Корень установки — каталог с CONTRACT.md. Ищем вверх от этого файла.

    ⚠️ Это ПОДСКАЗКА, а не последнее слово. Найденный здесь путь уходит в
    config_reader.root() как отправная точка, и там же ищется настоящий маркер
    коробки (`.factory_root`) — его в репозитории владельца нет, а CONTRACT.md
    в коробку клиента не едет. Значит на клиентской машине корень определит
    config_reader, а не эта функция, и он же определит его для боевого кода —
    одинаково для обоих. Своей отдельной логики поиска корня у доктора больше
    нет по этой же причине.
    """
    # ⚠️ FACTORY_ROOT — ПЕРВЫМ (02.09.2026, разделение engine/ и data/).
    # Мультиклиентный завод кладёт код в `engine/`, а данные клиента в `data/`;
    # идя вверх от `engine/modules/...`, до `data/` не дойти никогда — деревья
    # соседние. Поэтому явно заданный корень сильнее поиска по маркерам.
    # Это же чинит известное расхождение: `factory_config` и
    # `publishing/config_reader` FACTORY_ROOT уважают, а этот поиск — нет, и
    # подставленный корень молча игнорировался (тот же класс, что записан в
    # уроках про `analytics/upload_post.py`).
    _задан = os.environ.get("FACTORY_ROOT")
    if _задан and Path(_задан).is_dir():
        return Path(_задан)
    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        if (parent / "CONTRACT.md").exists():
            return parent
    return Path.cwd()


def read_config(root):
    """Единственная точка доступа к конфигу (контракт чтения от OWNER).

    ⚠️ ЧИТАЕТ РОВНО ТО ЖЕ, ЧТО И БОЕВОЙ КОД. До 22.08.2026 здесь лежала своя
    копия чтения двух файлов, а autopost.py/vk*/tg_send.py читали только `.env` —
    и на одной и той же раскладке доктор отвечал «готово», а публикация падала
    словами «НЕ ХВАТАЕТ В .env: UPLOAD_POST_USER». Расхождение было опаснее
    самой поломки: клиент видел зелёный отчёт. Теперь источник один —
    `config_reader`, разойтись больше негде.

    Приоритет: окружение > .env > factory.config.json. Значения НЕ печатаются.
    """
    return config_reader.values(KEYS, start=root)


def has(values, key):
    v = values.get(key)
    return bool(v and str(v).strip())


def check():
    root = find_root()
    values = read_config(root)
    problems = []
    lines = []

    # ── Upload-Post: Instagram, YouTube, Threads, Telegram-лента ──
    up_key = has(values, "UPLOAD_POST_API_KEY")
    up_user = has(values, "UPLOAD_POST_USER")
    if up_key and up_user:
        lines.append("OK  Instagram / YouTube / Threads / Telegram-лента: готово (Upload-Post).")
    elif not up_key and not up_user:
        problems.append(
            "Не заполнен ключ Upload-Post — посты и Reels в Instagram, YouTube, "
            "Threads и ленту Telegram публиковаться НЕ будут. Остальное работает."
        )
    else:
        missing = "ключ Upload-Post" if not up_key else "название профиля Upload-Post"
        problems.append(
            "Заполнено не всё для Upload-Post (не хватает: %s) — Instagram, YouTube, "
            "Threads и лента Telegram публиковаться НЕ будут. Остальное работает." % missing
        )

    # ── ВКонтакте: карусели, видео и клипы ──
    #
    # ДВА РАЗНЫХ КЛЮЧА, А НЕ ДВА ИМЕНИ ОДНОГО (см. ALIASES в config_reader.py):
    #   VK_USER_TOKEN_KATE (vk1.a., scope offline) — умеет ВСЁ: фото-альбом на
    #       стене (настоящая листаемая карусель), video.save, wall.post, сторис.
    #       Практически не протухает. Это то, что получает клиент.
    #   VK_USER_TOKEN (vk2.a., VK ID) — ЗАПАСНОЙ и УРЕЗАННЫЙ: wall.post и фото
    #       на стену для него закрыты (1051/15), остаются только видео/клипы, и
    #       живёт он ЧАС. Продлевается сам только при полном комплекте
    #       VK_REFRESH_TOKEN + VK_APP_ID + VK_DEVICE_ID; без них через час
    #       публикация встанет с ошибкой 5, и вчерашняя работоспособность тут
    #       ничего не значит — именно на этом клиент и терялся.
    vk_kate = has(values, "VK_USER_TOKEN_KATE")
    vk_id_token = has(values, "VK_USER_TOKEN")
    vk_group = has(values, "VK_GROUP_ID")
    no_group_note = (" Номер сообщества (VK_GROUP_ID) не задан — публиковать "
                     "будем только на личную страницу, не в сообщество.")
    # 🔴 08.09.2026. Раньше здесь стояло «ВКонтакте готов», если в настройках
    # лежит ключ vk1.a. Теперь это было бы враньём: работать этим ключом
    # запрещено — он добывался прямым входом, после которого ВК блокирует
    # аккаунт клиента целиком. Ключ не удаляем (чужие настройки не наше дело),
    # но и не выдаём за готовность: публикация в ВК идёт через SmmBox, и
    # спрашивать про неё надо у мастера SmmBox, а не у этой строки.
    if vk_kate or vk_id_token:
        problems.append(
            "ВКонтакте: в настройках лежит старый прямой ключ ВК. Он БОЛЬШЕ НЕ "
            "ИСПОЛЬЗУЕТСЯ — прямая публикация в ВК запрещена с 08.09.2026 "
            "(после такого входа ВК блокирует аккаунт целиком, снять "
            "блокировку может только сам владелец аккаунта). Удалять ключ не "
            "нужно, он просто не работает. Публикация в ВК идёт через "
            "SmmBox: %s" % VK_CONNECT_CMD)
    else:
        problems.append(
            "ВКонтакте публикуется через SmmBox — проверьте, что он подключён: "
            "%s. Прямой ключ ВК заводить НЕ НУЖНО и НЕЛЬЗЯ: этот вход запрещён "
            "с 08.09.2026, после него ВК блокирует аккаунт." % VK_CONNECT_CMD)
    if not vk_group:
        problems.append(
            "Номер сообщества ВКонтакте (VK_GROUP_ID) не задан — если "
            "публикуете в сообщество, укажите его.")

    # ── Telegram-архив (обязательная копия каждого поста) ──
    tg_bot = has(values, "TG_BOT_TOKEN")
    tg_chat = has(values, "TG_CHAT_ID")
    if tg_bot and tg_chat:
        lines.append("OK  Telegram-архив: готов, каждый пост будет дублироваться в канал.")
        # Архив есть — значит отчёт за день физически возможен. Спрашиваем ОДИН
        # раз и только тех, кого не спрашивал мастер: у клиентов, установленных
        # до патча 1.3.25, этого вопроса в анкете не было, и сам по себе он у
        # них никогда бы не всплыл.
        report = str(values.get("daily_report_enabled") or "").strip().lower()
        if not report:
            problems.append(
                "У вас есть Telegram-архив, но вы ещё не сказали, нужен ли "
                "ОТЧЁТ ЗА ДЕНЬ. Это одно короткое сообщение утром: сколько "
                "постов вышло за вчера и на каких площадках, сколько ещё "
                "ждёт выхода, не заканчивается ли контент — и предупреждение, "
                "если пост должен был выйти, а его нет. "
                "Включить: %s (выключить потом — тем же с --disable). "
                "Ничего не делать тоже можно: отчёт просто не будет приходить."
                % REPORT_ON_CMD
            )
        elif report in ("да", "yes", "true", "1", "on"):
            lines.append("OK  Отчёт за день: включён, приходит в тот же архив.")
        else:
            lines.append("OK  Отчёт за день: выключен по вашему выбору.")
    elif not tg_bot and not tg_chat:
        problems.append(
            "Не настроен Telegram-архив (нет ключа бота) — копии постов в личный "
            "канал-архив складываться НЕ будут. На публикацию площадок это не влияет."
        )
    else:
        missing = "ключ бота" if not tg_bot else "адрес канала (@логин)"
        problems.append(
            "Telegram-архив настроен не полностью (не хватает: %s) — копии постов "
            "в архив складываться НЕ будут. На публикацию это не влияет." % missing
        )

    # ── ffmpeg: сборка видео (Reels) и запасное слайд-шоу карусели ──
    # ⚠️ КАРУСЕЛЬ В ВК ffmpeg БОЛЬШЕ НЕ ТРЕБУЕТ. Основной путь карусели —
    # vkcarousel.py, настоящий листаемый фото-альбом: слайды уходят картинками,
    # никакого видео не собирается. Слайд-шоу (vkpost.py --dir) осталось
    # запасным путём для урезанного ключа vk2.a. Формулировку держим точной:
    # клиенту нельзя говорить «карусель в ВК не выйдет», когда она выйдет.
    # BOX-51: проверяем РАБОЧИЙ путь, а не системный PATH. Слайд-шоу собирает
    # build_slideshow.py, и берёт он ffmpeg из пакета imageio-ffmpeg (тот же, что
    # весь остальной завод), а системный не нужен вовсе — install/check_env.py
    # так и помечает его: required=False. Старая проверка через shutil.which
    # заявляла клиенту «карусель нельзя собрать для ВКонтакте» на полностью
    # рабочей коробке — и уходила владельцу красной тревогой.
    if _ffmpeg_ok():
        lines.append("OK  FFmpeg: на месте.")
    else:
        problems.append(
            "Не найден FFmpeg — не соберётся видео: Reels и запасное слайд-шоу "
            "карусели. Саму карусель в ВК это НЕ ломает — она уходит листаемым "
            "фото-альбомом, без видео. Готовые ролики и посты на других "
            "площадках тоже не пострадают. "
            "Почините: pip install imageio-ffmpeg==0.6.0 (либо системный: %s)"
            % _install_hint("ffmpeg", "winget install Gyan.FFmpeg")
        )

    return problems, lines


def main():
    try:
        problems, lines = check()
    except Exception as ex:  # никаких стектрейсов клиенту
        print("Проверка модуля публикации не смогла отработать: %s" % ex)
        print("Это сбой самой проверки, а не площадок. Покажите строку выше человеку.")
        return 1

    print("=== Проверка публикации (модуль publishing) ===")
    for line in lines:
        print(" ", line)
    if problems:
        print()
        print("Есть что доделать:")
        for p in problems:
            print("  - " + p)
        print()
        print("Это не поломка: каждая площадка независима, заполните недостающее "
              "когда будет удобно.")
        return 2          # урезанный режим, а не отказ (CONTRACT.md §2)
    print()
    print("Всё заполнено — публикация возможна на все площадки.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

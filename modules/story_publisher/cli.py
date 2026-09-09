# -*- coding: utf-8 -*-
"""CLI сторис: собрать кадр 9:16 и прогнать выкладку по площадкам.

По умолчанию — ПРОБНЫЙ ПРОГОН (dry_run=True): печатает, ЧТО и КУДА ушло бы, но
НЕ трогает сеть. Реальная отправка — только с явным флагом --send (как у
autopost.py). Даже с --send без заданных токенов драйверы честно скажут
not_configured и ничего не отправят: живая выкладка не «вооружена» по конфигу.

Примеры:
    # только собрать кадр сторис
    python -m story_publisher --build --hook "Забирай гайд" \
        --text "Пиши слово в личку" --code ГАЙД --out out/story.png

    # собрать и прогнать выкладку по всем площадкам В ПРОБНОМ РЕЖИМЕ
    python -m story_publisher --build --hook "Забирай гайд" --text "..." \
        --code ГАЙД --to ig,tg,vk

    # прогнать выкладку готового медиа (пробный прогон)
    python -m story_publisher --media out/story.png --to ig

    # реальная выкладка (по образцу --send); без токенов не отправит ничего
    python -m story_publisher --media out/story.png --to ig --send
"""
from __future__ import annotations

import argparse
import sys

from .publisher import publish_story
from .drivers import known_platforms


def _parse_platforms(spec):
    if not spec:
        return ["ig", "tg", "vk"]
    return [p.strip() for p in spec.split(",") if p.strip()]


#: Кадры, которые вообще имеет смысл слать историей.
_FRAME_EXT = (".png", ".jpg", ".jpeg", ".mp4", ".mov")


def _natural_key(name):
    """Ключ ЧЕЛОВЕЧЕСКОЙ сортировки: «10.png» идёт ПОСЛЕ «2.png». Обычная
    сортировка строк дала бы 1,10,2 — и серия историй уехала бы не по порядку.
    То же соглашение, что у smmbox_post.album_from_dir."""
    import re as _re
    return [int(t) if t.isdigit() else t.lower()
            for t in _re.split(r"(\d+)", str(name))]


def _frames_from_dir(d):
    """Все кадры папки серией, отсортированные по-человечески."""
    import os as _os
    if not _os.path.isdir(d):
        raise SystemExit("Папки нет: %s" % d)
    names = [n for n in _os.listdir(d)
             if _os.path.splitext(n)[1].lower() in _FRAME_EXT]
    if not names:
        raise SystemExit("В папке %s нет ни одного кадра (%s)."
                         % (d, ", ".join(_FRAME_EXT)))
    return [_os.path.join(d, n) for n in sorted(names, key=_natural_key)]


def _gate_enforced(repo):
    """Клиентская ли это сборка (жёсткий режим активации).

    Дубль правила `activation_gate.enforced` НАРОЧНО — ровно как в
    render_reels.py: если самого гейта на машине нет, спросить его о режиме не у
    кого, а решать что-то надо. Правило одно и то же: штамп config/.client_build
    (ставит установщик), config/activation.enforce или env KZ_ENFORCE_ACTIVATION=1.
    """
    import os as _os
    if _os.environ.get("KZ_ENFORCE_ACTIVATION", "").strip().lower() in (
            "1", "true", "yes", "on"):
        return True
    return any(_os.path.exists(_os.path.join(repo, "config", mark))
               for mark in (".client_build", "activation.enforce"))


#: Статусы драйверов, при которых сторис ДЕЙСТВИТЕЛЬНО ушла на площадку.
#: Всё остальное («not_configured», «requires_manual», «dry_run», «error»,
#: «unconfirmed») — это «не отправлено», и называть это иначе в итоговой строке
#: нельзя. «unconfirmed» = площадка ответила, но подтверждения публикации не
#: дала: молчать об этом — врать, называть отказом — тоже.
_SENT_STATUSES = ("accepted",)
_UNCONFIRMED = "unconfirmed"


def _print_result(res):
    """Человекочитаемо показать НАМЕРЕНИЕ/итог по площадке (без секретов)."""
    plat = res.get("platform")
    status = res.get("status")
    mode = "ПРОБНЫЙ ПРОГОН" if res.get("dry_run") else "ОТПРАВКА"
    print("  [%s] %s → %s" % (plat, mode, status))
    if res.get("route"):
        print("      маршрут: %s" % res["route"])
    if res.get("endpoint"):
        print("      endpoint: %s" % res["endpoint"])
    if res.get("request"):
        print("      тело запроса (без секретов): %s" % res["request"])
    if res.get("flow"):
        print("      шаги: %s" % " → ".join(res["flow"]))
    if res.get("missing"):
        print("      не заполнено: %s" % ", ".join(res["missing"]))
    if res.get("reminder"):
        print("      памятка: %s" % res["reminder"])
    if res.get("detail"):
        print("      %s" % res["detail"])
    # Серия: показываем КАЖДЫЙ кадр. Сводной строки мало — по ней не понять,
    # на каком именно кадре встала выкладка.
    for frame in res.get("series") or []:
        print("      кадр %s: %s%s"
              % (frame.get("frame"), frame.get("status"),
                 " — " + frame["detail"] if frame.get("detail") else ""))


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="story_publisher",
        description="Сборка кадра сторис 9:16 и (по умолчанию — пробная) выкладка "
                    "по площадкам. Реальная отправка только с --send.")

    # источник медиа: собрать или готовый файл
    ap.add_argument("--build", action="store_true",
                    help="собрать кадр сторис движком (см. --hook/--text/--code)")
    ap.add_argument("--media", nargs="+", metavar="ФАЙЛ",
                    help="готовое медиа 9:16 — картинка ИЛИ вертикальное видео "
                         "(готовый reels.mp4 годится). Несколько файлов = серия "
                         "историй подряд, это и есть карусель в сторис")
    ap.add_argument("--media-dir", metavar="ПАПКА",
                    help="все кадры из папки серией, по-человечески 1,2,…,10")
    ap.add_argument("--from-frames", action="store_true",
                    help="переложить кадры на холст 9:16 перед выкладкой. Нужен "
                         "для слайдов карусели: они 1080x1350, а сторис 1080x1920 "
                         "— без перекладки площадка обрежет текст")

    # параметры сборки
    ap.add_argument("--hook", help="заголовок-хук для сборки")
    ap.add_argument("--text", default="", help="короткий текст под хуком")
    ap.add_argument("--code", default="", help="кодовое слово/CTA (плашка)")
    ap.add_argument("--accent", default="lime", help="имя акцента (по умолчанию lime)")
    ap.add_argument("--background", help="фон-материал (фото); без него — тёмный фон")
    ap.add_argument("--out", help="куда сохранить PNG (по умолчанию output/stories)")

    # выкладка
    ap.add_argument("--to", default="",
                    help="площадки через запятую: ig,tg,vk (по умолчанию все)")
    ap.add_argument("--caption", default="", help="подпись/текст к сторис")
    ap.add_argument("--link", default="", help="ссылка (для IG включает ручной режим)")
    ap.add_argument("--client", help="client_id (мультитенант); без него — по умолчанию")
    ap.add_argument("--send", action="store_true",
                    help="РЕАЛЬНАЯ выкладка. Без флага — пробный прогон, сеть не "
                         "трогается. Даже с флагом без токенов ничего не уйдёт.")

    args = ap.parse_args(argv)

    # Источник кадров: папка серией, перечисленные файлы или сборка ниже.
    media_path = None
    if args.media_dir:
        media_path = _frames_from_dir(args.media_dir)
    elif args.media:
        media_path = list(args.media)
    build_info = None

    # 1) СБОРКА (по требованию)
    if args.build:
        if not args.hook:
            ap.error("--build требует --hook")
        from .story_builder import build_story
        build_info = build_story(
            hook=args.hook, text=args.text, code=args.code or None,
            accent=args.accent, background=args.background, out=args.out)
        media_path = build_info["path"]
        print("Собран кадр сторис:")
        print("  файл   : %s" % build_info["path"])
        print("  размер : %d×%d" % (build_info["width"], build_info["height"]))
        print("  движок : %s" % build_info["engine"])
        print("  фон    : %s" % build_info["background"])
        print("  плашка : %s" % ("да" if build_info["code"] else "нет"))

    if not media_path:
        # нет ни готового медиа, ни сборки — только собрали (или ничего)
        if build_info is not None:
            return 0
        ap.error("нужен --media <файл...>, --media-dir <папка> или --build")

    # Перекладка на холст сторис. Слайд карусели — 1080x1350, история —
    # 1080x1920: без этого шага площадка обрежет кадр, а срежется в нём именно
    # текст. Видео не трогаем — движок рилсов и так отдаёт 1080x1920.
    if args.from_frames:
        from .story_builder import stories_from_frames
        import os as _os
        srcs = media_path if isinstance(media_path, list) else [media_path]
        vids = [p for p in srcs if _os.path.splitext(p)[1].lower() in (".mp4", ".mov")]
        pics = [p for p in srcs if p not in vids]
        if vids:
            print("--from-frames: видео перекладке не подлежит, беру как есть: %s"
                  % ", ".join(_os.path.basename(v) for v in vids))
        if pics:
            out_dir = _os.path.join(_os.path.dirname(pics[0]) or ".", "story_frames")
            laid = stories_from_frames(pics, out_dir)
            print("Переложено на холст 9:16: %d кадр(ов) -> %s"
                  % (len(laid), out_dir))
            for item in laid:
                print("  %s (%s)" % (_os.path.basename(item["path"]), item["mode"]))
            media_path = [i["path"] for i in laid] + vids

    # ОДИН кадр остаётся ОДНИМ путём, а не списком из одного: путь одиночной
    # сторис обязан пройти ровно так же, как до появления серий.
    if isinstance(media_path, list) and len(media_path) == 1:
        media_path = media_path[0]

    # 2) ВЫКЛАДКА (по умолчанию dry_run=True)
    dry_run = not args.send
    platforms = _parse_platforms(args.to)

    print("")
    print("Выкладка сторис (%s):"
          % ("ПРОБНЫЙ ПРОГОН — ничего не отправляется"
             if dry_run else "РЕАЛЬНАЯ ОТПРАВКА"))
    if isinstance(media_path, list):
        print("  серия     : %d историй подряд (карусель в сторис)" % len(media_path))
        for i, one in enumerate(media_path, 1):
            print("     %2d. %s" % (i, one))
    else:
        print("  медиа     : %s" % media_path)
    print("  площадки  : %s" % ", ".join(platforms))
    print("  известные : %s" % ", ".join(known_platforms()))
    print("")

    # Гейт активации (BOX-29): реальная выкладка только на активированной копии.
    if not dry_run:
        import os as _os
        _repo = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
        if _repo not in sys.path:
            sys.path.insert(0, _repo)
        try:
            import activation_gate
        except ImportError:
            # НЕ «pass». Отсутствие проверки — это не прохождение проверки:
            # раньше здесь молча публиковали дальше, и достаточно было сломать
            # импорт (переименовать файл, положить пустышку), чтобы гейта не
            # стало вовсе. Правило берём то же, что у render_reels.py:_gate_enforced
            # и activation_gate.enforced — спросить сам гейт нельзя, его нет.
            if _gate_enforced(_repo):
                print("[активация] ВЫКЛАДКА ОСТАНОВЛЕНА: модуля проверки "
                      "активации нет. Сборка повреждена — переустановите завод.",
                      file=sys.stderr)
                return 2
            print("[активация] гейт недоступен — выкладка не блокируется "
                  "[мягкий режим]", file=sys.stderr)
        else:
            if not activation_gate.allow("выкладку сторис"):
                return 2

    any_error = False
    sent, not_sent = [], []
    for plat in platforms:
        res = publish_story(plat, media_path, args.caption,
                            link=(args.link or None), client_id=args.client,
                            dry_run=dry_run)
        _print_result(res)
        if res.get("status") == "error":
            any_error = True
        if res.get("status") in _SENT_STATUSES:
            sent.append(res.get("platform") or plat)
        else:
            not_sent.append("%s (%s)" % (res.get("platform") or plat,
                                         res.get("status")))

    print("")
    if dry_run:
        print("[ПРОБНЫЙ ПРОГОН] Ничего не отправлено. Для реальной выкладки "
              "добавь --send (и заполни токены площадок).")
    else:
        # ЧЕСТНЫЙ ИТОГ РЕАЛЬНОГО ПРОГОНА. Раньше при `--send` без токенов
        # печатались построчные «not_configured», итоговой строки не было вовсе,
        # а код возврата оставался 0 — со стороны это выглядело как удачная
        # выкладка. Теперь вслух названо, куда ушло и куда нет.
        print("Ушло на площадки: %s" % (", ".join(sent) if sent else "НИКУДА"))
        if not_sent:
            print("НЕ подтверждено/не отправлено: %s. Эти сторис придётся "
                  "проверить глазами и при необходимости выложить руками "
                  "с телефона." % ", ".join(not_sent))
    return 1 if any_error else 0


if __name__ == "__main__":
    sys.exit(main())

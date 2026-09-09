# -*- coding: utf-8 -*-
"""
ЗАГРУЗЧИК СТОКОВЫХ ФОТО — под тёмный набор (engine_photo).

Зачем он вообще есть. Банк Валерии (photos/, 41 кадр) под тёмный набор не годится:
предметных там четыре (14, 19, 30, 41), остальное портреты. А тёмная карусель по
разбору референса держится на предметах и интерьерах БЕЗ людей — лицо появляется
всего дважды, на обложке и на CTA, и оба раза это автор. Восемь слайдов своим банком
не набрать, поэтому кадры под внутренние слайды берём со стока.

ИСТОЧНИК ПЕРЕКЛЮЧАЕТСЯ. Движку всё равно, откуда пришёл кадр: единство карусели
держит engine_photo.grade(), а не сток. Поэтому провайдер — параметр:
  pixabay — основной. Ключ сразу, без одобрения; коммерческое использование
            разрешено, атрибуция не требуется; лимит 100 запросов / 60 сек.
            Требует скачивать кадр к себе, а не хотлинкить — это ровно то,
            что мы и делаем (качаем → grade() → текст).
  pexels  — запасной, если её ключ заработает. ВАЖНО: ключ идёт в заголовке
            Authorization ГОЛЫМ, без слова Bearer — с "Bearer" будет 401.
Unsplash сюда не подключён сознательно: их API требует встраивать их же URL
картинки (хотлинк, чтобы считать просмотры фотографу). Мы кадр перекрашиваем и
вжигаем в него текст — так их условие выполнить физически нельзя.

ЛИЦА ОТСЕИВАЮТСЯ АВТОМАТОМ. --no-faces прогоняет каждый скачанный кадр через тот
же детектор, что и checks.check_cover_face (mediapipe 0.10.14), и выбрасывает
кадры с людьми. Ни у Pixabay, ни у Pexels фильтра «без людей» в API нет, а
глазами это по 80 превью — та самая работа, которую мы уже научились не делать.

КЛЮЧ — в D:\\karusel\\.env, строкой:
    PIXABAY_API_KEY=...
    PEXELS_API_KEY=...
В чат ключ не присылать, читаем отсюда.

ЗАПУСК:
    python stock.py "office desk minimal" -n 12 --no-faces
    python stock.py "concrete interior" -n 8 --provider pexels

Кладёт кадры в stock/, карту номеров в stock_idx.json, контрольный лист с
номерами — STOCK_SHEET.jpg (его и показывать при выборе, как ALL_PHOTOS.jpg).
"""
import argparse
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request

from PIL import Image, ImageDraw

import engine4 as e

HERE = os.path.dirname(os.path.abspath(__file__))
ENV = os.path.join(HERE, ".env")
DIR = os.path.join(HERE, "stock")
IDX = os.path.join(HERE, "stock_idx.json")
SHEET = os.path.join(HERE, "STOCK_SHEET.jpg")
CACHE = os.path.join(HERE, ".cache_stock")

# Pixabay требует кэшировать ответы 24 часа и не долбить автозапросами.
CACHE_TTL = 24 * 3600
UA = "karusel/1.0 (personal content tool)"


# ── КЛЮЧИ ────────────────────────────────────────────────────────────────────
def load_env(path=ENV):
    """.env как есть: KEY=VALUE построчно, # — комментарий. Без зависимостей."""
    if not os.path.exists(path):
        return {}
    out = {}
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def key_for(provider):
    env = load_env()
    name = {"pixabay": "PIXABAY_API_KEY", "pexels": "PEXELS_API_KEY"}[provider]
    k = env.get(name) or os.environ.get(name)
    if not k:
        sys.exit(
            f"НЕТ КЛЮЧА: {name}.\n"
            f"Положи его в {ENV} строкой:\n    {name}=твой_ключ\n"
            f"Pixabay: ключ виден прямо в https://pixabay.com/api/docs/ , "
            f"когда ты залогинена — одобрения ждать не надо."
        )
    return k


# ── СЕТЬ ─────────────────────────────────────────────────────────────────────
def _get(url, headers=None):
    """GET с кэшем на диск: Pixabay просит кэшировать ответы на 24ч."""
    os.makedirs(CACHE, exist_ok=True)
    tag = hashlib.sha1(url.encode()).hexdigest()[:16]
    hit = os.path.join(CACHE, tag + ".json")
    if os.path.exists(hit) and time.time() - os.path.getmtime(hit) < CACHE_TTL:
        with open(hit, encoding="utf-8") as f:
            return json.load(f)

    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as ex:
        hint = ""
        if ex.code == 401:
            hint = ("\nПодсказка: у Pexels ключ идёт в Authorization ГОЛЫМ, "
                    "без слова Bearer. У Pixabay ключ — параметр ?key=, не заголовок.")
        elif ex.code == 429:
            hint = "\nПодсказка: упёрлись в лимит запросов. Подожди минуту."
        elif 500 <= ex.code < 600:
            hint = "\nПодсказка: это их сервер, не мы. Обычно временно — повтори позже."
        sys.exit(f"СТОК ОТВЕТИЛ {ex.code} {ex.reason}{hint}")
    except urllib.error.URLError as ex:
        sys.exit(f"СЕТЬ НЕДОСТУПНА: {ex.reason}")

    with open(hit, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return data


# ── ПРОВАЙДЕРЫ → единый вид {id, url, w, h} ─────────────────────────────────
def search_pixabay(query, n):
    url = "https://pixabay.com/api/?" + urllib.parse.urlencode({
        "key": key_for("pixabay"),
        "q": query,
        "image_type": "photo",
        "orientation": "vertical",   # нам нужен 4:5, вертикаль режется меньше
        "per_page": max(3, min(n * 3, 200)),  # берём с запасом: часть отсеют лица
        "safesearch": "true",
        "order": "popular",
    })
    hits = _get(url).get("hits", [])
    return [{"id": f"pixabay_{h['id']}",
             "url": h.get("largeImageURL") or h["webformatURL"],
             "w": h.get("imageWidth", 0), "h": h.get("imageHeight", 0)}
            for h in hits]


def search_pexels(query, n):
    url = "https://api.pexels.com/v1/search?" + urllib.parse.urlencode({
        "query": query, "per_page": max(3, min(n * 3, 80)), "orientation": "portrait",
    })
    # ГОЛЫЙ ключ. "Bearer " здесь = 401.
    photos = _get(url, {"Authorization": key_for("pexels")}).get("photos", [])
    return [{"id": f"pexels_{p['id']}",
             "url": p["src"]["large2x"],
             "w": p.get("width", 0), "h": p.get("height", 0)}
            for p in photos]


PROVIDERS = {"pixabay": search_pixabay, "pexels": search_pexels}


# ── ЛИЦА ─────────────────────────────────────────────────────────────────────
def has_face(path):
    """Тот же детектор и тот же fit(), что у checks.check_cover_face.

    checks.face_boxes ждёт ключ из e.PHOTOS, а не путь. e.PHOTOS — обычный
    словарь, поэтому кадр заводится туда временным ключом: так мы меряем ровно
    тем же кодом, что и проверка перед показом, и не заводим второй детектор,
    который завтра разъедется с первым.
    """
    import checks
    tmp = "_stock_probe"
    e.PHOTOS[tmp] = path
    try:
        return bool(checks.face_boxes(tmp))
    finally:
        e.PHOTOS.pop(tmp, None)


# ── СКАЧИВАНИЕ ───────────────────────────────────────────────────────────────
def download(item, dest_dir):
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, item["id"] + ".jpg")
    if os.path.exists(path):
        return path
    req = urllib.request.Request(item["url"], headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r, open(path, "wb") as f:
        f.write(r.read())
    return path


def fetch(query, n=8, provider="pixabay", no_faces=False):
    found = PROVIDERS[provider](query, n)
    if not found:
        sys.exit(f"НИЧЕГО НЕ НАЙДЕНО по запросу «{query}» на {provider}.")

    kept, skipped = [], 0
    for it in found:
        if len(kept) >= n:
            break
        try:
            p = download(it, DIR)
        except Exception as ex:
            print(f"  ! не скачался {it['id']}: {ex}")
            continue
        if no_faces and has_face(p):
            os.remove(p)
            skipped += 1
            continue
        kept.append(p)
        print(f"  + {os.path.basename(p)}")

    if no_faces:
        print(f"\nОтсеяно кадров с людьми: {skipped}")
    if len(kept) < n:
        print(f"ВНИМАНИЕ: просили {n}, набралось {len(kept)}. "
              f"Уточни запрос или подними -n.")
    return kept


# ── КОНТРОЛЬНЫЙ ЛИСТ ─────────────────────────────────────────────────────────
def build_sheet(paths, out=SHEET, cols=4, cell=270, graded=False):
    """Лист с номерами — то же назначение, что у ALL_PHOTOS.jpg.

    graded=True показывает кадры уже после engine_photo.grade(): выбирать честнее
    по тому, как кадр будет выглядеть в карусели, а не как он лежит на стоке.
    """
    if not paths:
        return None
    rows = (len(paths) + cols - 1) // cols
    pad, lab = 12, 46
    sheet = Image.new("RGB", (cols * (cell + pad) + pad,
                              rows * (cell + pad + lab) + pad), (245, 244, 242))
    d = ImageDraw.Draw(sheet)
    fnt = e.font("Black", 26)      # номер — по нему называют кадр
    fnt_s = e.font("Regular", 13)  # имя файла — служебное, мелким

    for i, p in enumerate(paths):
        im = Image.open(p).convert("RGB")
        if graded:
            import engine_photo as ph
            im = ph.grade(im)
        im.thumbnail((cell, cell), Image.LANCZOS)
        x = pad + (i % cols) * (cell + pad)
        y = pad + (i // cols) * (cell + pad + lab)
        sheet.paste(im, (x + (cell - im.width) // 2, y + (cell - im.height) // 2))
        # Номер крупно, имя файла мелко и обрезано по ширине ячейки: имена со
        # стока и в банке — длинные хеши, целиком они наезжают на соседа.
        d.text((x + 4, y + cell + 4), str(i + 1), font=fnt, fill=e.INK)
        name = os.path.basename(p)
        while name and d.textlength(name, font=fnt_s) > cell - 8:
            name = name[:-1]
        d.text((x + 4, y + cell + 32), name, font=fnt_s, fill=(120, 116, 112))

    sheet.save(out, quality=92)
    return out


def save_idx(paths, out=IDX):
    """Карта «номер с контрольного листа → имя файла» в stock_idx.json.

    ПОЧЕМУ ЭТО НЕ МЁРТВАЯ ЗАПИСЬ, ХОТЯ ЧИТАТЕЛЕЙ В КОДЕ НОЛЬ (проверено
    grep'ом по всему репозиторию). Индекс — не кэш движка, а ПОЛОВИНА ПЕРЕДАЧИ
    работы человеку, и вторая половина рядом: STOCK_SHEET.jpg — чтобы выбрать
    кадр глазами по номеру, stock_idx.json — чтобы этот номер превратить в
    путь. Ровно та же пара, что ALL_PHOTOS.jpg + idx.json у своего банка, и
    читает её тоже не код, а строка, которую автор пишет руками в сборочном
    скрипте (main() печатает её готовой). У idx.json таких рукописных читателей
    99 сборок, у stock_idx.json пока ноль — но это значит «стоковый кадр ещё ни
    разу не попал в опубликованную карусель», а не «формат никому не нужен».

    В отличие от vidx.json и broll_index.json, которые читает КОД (video_sheet,
    talk_broll), здесь потребитель по замыслу человек. Убрать запись — значит
    сломать подсказку в main() и оставить контрольный лист с номерами, которые
    не во что развернуть. Поэтому оставлено; см. README.md, раздел индексов.
    """
    idx = {str(i + 1): os.path.basename(p) for i, p in enumerate(paths)}
    with open(out, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=1)
    return idx


# ── CLI ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="Загрузчик стоковых фото под тёмный набор")
    ap.add_argument("query", help="поисковый запрос (лучше по-английски)")
    ap.add_argument("-n", type=int, default=8, help="сколько кадров нужно (по умолчанию 8)")
    ap.add_argument("--provider", default="pixabay", choices=list(PROVIDERS))
    ap.add_argument("--no-faces", action="store_true",
                    help="выбросить кадры с людьми (для внутренних слайдов)")
    ap.add_argument("--graded", action="store_true",
                    help="показать лист уже после grade()")
    a = ap.parse_args()

    print(f"Ищу «{a.query}» на {a.provider}, нужно {a.n}...")
    paths = fetch(a.query, a.n, a.provider, a.no_faces)
    if not paths:
        sys.exit("Не набралось ни одного кадра.")
    save_idx(paths)
    build_sheet(paths, graded=a.graded)
    print(f"\nГотово: {len(paths)} кадров в stock\\, карта — stock_idx.json")
    print(f"Контрольный лист: {SHEET}")
    print("\nПодключение в скрипте сборки:")
    print("    import json, engine4 as e, os")
    print("    sidx = json.load(open('stock_idx.json'))")
    print("    e.PHOTOS['s1'] = os.path.join('stock', sidx['1'])")


if __name__ == "__main__":
    main()

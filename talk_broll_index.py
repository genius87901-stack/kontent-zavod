# -*- coding: utf-8 -*-
"""E4-10: семантический индекс банка video/ (CLIP) + локальный источник b-roll.

Даёт движку №4 брать перебивки из СВОИХ роликов (банк video/), а не только со
стоков. Как встроено:
  • build_index() один раз проходит банк и пишет broll_index.json — на каждый
    клип CLIP-эмбеддинг его СРЕДНЕГО кадра (мидфрейма) плюс метаданные (длина,
    размер). Мидфрейм, а не обложка: тот же урок, что у стоков (clip_midframe в
    talk_broll) — интро/аутро не репрезентативны, честнее середина клипа.
  • LocalBankSource.search(query, dur) эмбеддит текст визуального запроса и
    отдаёт ближайшие клипы по косинусу. Это СЕМАНТИКА, а не строковый матч по
    имени файла: запрос «переговоры в офисе» находит офисный клип, даже если в
    имени ничего нет. Класс регистрируется в talk_broll.SOURCES['local'].

Почему отдельный модуль, а не внутри talk_broll.py: torch/open_clip тяжёлые и
стоковому пути не нужны. talk_broll грузит это ленивицей только когда реально
зовут локальный источник (см. фабрику _local_source там).

CLIP: open_clip ViT-B-32 (laion2b) — лёгкая, идёт на CPU. Текстовые запросы у
нас по-английски (visual_query в blocks.json talk_segment формулирует по-англ.),
под них и считаем текстовый эмбеддинг.

broll_index.json лёгкий (эмбеддинги float, округлены) — идёт в git, как vidx.json.
Веса модели open_clip кэшируются вне репо (HF cache), в git НЕ попадают.
"""
import json
import os

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
VIDEO = os.path.join(HERE, "video")
INDEX = os.path.join(HERE, "broll_index.json")

MODEL_NAME = "ViT-B-32"
PRETRAINED = "laion2b_s34b_b79k"
# .HEIC в банке — это фото, попавшие случайно (см. HANDOFF), их не индексируем.
VIDEO_EXT = (".mov", ".mp4", ".m4v", ".avi", ".mkv", ".webm")

_model = None
_preprocess = None
_tokenizer = None


def _load():
    """Ленивая загрузка CLIP — только когда реально нужен эмбеддинг."""
    global _model, _preprocess, _tokenizer
    if _model is not None:
        return
    import torch  # noqa: F401  (нужен open_clip'у и для no_grad ниже)
    import open_clip
    _model, _, _preprocess = open_clip.create_model_and_transforms(
        MODEL_NAME, pretrained=PRETRAINED)
    _model.eval()
    _tokenizer = open_clip.get_tokenizer(MODEL_NAME)


def _dims(path):
    """(ширина, высота) клипа С УЧЁТОМ ФЛАГА ПОВОРОТА (задача 1: качество монтажа).

    cv2 флаг rotation ИГНОРИРУЕТ и для вертикальных .MOV с телефона (хранятся как
    1920×1080 + rotation=-90) отдаёт РАЗВЁРНУТЫЕ 1920×1080 — тогда порог разрешения
    перебивки судил бы «пейзаж» вместо реального портрета. Поэтому размеры берём
    ffprobe'ом (как в engine_talk._video_rotation): он же отдаёт side_data rotation,
    и при повороте ±90 меняем W/H местами. ffprobe надёжнее, но на Windows-машине
    клиента может не стоять → запасной путь разбором вывода ffmpeg -i (есть у всех
    через imageio_ffmpeg), крайний — cv2 (без коррекции, лучше чем ничего)."""
    import re
    import shutil
    import subprocess
    w = h = 0
    rot = 0
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        try:
            out = subprocess.run(
                [ffprobe, "-v", "0", "-select_streams", "v:0", "-show_entries",
                 "stream=width,height:stream_side_data=rotation",
                 "-of", "default=nw=1", path],
                capture_output=True, text=True, timeout=30).stdout
            for line in out.splitlines():
                k, _, val = line.partition("=")
                if k == "width":
                    w = int(val or 0)
                elif k == "height":
                    h = int(val or 0)
                elif k == "rotation":
                    rot = int(round(float(val or 0)))
        except Exception:
            w = h = 0
    if not (w and h):                       # запасной путь: ffmpeg -i (imageio)
        try:
            import imageio_ffmpeg
            ff = imageio_ffmpeg.get_ffmpeg_exe()
            err = subprocess.run([ff, "-i", path], capture_output=True, text=True,
                                 timeout=30).stderr or ""
            m = re.search(r",\s(\d{2,5})x(\d{2,5})", err)
            if m:
                w, h = int(m.group(1)), int(m.group(2))
            mr = (re.search(r"rotation of ([\-\d.]+) degrees", err)
                  or re.search(r"rotate\s*:\s*(-?\d+)", err))
            if mr:
                rot = int(round(float(mr.group(1))))
        except Exception:
            pass
    if abs(rot) % 180 == 90:                 # вертикальный кадр хранится «на боку»
        w, h = h, w
    return w, h


def _probe(path):
    """(мидфрейм BGR | None, w, h, duration). Размеры — С КОРРЕКЦИЕЙ ПОВОРОТА через
    _dims (ffprobe); мидфрейм и длительность — cv2. При сбое _dims (w или h == 0)
    падаем на cv2-размеры, чтобы индекс всё равно собрался."""
    c = cv2.VideoCapture(path)
    n = c.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    cw = int(c.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    ch = int(c.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    fps = c.get(cv2.CAP_PROP_FPS) or 0
    dur = (n / fps) if fps else 0.0
    c.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(n // 2)))
    ok, fr = c.read()
    c.release()
    w, h = _dims(path)
    if not (w and h):
        w, h = cw, ch
    return (fr if ok else None), w, h, round(float(dur), 2)


def embed_image(bgr):
    """CLIP-эмбеддинг кадра (нормированный вектор np.float32)."""
    _load()
    import torch
    from PIL import Image
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    im = _preprocess(Image.fromarray(rgb)).unsqueeze(0)
    with torch.no_grad():
        v = _model.encode_image(im)
        v = v / v.norm(dim=-1, keepdim=True)
    return v[0].cpu().numpy().astype("float32")


def embed_text(query):
    """CLIP-эмбеддинг текстового запроса (нормированный вектор np.float32)."""
    _load()
    import torch
    tok = _tokenizer([query])
    with torch.no_grad():
        v = _model.encode_text(tok)
        v = v / v.norm(dim=-1, keepdim=True)
    return v[0].cpu().numpy().astype("float32")


def build_index(video_dir=VIDEO, out=INDEX):
    """Пройти банк, посчитать эмбеддинг мидфрейма каждого клипа → broll_index.json.
    Пропускает не-видео (в т.ч. .HEIC) и нечитаемые файлы. Идемпотентно:
    полностью перезаписывает индекс тем, что реально лежит в video/."""
    vp = os.path.join(HERE, "vidx.json")
    num_by_name = ({v: k for k, v in json.load(open(vp, encoding="utf-8")).items()}
                   if os.path.exists(vp) else {})
    files = sorted(f for f in os.listdir(video_dir)
                   if f.lower().endswith(VIDEO_EXT))
    items = []
    for i, fn in enumerate(files, 1):
        fr, w, h, dur = _probe(os.path.join(video_dir, fn))
        if fr is None:
            print("  ! пропуск (не читается): %s" % fn)
            continue
        emb = embed_image(fr)
        items.append({
            "name": fn,
            "vidx": num_by_name.get(fn),
            # T1: путь ВСЕГДА posix-слэшем (не os.path.join) — индекс собирают
            # то на Windows, то на Mac (кросс-платформенный завод), а
            # os.path.join на Windows писал "video\\ИМЯ.MOV". LocalBankSource
            # на Mac открывал этот бэкслеш как ОДНО имя файла — "нет такого
            # файла" везде по банку. path.join потребителя (ниже) читает то же
            # поле как posix, поэтому формат фиксируем тут раз и навсегда.
            "path": "video/" + fn,
            "w": w, "h": h, "duration": dur,
            "emb": [round(float(x), 5) for x in emb],
        })
        print("  [%2d/%2d] %-24s %5.1fс %dx%d" % (i, len(files), fn, dur, w, h))
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"model": "%s/%s" % (MODEL_NAME, PRETRAINED),
                   "dim": len(items[0]["emb"]) if items else 0,
                   "clips": items}, f, ensure_ascii=False)
    print("OK -> %s (%d клипов)" % (out, len(items)))
    return out


class LocalBankSource:
    """Свой банк video/ как источник b-roll — семантикой по broll_index.json.

    Контракт talk_broll.BrollSource: search(query, dur) -> [candidate]. Кандидат
    в том же формате, что у стоков, плюс:
      • source="local", path=локальный файл (url=None — download_clip вернёт path);
      • score=косинус к запросу (для приоритета лучшего своего клипа).
    Отдаёт только выше порога similarity, чтобы слабый локальный матч не
    вытеснял хороший сток (доля/приоритет решаются выше, в planner)."""
    name = "local"

    def __init__(self, index=INDEX, threshold=0.20):
        self.index = index
        self.threshold = threshold
        self._clips = None
        self._mat = None

    def _load_index(self):
        if self._clips is None:
            if not os.path.exists(self.index):
                raise SystemExit(
                    "нет %s — сначала построй индекс: "
                    "python talk_broll_index.py --build" % os.path.basename(self.index))
            data = json.load(open(self.index, encoding="utf-8"))
            self._clips = data.get("clips", [])
            self._mat = (np.array([c["emb"] for c in self._clips], dtype="float32")
                         if self._clips else None)
        return self._clips

    def search(self, query, dur, n=30):
        clips = self._load_index()
        if not clips:
            return []
        q = embed_text(query)
        sims = self._mat @ q               # оба нормированы → косинусная близость
        out = []
        for idx in np.argsort(-sims)[:n]:
            s = float(sims[idx])
            if s < self.threshold:
                break
            c = clips[idx]
            # T1: broll_index.json бывает собран на Windows (path типа
            # "video\\ИМЯ.MOV") — на Mac os.path.join трактует бэкслеш как
            # часть имени файла, не разделитель, и клип "не находится"
            # (падало в _sha256 при записи лока и в реальном рендере).
            # Нормализуем разделитель ДО join независимо от того, кто и на
            # чём индекс строил — фикс живёт тут, а не в одной сборке.
            path = os.path.join(HERE, *c["path"].replace("\\", "/").split("/"))
            # T1: индекс банка video/ может отстать от реальных файлов на диске
            # (материал переносят/удаляют вне цикла build_index) — на реальном
            # прогоне 7 из 87 записей индекса указывали на удалённые клипы
            # (IMG_3374.MOV, IMG_3229.MOV и др.). Раньше такой кандидат доходил
            # до pick() и падал ГЛУБОКО в конвейере (FileNotFoundError в
            # _sha256 или в самом рендере, после того как речь уже озвучена
            # планировщиком) — весь ролик срывался вместо тихого fallback на
            # следующего кандидата. Проверяем на диске здесь, у источника.
            if not os.path.exists(path):
                continue
            out.append({
                "source": "local",
                "id": "local_%s" % os.path.splitext(c["name"])[0],
                "url": None,
                "path": path,
                "w": c["w"], "h": c["h"],
                "duration": c["duration"],
                "tags": c["name"],
                "score": round(s, 4),
            })
        return out


def main():
    import sys
    args = sys.argv[1:]
    if not args or "--build" in args:
        build_index()
    else:
        q = args[0]
        src = LocalBankSource()
        res = src.search(q, dur=5.0)
        print("по «%s» — %d локальных клипов над порогом:" % (q, len(res)))
        for c in res[:10]:
            print("  %.3f  %-24s %5.1fс %dx%d"
                  % (c["score"], c["tags"], c["duration"], c["w"], c["h"]))


if __name__ == "__main__":
    raise SystemExit(main())

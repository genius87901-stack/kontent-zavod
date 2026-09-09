# -*- coding: utf-8 -*-
"""E4-09: матирование спикера (вырез по силуэту) для шаблона Cutout движка №4.

Даёт альфа-маску спикера по кадру, чтобы положить его ПОВЕРХ сменяющегося b-roll
(референс Пример1: девушка вырезана, за ней меняется travel-фон). Спикер всегда
на экране, фон под ним — другое видео.

БЭКЕНДЫ (выбор — параметр backend):
  • 'mediapipe' (по умолчанию) — Selfie Segmentation, УЖЕ стоит (mediapipe
    0.10.14), НИЧЕГО не качает, идёт на CPU в реальном времени. Край грубее —
    поэтому чистим: порог → морфология (убрать дыры/крошку) → перо (мягкий край,
    иначе силуэт «вырезан ножницами»).
  • 'modnet' (R17) — MODNet portrait matting (github.com/ZHKKKe/MODNet,
    Apache-2.0 — проверено по файлу LICENSE репозитория) через onnxruntime
    (MIT) на CPU. Маска считается в ~512×896 против 144×256 у mediapipe —
    край заметно тоньше. Веса кладутся в коробку ЗАРАНЕЕ (models/modnet/
    рядом с этим файлом); в рантайме НИЧЕГО не скачивается — если файла нет,
    падаем честной ошибкой с точным путём. Сырая вероятность обоих бэкендов
    идёт через ОДИН и тот же пост-процесс (EMA → порог → морфология → guard
    → перо), так что сравнение бэкендов изолирует именно модель.

RVM-ветки здесь БОЛЬШЕ НЕТ (R5): RobustVideoMatting — GPL-3.0 (лицензия сменена
16.09.2021) + веса тянулись torch.hub'ом из сети в рантайме. Для клиентской
коробки (офлайн, только MIT/BSD/Apache/OFL) это двойное нарушение — удалено.

Контракт: Matter(backend).alpha(frame_bgr) -> np.float32 HxW в [0,1], где 1 —
спикер, 0 — фон. Размер маски = размер кадра.

ПРЕКОМПЬЮТ АЛЬФЫ (R18): модель можно прогнать один раз отдельной стадией и
читать в рендере из кэша (развязка «скорость модели ↔ скорость рендера»;
для modnet на CPU это обязательный путь — живой счёт ~0.5 c/кадр):

    python talk_matte.py --precompute VIDEO [--backend X] [--out CACHE.npz]
    Matter(cache="CACHE.npz").alpha(frame)   # читает кадры по порядку

Формат кэша: zip (npz-совместимый), внутри по кадру uint8-массив f000000…
(альфа ×255, deflate) + meta [n, h, w]. Пишется ИНКРЕМЕНТНО (кадр за кадром,
без удержания всего ролика в RAM), читается лениво (np.load декодирует только
запрошенный кадр) — random access без загрузки всего файла.
"""
import os

import cv2
import numpy as np

HERE = __file__
# Веса MODNet — относительный путь от ЭТОГО файла (переезжает вместе с коробкой)
MODNET_ONNX = os.path.join(os.path.dirname(os.path.abspath(HERE)),
                           "models", "modnet",
                           "modnet_photographic_portrait_matting.onnx")
MODNET_REF = 512    # опорная сторона ресайза (официальный inference MODNet)


class Matter:
    """Матировщик спикера. Один экземпляр на ролик (держит модель/состояние).

    cache=путь_к_npz — читать заранее посчитанную альфу (см. --precompute)
    вместо живого счёта моделью; кадры запрашиваются в том же порядке, что
    в видео (внутренний счётчик). backend при этом игнорируется."""

    def __init__(self, backend="mediapipe", feather=7, thr=0.5,
                 ema=0.7, soft=0.0, guard=True, cc_largest=False, cache=None):
        self.backend = backend
        self.feather = feather      # радиус пера края (px, 0 — резкий край)
        self.thr = thr              # центр порога бинаризации маски сегментации
        # EMA по альфе-вероятности: a_t = ema·m_t + (1-ema)·a_{t-1}. Убирает
        # «кипение» края mediapipe между кадрами (temporal-стабилизация). ema<1 —
        # сильнее сглаживание; 1.0 — прежнее поведение (без EMA).
        self.ema = ema
        # мягкий порог: полуширина smoothstep-рампы вокруг thr (в единицах
        # вероятности). 0 — жёсткий порог (прежнее поведение).
        self.soft = soft
        # guard: при опустошении/заливе всей маски откатываемся на последнюю
        # валидную маску (fallback), а не отдаём пустой/полный кадр. Безопасно:
        # на нормальных кадрах — no-op.
        self.guard = guard
        # cc_largest: держать ТОЛЬКО крупнейшую связную компоненту. Убирает
        # «залив» фоном (выигрыш на 4525), но «роняет» легитимные фрагменты
        # силуэта (кисть/частичное тело) → скачок центроида на 5151/5356.
        # По умолчанию OFF (замером ухудшал maxjump/iou — см. журнал Шаг 2).
        self.cc_largest = cc_largest
        self._ema_prev = None       # буфер сглаженной вероятности
        self._last_mask = None      # последняя валидная маска (uint8, до пера)
        self._area_ref = None       # опорная типичная доля площади (для flood)
        self._seg = None
        self._cache = None
        if cache is not None:
            self._init_cache(cache)
            return                       # модель не поднимаем — читаем из кэша
        if backend == "mediapipe":
            import mediapipe as mp
            self._seg = mp.solutions.selfie_segmentation.SelfieSegmentation(
                model_selection=1)   # 1 — landscape/general, точнее на полурост
        elif backend == "modnet":
            self._init_modnet()
        else:
            raise ValueError("backend: 'mediapipe' | 'modnet'")

    def _init_modnet(self):
        # Никаких скачиваний в рантайме: весов нет — честная ошибка с путём.
        if not os.path.exists(MODNET_ONNX):
            raise SystemExit(
                "MODNet-весов нет: положи файл %s\n"
                "(modnet_photographic_portrait_matting.onnx, ~25 МБ, Apache-2.0, "
                "официальный ONNX-экспорт из github.com/ZHKKKe/MODNet). "
                "Скачивание в рантайме запрещено правилами коробки." % MODNET_ONNX)
        import onnxruntime as ort
        self._modnet = ort.InferenceSession(
            MODNET_ONNX, providers=["CPUExecutionProvider"])
        self._modnet_in = self._modnet.get_inputs()[0].name

    def _init_cache(self, path):
        if not os.path.exists(path):
            raise SystemExit(
                "Кэша альфы нет: %s. Сначала посчитай его: "
                "python talk_matte.py --precompute VIDEO [--backend X] "
                "[--out %s]" % (path, path))
        self._cache = np.load(path)      # ленивый zip: кадр читается по запросу
        meta = self._cache["meta"]       # int64 [n, h, w]
        self._cache_n = int(meta[0])
        self._cache_hw = (int(meta[1]), int(meta[2]))
        self._cache_i = 0

    # ── альфа по кадру ───────────────────────────────────────────────────────
    def alpha(self, frame_bgr):
        if self._cache is not None:
            return self._alpha_cache(frame_bgr)
        if self.backend == "modnet":
            return self._alpha_modnet(frame_bgr)
        return self._alpha_mp(frame_bgr)

    def _alpha_cache(self, frame_bgr):
        if self._cache_i >= self._cache_n:
            raise SystemExit(
                "Кэш альфы короче видео: запрошен кадр %d, в кэше %d. "
                "Пересчитай кэш на ЭТОТ ролик (--precompute)."
                % (self._cache_i, self._cache_n))
        a8 = self._cache["f%06d" % self._cache_i]
        self._cache_i += 1
        a = a8.astype(np.float32) / 255.0
        h, w = frame_bgr.shape[:2]
        if a.shape != (h, w):            # кадр другого размера — тянем маску
            a = cv2.resize(a, (w, h), interpolation=cv2.INTER_LINEAR)
        return a

    def _alpha_mp(self, frame_bgr):
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        m = self._seg.process(rgb).segmentation_mask     # float HxW в [0,1]
        h, w = frame_bgr.shape[:2]
        if m is None:
            # нет маски — держим последнюю сглаженную вероятность (не пустой кадр)
            m = (self._ema_prev if self._ema_prev is not None
                 else np.zeros((h, w), np.float32))
        m = np.clip(m, 0, 1).astype(np.float32)
        return self._post(m, h, w)

    def _alpha_modnet(self, frame_bgr):
        h, w = frame_bgr.shape[:2]
        # ресайз как в официальном onnx/inference_onnx.py: меньшая сторона →
        # MODNET_REF, обе стороны кратны 32 (иначе сеть падает на даунсемплах)
        if max(h, w) < MODNET_REF or min(h, w) > MODNET_REF:
            if w >= h:
                rh, rw = MODNET_REF, int(w / h * MODNET_REF)
            else:
                rw, rh = MODNET_REF, int(h / w * MODNET_REF)
        else:
            rh, rw = h, w
        rw -= rw % 32
        rh -= rh % 32
        im = cv2.resize(frame_bgr, (rw, rh), interpolation=cv2.INTER_AREA)
        im = cv2.cvtColor(im, cv2.COLOR_BGR2RGB).astype(np.float32)
        im = (im / 255.0 - 0.5) / 0.5                     # нормировка MODNet
        x = im.transpose(2, 0, 1)[None]                   # NCHW
        pha = self._modnet.run(None, {self._modnet_in: x})[0][0, 0]
        m = cv2.resize(pha, (w, h), interpolation=cv2.INTER_LINEAR)
        m = np.clip(m, 0, 1).astype(np.float32)
        return self._post(m, h, w)

    def _post(self, m, h, w):
        """Общий пост-процесс сырой вероятности (оба бэкенда): EMA → порог →
        морфология → guard → перо. Для mediapipe — байт-в-байт прежняя цепочка."""
        # temporal-EMA по вероятности: гасит покадровое дрожание края.
        if (self.ema < 1.0 and self._ema_prev is not None
                and self._ema_prev.shape == m.shape):
            m = self.ema * m + (1.0 - self.ema) * self._ema_prev
        self._ema_prev = m
        # порог: жёсткий (soft=0) или мягкий smoothstep-рампой вокруг thr
        if self.soft > 0:
            lo, hi = self.thr - self.soft, self.thr + self.soft
            a = np.clip((m - lo) / max(1e-6, hi - lo), 0.0, 1.0)
            a = a * a * (3.0 - 2.0 * a)                   # smoothstep
            hard = (a * 255.0).astype(np.uint8)
        else:
            hard = (m > self.thr).astype(np.uint8) * 255
        # убрать крошку фона и дырки в силуэте
        hard = cv2.morphologyEx(hard, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        hard = cv2.morphologyEx(hard, cv2.MORPH_CLOSE, np.ones((11, 11), np.uint8))
        if self.guard:
            hard = self._guard_mask(hard, h, w)
        if self.feather > 0:                              # мягкий край
            k = self.feather * 2 + 1
            hard = cv2.GaussianBlur(hard, (k, k), 0)
        return (hard.astype(np.float32) / 255.0)

    def _guard_mask(self, hard, h, w):
        """Стабилизирующий guard над бинарной/мягкой маской (uint8, 0..255):
        fallback на последнюю валидную при опустошении/заливе всего кадра.
        Опционально (cc_largest, по умолчанию OFF) — держать только крупнейшую
        связную компоненту (см. журнал: ухудшал maxjump/iou → выключено)."""
        bw = (hard > 127)
        if self.cc_largest:
            n, lbl, stats, _ = cv2.connectedComponentsWithStats(
                bw.astype(np.uint8), connectivity=8)
            if n > 1:
                # компонента 0 — фон; берём крупнейшую из остальных по площади
                big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
                keep = (lbl == big)
                hard = hard * keep.astype(np.uint8)       # сохраняет soft-градиент
                area = float(keep.mean())
            else:
                area = 0.0
        else:
            area = float(bw.mean())
        # guard опустошения / залива всего кадра → откат на последнюю валидную
        empty = area < 0.01
        flood = area > 0.92
        if (empty or flood) and self._last_mask is not None:
            return self._last_mask.copy()
        self._last_mask = hard.copy()
        # опорная площадь (медленное скользящее) — телеметрия для будущих guard'ов
        self._area_ref = area if self._area_ref is None else (
            0.9 * self._area_ref + 0.1 * area)
        return hard

def precompute(video, backend="mediapipe", out=None, **matter_kw):
    """R18: посчитать альфу ВСЕХ кадров ролика и сжать в npz-совместимый кэш.

    Пишем инкрементно (zip deflate, по кадру uint8 ×255) — RAM не растёт с
    длиной ролика. Возвращает путь кэша."""
    import io
    import time
    import zipfile
    from numpy.lib import format as npfmt

    out = out or (os.path.splitext(video)[0] + ".alpha.npz")
    matter = Matter(backend=backend, **matter_kw)
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise SystemExit("не открылось видео: %s" % video)
    t0 = time.time()
    n, h, w = 0, 0, 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        while True:
            ok, fr = cap.read()
            if not ok:
                break
            a8 = (matter.alpha(fr) * 255.0 + 0.5).astype(np.uint8)
            h, w = a8.shape
            buf = io.BytesIO()
            npfmt.write_array(buf, a8)
            zf.writestr("f%06d.npy" % n, buf.getvalue())
            n += 1
            if n % 100 == 0:
                print("  кадр %d (%.1f c)" % (n, time.time() - t0), flush=True)
        buf = io.BytesIO()
        npfmt.write_array(buf, np.array([n, h, w], np.int64))
        zf.writestr("meta.npy", buf.getvalue())
    cap.release()
    dt = time.time() - t0
    print("OK -> %s (%d кадров %dx%d, %.1f МБ, %.1f c, %.2f кадр/c)"
          % (out, n, w, h, os.path.getsize(out) / 1e6, dt, n / max(dt, 1e-9)),
          flush=True)
    return out


def cutout_rgba(frame_bgr, matter):
    """Кадр спикера как RGBA (H×W×4, uint8): RGB кадра + альфа матировщика.
    Готово для наложения на фон-b-roll (Image.paste с альфой)."""
    a = matter.alpha(frame_bgr)
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    return np.dstack([rgb, (a * 255).astype(np.uint8)])


def main():
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--precompute":
        # R18: python talk_matte.py --precompute VIDEO [--backend X] [--out F]
        args = sys.argv[2:]
        video, backend, out = None, "mediapipe", None
        i = 0
        while i < len(args):
            if args[i] == "--backend":
                backend = args[i + 1]; i += 2
            elif args[i] == "--out":
                out = args[i + 1]; i += 2
            else:
                video = args[i]; i += 1
        if not video:
            raise SystemExit("usage: python talk_matte.py --precompute VIDEO "
                             "[--backend mediapipe|modnet] [--out CACHE.npz]")
        precompute(video, backend=backend, out=out)
        sys.exit(0)

    # быстрый замер бэкенда: вырезать спикера из середины ролика на шахматку,
    # чтобы край было видно глазами (замер, не вывод).
    from PIL import Image
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), ".cache_src", "IMG_8138.MP4")
    backend = sys.argv[2] if len(sys.argv) > 2 else "mediapipe"
    c = cv2.VideoCapture(src)
    n = c.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    c.set(cv2.CAP_PROP_POS_FRAMES, int(n // 2))
    ok, fr = c.read()
    c.release()
    if not ok:
        raise SystemExit("не прочёл кадр из %s" % src)
    m = Matter(backend=backend)
    rgba = cutout_rgba(fr, m)
    h, w = rgba.shape[:2]
    board = np.zeros((h, w, 3), np.uint8)                  # шахматка под альфу
    sq = 40
    for y in range(0, h, sq):
        for x in range(0, w, sq):
            if ((x // sq) + (y // sq)) % 2:
                board[y:y + sq, x:x + sq] = (60, 60, 60)
            else:
                board[y:y + sq, x:x + sq] = (200, 200, 200)
    base = Image.fromarray(board)
    base.paste(Image.fromarray(rgba, "RGBA"), (0, 0), Image.fromarray(rgba, "RGBA"))
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "talk_matte_REVIEW.jpg")
    base.convert("RGB").save(out, quality=88)
    print("OK ->", out, "(%s, %dx%d)" % (backend, w, h))


if __name__ == "__main__":
    raise SystemExit(main())

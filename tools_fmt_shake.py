# -*- coding: utf-8 -*-
"""Измеритель ДРОЖАНИЯ кадра: медианный сдвиг между соседними кадрами.

ЗАЧЕМ. Дрожание нельзя мерить длинами сцен — это косвенный признак, и я на нём
уже ошибся дважды, отчитавшись о починке, которой не было. Здесь меряется сам
эффект: фазовая корреляция соседних кадров даёт сдвиг картинки в пикселях.
Устойчивый кадр даёт единицы десятых; дрожание — единицы пикселей и выше.
Склейки исключаются: сдвиг больше SKIP_PX считается сменой плана, а не тряской.
"""
import sys
import cv2
import numpy as np

SKIP_PX = 25.0          # больше — это склейка/смена плана, не дрожание


def measure(path, t0=0.0, t1=None, step=1):
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    end = int((t1 or n / fps) * fps)
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(t0 * fps))
    prev, shifts = None, []
    for _ in range(int(t0 * fps), min(end, n), step):
        ok, f = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(cv2.resize(f, (270, 480)), cv2.COLOR_BGR2GRAY)
        g = np.float32(g)
        if prev is not None:
            (dx, dy), _ = cv2.phaseCorrelate(prev, g)
            d = float(np.hypot(dx, dy)) * 4.0        # обратно в масштаб 1080
            if d < SKIP_PX:
                shifts.append(d)
        prev = g
    cap.release()
    if not shifts:
        return None
    a = np.array(shifts)
    return dict(кадров=len(a), медиана=float(np.median(a)),
                среднее=float(a.mean()), p90=float(np.percentile(a, 90)),
                максимум=float(a.max()))


if __name__ == "__main__":
    for p in sys.argv[1:]:
        r = measure(p)
        print("%-40s %s" % (p.split("/")[-1],
                            "нет данных" if not r else
                            "медиана %.2f px | p90 %.2f | макс %.2f | кадров %d"
                            % (r["медиана"], r["p90"], r["максимум"], r["кадров"])))

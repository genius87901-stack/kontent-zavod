# -*- coding: utf-8 -*-
"""Стадия 1 движка №4: talking-видео -> source_meta.json.

Метаданные исходника + трек лица спикера + темп речи. Нужны дальше:
  - split: чтобы шов не резал лицо (где лицо по времени — знаем);
  - cutout: опорная точка для матирования;
  - страховка «текст не на лице» (правило завода) для субтитра/плашки;
  - темп речи (wpm) — планировщику монтажа (плотный блок -> монтаж быстрее).

Всё как в проекте: ffmpeg НЕ в PATH -> через imageio_ffmpeg; ffprobe в комплект
не входит -> длительность/аудио берём из cv2 + парсинга stderr ffmpeg. Детектор
лиц — checks.face_boxes_img (mediapipe 0.10.14, protobuf 4.25.9 — НЕ обновлять).

Контракт выхода = REELS_TALK_ARCH.md §6:
  {"src","dur","fps","w","h","has_audio","speech_wpm",
   "face_track":[{"t":сек,"box":[x,y,w,h]}|{"t":сек,"box":null}, ...]}
"""
import os
import json
import argparse
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))


def _probe_audio(video_path):
    """Есть ли звуковая дорожка — по stderr ffmpeg (ffprobe в комплекте нет)."""
    import imageio_ffmpeg
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    p = subprocess.run([ff, "-i", video_path], capture_output=True)
    err = p.stderr.decode("utf-8", "ignore")
    return "Audio:" in err


def _speech_wpm(transcript_path, dur):
    """Слов в минуту из транскрипта (если дан) — темп речи."""
    if not transcript_path or not os.path.exists(transcript_path):
        return None
    segs = json.load(open(transcript_path, encoding="utf-8"))
    n = sum(len(s.get("words", [])) for s in segs)
    return round(n / dur * 60.0, 1) if dur > 0 else None


def analyze(video_path, transcript_path=None, out_path=None, step=0.5):
    """Собрать source_meta.json. step — шаг сэмплирования трека лица (сек)."""
    import cv2
    import checks  # mediapipe-детектор завода (face_boxes_img)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError("cv2 не открыл видео: %s" % video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    dur = round(n / fps, 3) if fps else 0.0

    face_track = []
    t = 0.0
    while t < dur:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, frame = cap.read()
        if not ok:
            break
        boxes = checks.face_boxes_img(frame)
        # берём самое крупное лицо (спикер — крупнее случайных на фоне)
        box = max(boxes, key=lambda b: b[2] * b[3]) if boxes else None
        face_track.append({"t": round(t, 2),
                           "box": [int(v) for v in box] if box else None})
        t += step
    cap.release()

    meta = {"src": video_path, "dur": dur, "fps": round(fps, 3),
            "w": w, "h": h, "has_audio": _probe_audio(video_path),
            "speech_wpm": _speech_wpm(transcript_path, dur),
            "face_track": face_track}

    if out_path is None:
        base = os.path.splitext(os.path.basename(video_path))[0]
        out_path = os.path.join(os.path.dirname(video_path) or ".",
                                base + "_meta.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return out_path, meta


def main():
    ap = argparse.ArgumentParser(
        description="talking-видео -> source_meta.json (Стадия 1)")
    ap.add_argument("video", help="путь к говорящему видео")
    ap.add_argument("-t", "--transcript", default=None,
                    help="transcript.json (для темпа речи), опц.")
    ap.add_argument("-o", "--out", default=None, help="куда писать meta.json")
    ap.add_argument("--step", type=float, default=0.5,
                    help="шаг сэмплирования трека лица, сек (по умолчанию 0.5)")
    args = ap.parse_args()

    out, meta = analyze(args.video, args.transcript, args.out, args.step)
    faces = sum(1 for f in meta["face_track"] if f["box"])
    print("готово: %s" % out)
    print("  %dx%d, %.1f fps, %.1f сек, звук: %s, темп: %s wpm"
          % (meta["w"], meta["h"], meta["fps"], meta["dur"],
             "да" if meta["has_audio"] else "нет", meta["speech_wpm"]))
    print("  трек лица: лицо в %d из %d сэмплов" % (faces, len(meta["face_track"])))


if __name__ == "__main__":
    raise SystemExit(main())

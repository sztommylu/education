"""Shared local English OCR; model cache is kept inside the project."""
import io
import os
from pathlib import Path
from threading import Lock
from time import perf_counter

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(ROOT / ".ocr-cache"))
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
os.environ.setdefault("HF_HOME", str(ROOT / ".ocr-cache" / "huggingface"))
os.environ.setdefault("MODELSCOPE_CACHE", str(ROOT / ".ocr-cache" / "modelscope"))
os.environ.setdefault("OMP_NUM_THREADS", "2")
MODEL_NAME = "en_PP-OCRv5_mobile_rec"
MODEL_LOCK = Lock()
_MODEL = None


def load_model():
    global _MODEL
    if _MODEL is None:
        from paddleocr import TextRecognition
        _MODEL = TextRecognition(model_name=MODEL_NAME, device="cpu", enable_mkldnn=False, cpu_threads=2)
    return _MODEL


def recognize(raw):
    import numpy as np
    from PIL import Image, ImageChops, UnidentifiedImageError
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.width * source.height > 8_000_000:
                raise ValueError("图片过大，请使用小于 800 万像素的单行截图。")
            source.load()
            rgba = source.convert("RGBA")
            image = Image.new("RGB", rgba.size, "white")
            image.paste(rgba, mask=rgba.getchannel("A"))
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("无法读取图片，请使用 PNG 或 JPEG。") from exc
    # Remove unused canvas margins, preserving every stroke and spelling error.
    ink = ImageChops.invert(image.convert("L")).point(lambda p: 255 if p > 60 else 0)
    bounds = ink.getbbox()
    if bounds is None:
        raise ValueError("请先写一个英文单词。")
    image = image.crop(bounds)
    padding = max(8, round(image.height * .15))
    framed = Image.new("RGB", (image.width + padding * 2, image.height + padding * 2), "white")
    framed.paste(image, (padding, padding))
    with MODEL_LOCK:
        model = load_model()
        start = perf_counter()
        result = next(iter(model.predict(np.asarray(framed)[:, :, ::-1].copy(), batch_size=1)))
        return {"text": result["rec_text"], "confidence": float(result["rec_score"]),
                "elapsed_ms": round((perf_counter() - start) * 1000), "model": MODEL_NAME}

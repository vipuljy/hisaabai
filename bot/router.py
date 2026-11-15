"""Free features for the router: cheap signals that predict whether Haiku will read a photo right.

photo_features() looks at the image (blur, brightness, contrast, ink, shape, and Tesseract's script /
orientation check if Tesseract is installed). ocr_features() looks at the RapidOCR reading (confidence, words,
digits, table-like lines, GSTIN, which proofs passed). Together they also stand in for "printed vs handwritten":
the hand label is NEVER a feature, because the live bot does not have it. Missing values are None.
"""

from __future__ import annotations

import io
import re

IMAGE_FEATURES = ["blur", "brightness", "contrast", "ink_ratio", "aspect", "osd_devanagari", "osd_script_conf",
                  "osd_orient_conf"]
OCR_FEATURES = ["page_conf", "word_count", "n_lines", "low_conf_share", "digit_ratio", "words_per_line",
                "numeric_line_share", "has_gstin", "devanagari_ratio", "total_found", "total_trusted",
                "proof_words", "proof_maths", "date_found", "invoice_found"]
FEATURES = IMAGE_FEATURES + OCR_FEATURES


def photo_features(image_bytes: bytes) -> dict:
    """Image-only signals, on a copy shrunk to 1000 px on the long side (fast, comparable across phones)."""
    import cv2
    import numpy as np
    from PIL import Image, ImageOps

    img = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes))).convert("L")
    w, h = img.size
    img.thumbnail((1000, 1000))
    gray = np.array(img)
    ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    out = {"blur": float(cv2.Laplacian(gray, cv2.CV_64F).var()), "brightness": float(gray.mean()),
           "contrast": float(gray.std()), "ink_ratio": float((ink > 0).mean()), "aspect": round(w / h, 3),
           "osd_devanagari": None, "osd_script_conf": None, "osd_orient_conf": None}
    try:  # optional: Tesseract's orientation + script detector
        from bot.read_ocr import _tesseract
        tess = _tesseract()
        osd = tess.image_to_osd(img, output_type=tess.Output.DICT)
        out["osd_devanagari"] = 1.0 if str(osd.get("script", "")).lower().startswith("devanagari") else 0.0
        out["osd_script_conf"] = float(osd.get("script_conf", 0))
        out["osd_orient_conf"] = float(osd.get("orientation_conf", 0))
    except Exception:
        pass
    return out


def ocr_features(rec: dict | None) -> dict:
    """Signals from a cached RapidOCR record (src/06_free_readers_eval.py run ocr_rapid)."""
    out = dict.fromkeys(OCR_FEATURES)
    if not rec or rec.get("error") not in (None, "no_text"):
        return out
    text = rec.get("raw_text") or ""
    ocr = rec.get("ocr") or {}
    words = ocr.get("words") or []
    lines = [l for l in text.splitlines() if l.strip()]
    chars = re.sub(r"\s", "", text)
    fields = rec.get("fields") or {}
    unsure = set(fields.get("unsure_fields") or [])
    out.update({
        "page_conf": ocr.get("page_conf"),
        "word_count": len(words),
        "n_lines": len(lines),
        "low_conf_share": (sum(w[1] < 80 for w in words) / len(words)) if words else None,
        "digit_ratio": (sum(c.isdigit() for c in chars) / len(chars)) if chars else None,
        "words_per_line": (len(words) / len(lines)) if lines else None,
        "numeric_line_share": (sum(len(re.findall(r"\d+(?:\.\d+)?", l)) >= 3 for l in lines) / len(lines)) if lines else None,
        "has_gstin": 1.0 if rec.get("supplier_gstin") else 0.0,
        "devanagari_ratio": (sum("ऀ" <= c <= "ॿ" for c in chars) / len(chars)) if chars else None,
        "total_found": 1.0 if fields.get("total_amount") is not None else 0.0,
        "total_trusted": 1.0 if fields.get("total_amount") is not None and "total_amount" not in unsure else 0.0,
        "proof_words": 1.0 if (rec.get("checks") or {}).get("words_match_total") == "pass" else 0.0,
        "proof_maths": 1.0 if rec.get("total_proof") == "maths" else 0.0,
        "date_found": 1.0 if fields.get("bill_date") else 0.0,
        "invoice_found": 1.0 if fields.get("invoice_number") else 0.0,
    })
    return out

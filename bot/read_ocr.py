"""Read a bill photo with free OCR + the same rules as the PDF reader.

Engines (all free, run on the laptop CPU, no API): rapidocr (DEFAULT since 25 Oct: PP-OCR models via
onnxruntime; read 53% of printed totals right vs Tesseract's 16%), tesseract (fallback when rapidocr is
not installed). Every engine is turned into the SAME word boxes [text, conf 0-100, block, par, line, left, width],
so field_rules, checks and the frozen rules_v3 are identical for all of them.

Steps: fix the phone's rotation flag -> greyscale -> enlarge small photos (Tesseract
wants ~300 dpi) -> Tesseract's orientation check turns sideways photos upright ->
OCR word boxes -> lines, split where a big gap separates two columns (seller | buyer)
-> bot/field_rules.py + bot/checks.py.
Guards (all fixed rules, not tuned on the eval bills; the router step learns better ones):
  confidence  page average < 60 -> every field unsure; TOTAL line < 60 -> total unsure
  proof gate  total trusted only if "Rupees ... Only" in words was found and equals the digits
  supplier    the name is always unsure (OCR cannot reliably tell seller from buyer);
              the seller's GSTIN is recorded instead, to match a known supplier later
  invoice no. trusted only if the same number (4+ characters) appears at least twice on the page
FROZEN at rules_v3 (2026-10-25): no more rule changes tuned on the 136 eval bills.
"""

from __future__ import annotations

import io
import json
import os
import re
import statistics
import threading
import time
from pathlib import Path

from bot import checks as checks_mod
from bot import field_rules, templates
from bot.extract import FIELDS, Extraction, norm_amount

READER = "ocr_rules"
ENGINES = {"tesseract": "ocr_rules", "rapidocr": "ocr_rapid", "rapidocr_clean": "ocr_rapid_clean",
           "doctr": "ocr_doctr"}  # engine -> eval run name


def default_engine() -> str:
    """The bot's photo engine: RapidOCR if installed (requirements-ocr-extra.txt), else Tesseract.
    Photo cleanup (engine "rapidocr_clean") is NOT the default: on DEV it added 2 trusted totals but also 1 silent
    error (a handwritten date 21/7 read as 21/3 with 82% confidence); see the Build log."""
    try:
        import rapidocr  # noqa: F401
        return "rapidocr"
    except ImportError:
        return "tesseract"
RULES_VERSION = "rules_v4"  # v4 = v3 extraction + proof-based trust for the total (words / maths / memory)
CONF_THRESHOLD = 60
COLUMN_GAP_CHARS = 4          # a gap wider than ~4 characters between two words = a new column
TESSERACT_CONFIG = "--psm 4"  # psm 4 = one column of text lines of varying size: keeps a bill row on one line
MIN_SIDE_FOR_OCR = 2000       # photos smaller than this on the long side are enlarged 2x
WINDOWS_DEFAULT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
_BOX_KEYS = ("text", "conf", "block_num", "par_num", "line_num", "left", "width")


class NoTesseract(RuntimeError):
    pass


class NoEngine(RuntimeError):
    pass


_rapid = {"engine": None}
_rapid_lock = threading.Lock()


def _rapidocr():
    """One RapidOCR instance per process (loading the models takes a few seconds)."""
    with _rapid_lock:
        if _rapid["engine"] is None:
            try:
                import logging as _logging

                from rapidocr import RapidOCR
                _logging.getLogger("RapidOCR").setLevel(_logging.WARNING)
            except ImportError as err:
                raise NoEngine("rapidocr not installed: pip install -r requirements-ocr-extra.txt") from err
            _rapid["engine"] = RapidOCR()
        return _rapid["engine"]


_doctr = {"model": None}
_doctr_lock = threading.Lock()


def _doctr_model():
    """docTR (PyTorch). Optional: pip install -r requirements-ocr-doctr.txt (~1 GB with PyTorch)."""
    with _doctr_lock:
        if _doctr["model"] is None:
            try:
                from doctr.models import ocr_predictor
            except ImportError as err:
                raise NoEngine("docTR not installed: pip install -r requirements-ocr-doctr.txt") from err
            _doctr["model"] = ocr_predictor(pretrained=True)
        return _doctr["model"]


def words_from_doctr(page: dict) -> list[list]:
    """docTR's export() of one page (blocks > lines > words, geometry relative 0-1) -> the same word boxes
    as Tesseract: [text, conf 0-100, block, par, line, left px, width px]."""
    h, w = page.get("dimensions") or (1000, 1000)
    out = []
    for b, block in enumerate(page.get("blocks", []), start=1):
        for n, line in enumerate(block.get("lines", []), start=1):
            for word in line.get("words", []):
                text = (word.get("value") or "").strip()
                if not text:
                    continue
                (x0, _y0), (x1, _y1) = word["geometry"][:2]
                out.append([text, round(float(word.get("confidence", 0)) * 100, 1), b, 1, n,
                            int(x0 * w), max(1, int((x1 - x0) * w))])
    return out


def words_from_segments(boxes, txts, scores) -> list[list]:
    """RapidOCR gives one box per text SEGMENT (4 corner points), not per word. Segments whose vertical
    centres are within half a text height share a line; each segment's words are spread over its box
    width by character count. Output = the same word boxes as Tesseract (conf 0-100)."""
    segs = []
    for box, txt, sc in zip(boxes, txts, scores):
        txt = (txt or "").strip()
        if not txt:
            continue
        xs, ys = [float(p[0]) for p in box], [float(p[1]) for p in box]
        segs.append({"text": txt, "conf": float(sc) * 100, "left": min(xs), "right": max(xs),
                     "mid": (min(ys) + max(ys)) / 2, "h": max(1.0, max(ys) - min(ys))})
    segs.sort(key=lambda g: g["mid"])
    lines: list[list[dict]] = []
    for g in segs:
        if lines:
            row = lines[-1]
            mid = sum(x["mid"] for x in row) / len(row)
            h = statistics.median(x["h"] for x in row)
            if abs(g["mid"] - mid) <= 0.5 * max(h, g["h"]):
                row.append(g)
                continue
        lines.append([g])
    out = []
    for n, row in enumerate(lines, start=1):
        for g in sorted(row, key=lambda x: x["left"]):
            width, text = g["right"] - g["left"], g["text"]
            pos = 0
            for word in text.split():
                start = text.index(word, pos)
                pos = start + len(word)
                left = g["left"] + width * start / len(text)
                out.append([word, round(g["conf"], 1), 1, 1, n, int(left), max(1, int(width * len(word) / len(text)))])
    return out


def _tesseract():
    """pytesseract, pointed at the Tesseract program (TESSERACT_CMD, the Windows default folder, or PATH)."""
    try:
        import pytesseract
    except ImportError as err:
        raise NoTesseract("pytesseract not installed") from err
    cmd = os.getenv("TESSERACT_CMD") or (WINDOWS_DEFAULT if Path(WINDOWS_DEFAULT).exists() else None)
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    return pytesseract


def prepare(image_bytes: bytes):
    from PIL import Image, ImageOps

    img = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes))).convert("L")
    if max(img.size) < MIN_SIDE_FOR_OCR:
        img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
    return img


def rotation_from_osd(osd: dict | None) -> int:
    """Degrees to turn the photo CLOCKWISE to make it upright (0 if Tesseract is unsure)."""
    if not osd or float(osd.get("orientation_conf", 0)) < 1.0:
        return 0
    return int(osd.get("rotate", 0)) % 360


def words_from_data(data: dict) -> list[list]:
    """Tesseract's image_to_data dict -> compact word boxes [text, conf, block, par, line, left, width] (kept in the cache)."""
    n = len(data["text"])
    col = {k: data.get(k) or [0] * n for k in _BOX_KEYS}
    out = []
    for i in range(n):
        word, conf = (col["text"][i] or "").strip(), float(col["conf"][i])
        if word and conf >= 0:
            out.append([word, round(conf, 1)] + [int(col[k][i]) for k in _BOX_KEYS[2:]])
    return out


def lines_from_words(words: list[list]) -> tuple[list[str], list[float]]:
    """Word boxes -> text lines and each line's mean word confidence.
    A Tesseract line is cut where the gap between two words is wider than COLUMN_GAP_CHARS characters,
    so side-by-side blocks (seller | buyer | invoice no.) become separate lines."""
    groups, order = {}, []
    for w in words:
        key = tuple(w[2:5])
        if key not in groups:
            order.append(key)
            groups[key] = []
        groups[key].append(w)
    lines, confs = [], []
    for key in order:
        ws = sorted(groups[key], key=lambda w: w[5])
        char_w = statistics.median(w[6] / max(1, len(w[0])) for w in ws) or 1
        segment = [ws[0]]
        for prev, cur in zip(ws, ws[1:]):
            gap = cur[5] - (prev[5] + prev[6])
            if char_w and prev[6] and gap > COLUMN_GAP_CHARS * char_w:
                lines.append(" ".join(w[0] for w in segment))
                confs.append(sum(w[1] for w in segment) / len(segment))
                segment = []
            segment.append(cur)
        lines.append(" ".join(w[0] for w in segment))
        confs.append(sum(w[1] for w in segment) / len(segment))
    return lines, confs


def _page_amounts(text: str) -> list[float]:
    seen, out = set(), []
    for line in text.splitlines():
        for a in field_rules.amounts_in(line):
            if a not in seen:
                seen.add(a)
                out.append(a)
    return out


def trust_total(text: str, fields: dict, memory: list | None = None) -> dict:
    """Which total to keep, and whether it is TRUSTED. Trusted = a proof (amount in words = digits, or the bill's own
    GST maths adds up, to the nearest rupee) AND the sanity + balance checks. Candidates, in order:
      1. the supplier memory's label (if any), 2. the rules' total,
      3. only when the rules found no total, 0, or just the weak plain "Total" label: an amount printed on the page
         that equals the amount in words or the GST maths ("recovered").
    The first candidate with a proof wins. Without a proof the rules' total is kept but marked unsure."""
    rules_total = fields.get("total_amount")
    loc = field_rules.locate_total(text)
    cands = []
    for tpl in memory or []:
        v = templates.apply(text, tpl)
        if v is not None and norm_amount(v) != rules_total:
            cands.append((v, "memory"))
    if rules_total is not None:
        cands.append((rules_total, "rules"))
    weak = rules_total in (None, 0) or (loc is not None and loc["label"] == field_rules.WEAK_TOTAL_LABEL)
    if weak:
        targets = checks_mod.rate_formula_totals(text) + checks_mod.summary_totals(text)
        words = checks_mod.amount_in_words(text)
        if words:
            targets.append(words)
        for a in _page_amounts(text):
            if a > 0 and any(abs(a - t) <= checks_mod.NEAREST_RUPEE for t in targets) and norm_amount(a) != rules_total:
                cands.append((a, "recovered"))
    for value, source in cands:
        proof = checks_mod.total_proof(text, value)
        if proof and checks_mod.total_is_safe(text, value):
            return {"total": norm_amount(value), "trusted": True, "proof": proof, "source": source}
    return {"total": rules_total, "trusted": False, "proof": None, "source": "rules"}


def analyse(words: list[list], today=None, memory: list | None = None) -> dict:
    """Word boxes -> fields + guards. Pure function: re-run on cached boxes after any rule change.
    memory = remembered total labels for this supplier (bot/templates.py), or None."""
    lines, line_conf = lines_from_words(words)
    text = "\n".join(lines)
    page_conf = round(sum(w[1] for w in words) / len(words), 1) if words else None
    fields, checks = field_rules.read_text(text, today=today)
    rules_only = list(fields["unsure_fields"])
    guard = set(rules_only)
    if page_conf is not None and page_conf < CONF_THRESHOLD:                      # confidence: page
        guard |= {f for f in FIELDS if fields.get(f) is not None}
    loc = field_rules.locate_total(text)
    total_line_conf = None
    if loc:
        idx = sorted({loc["label_line"], loc["number_line"]})
        total_line_conf = round(sum(line_conf[i] for i in idx) / len(idx), 1)
    # rules_v4: the total's trust comes ONLY from a proof (words / maths / memory + proof) and the sanity + balance
    # checks, the same for every engine; confidence and the plain-"Total" rule no longer decide it.
    tt = trust_total(text, fields, memory)
    fields["total_amount"] = tt["total"]
    guard.discard("total_amount")
    if fields.get("total_amount") is not None and not tt["trusted"]:
        guard.add("total_amount")
    if fields.get("supplier_name") is not None:                                  # supplier (C)
        guard.add("supplier_name")
    if fields.get("invoice_number") is not None and not invoice_seen_twice(fields["invoice_number"], text):
        guard.add("invoice_number")                                              # invoice no. (F)
    if fields.get("bill_date") is not None and (len(labelled_dates(text)) > 1 or bill_date_from_bare_dated(text)):
        guard.add("bill_date")  # rules_v4: 2+ labelled dates, or a bare Tally 'Dated' (several boxes use it) -> ask
    fields["unsure_fields"] = [f for f in FIELDS if f in guard]
    return {"fields": fields, "checks": checks, "text": text, "unsure_rules_only": rules_only,
            "supplier_gstin": field_rules.find_seller_gstin(text),
            "total_proof": tt["proof"], "total_source": tt["source"],
            "ocr": {"line_conf": [round(c, 1) for c in line_conf], "page_conf": page_conf,
                    "total_line_conf": total_line_conf, "word_count": len(words)}}


def labelled_dates(text: str) -> set:
    """Every date printed after a bill-date label ('Date', 'Dated', 'Dt.'), skipping due / order / LR ... dates."""
    lines = field_rules._lines(text)
    found = set()
    for rx in (field_rules._BILL_DATE_LABEL, field_rules._DT_LABEL):
        for i, line in enumerate(lines):
            for m in rx.finditer(line):
                if field_rules._NOT_BILL_DATE.search(line[: m.start()]):
                    continue
                d = field_rules._date_after(lines, i, m.end())
                if d:
                    found.add(d)
    return found


def bill_date_from_bare_dated(text: str) -> bool:
    """True if the bill date came from a bare 'Dated' label. Tally invoices print 'Dated' in several boxes
    (invoice, delivery note, buyer's order, despatch), so a bare 'Dated' is ambiguous; 'Invoice Date' is not."""
    lines = field_rules._lines(text)
    for i, line in enumerate(lines):
        for m in field_rules._BILL_DATE_LABEL.finditer(line):
            if field_rules._NOT_BILL_DATE.search(line[: m.start()]):
                continue
            if field_rules._date_after(lines, i, m.end()):
                label = re.sub(r"\s+", "", m.group(0).lower())
                return label.endswith("dated") and not re.match(r"(invoice|inv|bill)", label)
    return False


def invoice_seen_twice(invoice: str, text: str) -> bool:
    """OCR misreads (I->1, O->0) rarely repeat the same way, so a number printed twice and read
    the same both times is much safer. Short numbers (< 4 characters) never count: they repeat by chance."""
    inv = re.sub(r"\s+", "", str(invoice).upper())
    if len(inv) < 4:
        return False
    return len(re.findall(r"(?<![A-Z0-9])" + re.escape(inv) + r"(?![A-Z0-9])", text.upper())) >= 2


def _blank(error: str, reader: str = READER) -> Extraction:
    return Extraction(fields=field_rules.blank_fields(), raw_text="", model=reader,
                      prompt_version=RULES_VERSION, error=error, extra={})


def _osd_rotation(img) -> int:
    """Tesseract's orientation check (shared by every engine, so the comparison is about reading, not rotating).
    Without Tesseract the photo is used as it is."""
    try:
        tess = _tesseract()
        return rotation_from_osd(tess.image_to_osd(img, output_type=tess.Output.DICT))
    except Exception:
        return 0


def clean_photo(rgb):
    """Step-4 experiment: trim the dark border around the paper, straighten a small tilt, even out the contrast
    (CLAHE). numpy RGB in, numpy RGB out (OpenCV comes with rapidocr)."""
    import cv2
    import numpy as np

    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    # 1. trim: the biggest bright region is the paper; crop to it if it fills most of the photo
    _, paper = cv2.threshold(cv2.GaussianBlur(gray, (5, 5), 0), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(paper, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        x, y, w, h = cv2.boundingRect(max(contours, key=cv2.contourArea))
        if 0.5 * gray.size < w * h < 0.98 * gray.size:
            rgb, gray = rgb[y:y + h, x:x + w], gray[y:y + h, x:x + w]
    # 2. deskew: angle of the ink, only small tilts (bigger turns are the orientation check's job)
    ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    pts = np.column_stack(np.where(ink > 0))
    if len(pts) > 500:
        angle = cv2.minAreaRect(pts[:, ::-1].astype(np.float32))[-1]
        angle = angle - 90 if angle > 45 else angle
        if 0.3 < abs(angle) < 10:
            h, w = gray.shape
            m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
            rgb = cv2.warpAffine(rgb, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    # 3. contrast: CLAHE on the lightness channel
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    lab[:, :, 0] = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(lab[:, :, 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)


def _rapid_words(image_bytes: bytes, clean: bool = False):
    import numpy as np
    from PIL import Image, ImageOps

    engine = _rapidocr()
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes))).convert("RGB")
    rotation = _osd_rotation(img.convert("L"))
    if rotation:
        img = img.rotate(-rotation, expand=True)
    arr = np.array(img)
    if clean:
        arr = clean_photo(arr)
    res = engine(arr)
    if res is None or res.boxes is None:
        return [], rotation
    return words_from_segments(res.boxes, res.txts, res.scores), rotation


def read_ocr(image_bytes: bytes, today=None, engine: str = "auto") -> Extraction:
    """One photo -> fields like a Claude reply. OCR details (word boxes, confidences, rotation) in .extra.
    engine="auto" = default_engine() (RapidOCR, falling back to Tesseract)."""
    started = time.perf_counter()
    if engine == "auto":
        engine = default_engine()
    reader = ENGINES.get(engine, READER)
    try:
        if engine in ("rapidocr", "rapidocr_clean"):
            words, rotation = _rapid_words(image_bytes, clean=engine == "rapidocr_clean")
        elif engine == "doctr":
            import numpy as np
            from PIL import Image, ImageOps

            model = _doctr_model()
            img = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes))).convert("RGB")
            rotation = _osd_rotation(img.convert("L"))
            if rotation:
                img = img.rotate(-rotation, expand=True)
            words = words_from_doctr(model([np.array(img)]).export()["pages"][0])
        else:
            tess = _tesseract()
            img = prepare(image_bytes)
            try:
                rotation = rotation_from_osd(tess.image_to_osd(img, output_type=tess.Output.DICT))
            except Exception as err:  # OSD fails on pages with little text: keep the photo as it is
                if type(err).__name__ == "TesseractNotFoundError":
                    raise NoTesseract(str(err)) from err
                rotation = 0
            if rotation:
                img = img.rotate(-rotation, expand=True)  # PIL turns anticlockwise, so negate
            data = tess.image_to_data(img, lang="eng", config=TESSERACT_CONFIG, output_type=tess.Output.DICT)
            words = words_from_data(data)
    except NoTesseract:
        return _blank("no_tesseract", reader)
    except NoEngine:
        return _blank("no_engine", reader)
    except Exception as err:
        return _blank("no_tesseract" if type(err).__name__ == "TesseractNotFoundError" else f"ocr_error: {err}", reader)

    out = analyse(words, today=today)
    ex = Extraction(fields=out["fields"], raw_text=out["text"], model=reader, prompt_version=RULES_VERSION,
                    error=None if words else "no_text")
    ex.latency_ms = int((time.perf_counter() - started) * 1000)
    ex.extra = {"checks": out["checks"], "unsure_rules_only": out["unsure_rules_only"],
                "supplier_gstin": out["supplier_gstin"], "total_proof": out["total_proof"],
                "total_source": out["total_source"],
                "ocr": out["ocr"] | {"rotation": rotation, "words": words}}
    return ex


# ---------------------------------------------------------------- eval helper
def _write_atomic(path: Path, record: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)  # a killed run never leaves a half-written cache file


def read_bill(bill_id: str, image_path: Path, cache_dir: Path, fresh: bool = False, today=None,
              engine: str = "tesseract") -> dict:
    """OCR one labelled photo, cached. A cached bill is NOT re-OCRed: its saved word boxes are
    re-analysed with the current rules (seconds instead of minutes)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{bill_id}.json"
    if cache_file.exists() and not fresh:
        rec = json.loads(cache_file.read_text(encoding="utf-8"))
        if (rec.get("ocr") or {}).get("words") is not None:
            out = analyse(rec["ocr"]["words"], today=today)
            rec.update(fields=out["fields"], raw_text=out["text"], checks=out["checks"], prompt=RULES_VERSION,
                       unsure_rules_only=out["unsure_rules_only"], supplier_gstin=out["supplier_gstin"],
                       total_proof=out["total_proof"], total_source=out["total_source"])
            rec["ocr"].update(out["ocr"])
            _write_atomic(cache_file, rec)
            return rec
        if rec.get("error") in ("no_image",):
            return rec
        # older cache without word boxes: fall through and OCR again
    reader = ENGINES.get(engine, READER)
    if not image_path.exists():
        ex = _blank("no_image", reader)
    else:
        ex = read_ocr(image_path.read_bytes(), today=today, engine=engine)
    rec = {
        "bill_id": bill_id, "model": reader, "prompt": RULES_VERSION, "engine": engine,
        "fields": ex.fields, "raw_text": ex.raw_text, "error": ex.error,
        "input_tokens": 0, "output_tokens": 0, "latency_ms": ex.latency_ms,
        "checks": ex.extra.get("checks", {}),
        "unsure_rules_only": ex.extra.get("unsure_rules_only", ex.fields["unsure_fields"]),
        "supplier_gstin": ex.extra.get("supplier_gstin"),
        "total_proof": ex.extra.get("total_proof"), "total_source": ex.extra.get("total_source"),
        "ocr": ex.extra.get("ocr"),
    }
    if ex.error not in ("no_tesseract", "no_engine"):  # never cache "engine missing": install it and re-run
        _write_atomic(cache_file, rec)
    return rec

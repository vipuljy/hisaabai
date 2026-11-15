"""Peak memory of the running bot under load (Render free has 512 MB). Linux only (reads /proc), e.g. WSL or a VM:
    MALLOC_ARENA_MAX=2 python scripts/memory_check.py
Starts the real app (uvicorn, P5_no_ocr, the real bill-image preparation and PDF reading) with WhatsApp and Anthropic
faked (no network, no cost), sends (1) one shop's messages one at a time and (2) bursts of 25 photos / PDFs / texts
from 5 shops at once, and prints the process's peak memory (VmHWM). 29 Oct 2026, Python 3.10, asyncio loop:
one at a time 234-241 MB; bursts 398-409 MB with MALLOC_ARENA_MAX=2 (as in the Dockerfile), 667 MB without."""

import concurrent.futures as cf
import io
import itertools
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = r'''
import json, os, sys, tempfile
from functools import partial
from types import SimpleNamespace as NS
os.environ.update({k: "" for k in ("DATABASE_URL", "SUPABASE_URL", "SUPABASE_SERVICE_KEY", "ANTHROPIC_API_KEY",
                                   "SARVAM_API_KEY", "WHATSAPP_TOKEN", "APP_SECRET")})
sys.path.insert(0, os.environ["HISAAB_ROOT"])
import anthropic  # loaded in production too: count its memory
from bot.app import create_app
from bot.conversation import Bot, Limits
from bot.extract import extract_bill
from bot.photos import LocalPhotos
from bot.policy import PolicyReader
from bot.store import MemoryStore
BLOBS = {k: open(v, "rb").read() for k, v in json.loads(os.environ["HISAAB_MEDIA"]).items()}
class FakeAnthropic:
    def __init__(self): self.messages = self
    def create(self, **kw):
        f = {"is_bill": True, "supplier_name": "Shiv Medical Distributors", "bill_date": "2026-10-20",
             "invoice_number": "D5721", "total_amount": 4769, "due_date": None, "unsure_fields": []}
        return NS(content=[NS(type="text", text=json.dumps(f))], stop_reason="end_turn",
                  usage=NS(input_tokens=1500, output_tokens=200, output_tokens_details=None))
class FakeWA:
    n = 0
    async def _id(self, *a, **k):
        FakeWA.n += 1
        return f"wamid.out{FakeWA.n}"
    send_text = send_buttons = send_list = send_template = _id
    async def download_media(self, media_id): return BLOBS[media_id.split("#")[0]]
c = FakeAnthropic()
reader = PolicyReader("P5_no_ocr", haiku=partial(extract_bill, model="claude-haiku-5-5", client=c),
                      sonnet=partial(extract_bill, model="claude-sonnet-5-5", client=c))
app = create_app(bot=Bot(MemoryStore(), FakeWA(), reader, pilot_open=True, photos=LocalPhotos(tempfile.mkdtemp()),
                         limits=Limits(per_shop_per_day=10**6, total_per_day=10**6)), verify_token="v", app_secret="")
'''


def media(tmp: Path) -> dict:
    sys.path.insert(0, str(ROOT / "tests"))
    from PIL import Image, ImageDraw
    import test_pdf_whatsapp as T  # the same invented PDFs the tests use
    import random
    rnd = random.Random(1)
    photo = Image.linear_gradient("L").resize((4000, 3000)).convert("RGB")  # a 12 MP phone photo
    photo = Image.blend(photo, Image.effect_noise((4000, 3000), 40).convert("RGB"), 0.3)
    photo.save(tmp / "photo.jpg", quality=88)
    (tmp / "pdf.pdf").write_bytes(T.text_pdf([T.HEADER + ["x"] * 3, T.ITEMS * 3, ["Page 3"] + T.TOTAL]))
    pages = [Image.new("RGB", (2480, 3508), "white") for _ in range(3)]  # scanned A4 at 300 dpi
    for k, p in enumerate(pages):
        ImageDraw.Draw(p).text((100, 100 + rnd.randint(0, 9)), f"page {k}", fill="black")
    buf = io.BytesIO()
    pages[0].save(buf, "PDF", save_all=True, append_images=pages[1:], resolution=300)
    (tmp / "scan.pdf").write_bytes(buf.getvalue())
    return {"photo": str(tmp / "photo.jpg"), "pdf": str(tmp / "pdf.pdf"), "scan": str(tmp / "scan.pdf")}


def main() -> int:
    import httpx
    if not Path("/proc/self/status").exists():
        print("Linux only (reads /proc). Run it in WSL, a Linux VM or a container.")
        return 1
    tmp = Path(tempfile.mkdtemp())
    (tmp / "harness_app.py").write_text(APP, encoding="utf-8")
    env = os.environ | {"HISAAB_ROOT": str(ROOT), "HISAAB_MEDIA": json.dumps(media(tmp)), "PYTHONPATH": str(tmp)}
    port = "8799"
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "harness_app:app", "--port", port, "--log-level",
                             "warning"], env=env, cwd=tmp)
    url, ids = f"http://127.0.0.1:{port}", itertools.count(1)

    def mem():
        s = Path(f"/proc/{proc.pid}/status").read_text()
        return [int(next(l for l in s.splitlines() if l.startswith(k)).split()[1]) // 1024 for k in ("VmRSS:", "VmHWM:")]

    def post(sender, kind, **x):
        msg = {"id": f"wamid.in{next(ids)}", "from": sender, "type": kind} | x
        return httpx.post(url + "/webhook", json={"entry": [{"changes": [{"value": {"messages": [msg]}}]}]},
                          timeout=60).status_code
    try:
        for _ in range(60):
            try:
                httpx.get(url + "/")
                break
            except httpx.HTTPError:
                time.sleep(0.5)
        print(f"started: {mem()[0]} MB  (MALLOC_ARENA_MAX={os.getenv('MALLOC_ARENA_MAX', 'not set')})")
        shops = [f"91000000{k:04d}" for k in range(5)]  # fake numbers  # secret-scan: ignore
        for s in shops:
            post(s, "text", text={"body": "hi"})
            post(s, "interactive", interactive={"type": "button_reply", "button_reply": {"id": "consent_yes", "title": "x"}})
            post(s, "text", text={"body": "Gupta General Store"})
        for k in range(8):  # one shop, one message at a time
            post(shops[0], "image", image={"id": f"photo#{k}"})
            post(shops[0], "document", document={"id": "scan" if k % 2 else "pdf", "mime_type": "application/pdf"})
            time.sleep(1.5)
        time.sleep(3)
        one = mem()[1]
        print(f"one shop, one message at a time: peak {one} MB")
        codes = []
        with cf.ThreadPoolExecutor(10) as pool:
            for rnd in range(3):
                jobs = [pool.submit(post, s, "image", image={"id": f"photo#{rnd}{s}"}) for s in shops]
                jobs += [pool.submit(post, s, "document", document={"id": d, "mime_type": "application/pdf"})
                         for s in shops for d in ("pdf", "scan")]
                jobs += [pool.submit(post, s, "text", text={"body": t}) for s in shops
                         for t in ("sabse zyada baaki kiska hai", "verma ko 2000 diye")]
                codes += [j.result() for j in jobs]
                time.sleep(5)
        rss, peak = mem()
        print(f"bursts of 25 messages from 5 shops at once: peak {peak} MB (now {rss} MB); "
              f"HTTP 200 for {codes.count(200)} of {len(codes)}")
        print(f"VERDICT: peak {peak} MB of Render free's 512 MB -> {'OK' if peak < 450 else 'TOO CLOSE'}")
    finally:
        proc.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())

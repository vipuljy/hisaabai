"""One-click database setup for Supabase (or any Postgres). Safe to run again: it never deletes your data.

  python -m bot.setup_db        (or double-click RUN_setup_database.bat)

1. Loads db/schema.sql (tables, views, row-level security). Every statement is "create if not exists",
   so re-running only adds what is new.
2. Smoke test: creates a throwaway shop ZZTEST, a bill, a payment, checks the dues maths, then deletes
   the shop and everything it made.
3. If SUPABASE_URL and SUPABASE_SERVICE_KEY are set: creates the PRIVATE photo bucket, uploads a tiny
   test file, reads it back, deletes it.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_CODE = "ZZTEST"
TEST_WA = "000000000000"  # not a real number


def load_schema(url: str, schema_path: Path = ROOT / "db" / "schema.sql") -> None:
    import psycopg

    with psycopg.connect(url, autocommit=True, prepare_threshold=None, connect_timeout=15) as conn:
        conn.execute(schema_path.read_text(encoding="utf-8"))


def smoke_test(store) -> list[str]:
    """Runs the real store code end to end, then removes every trace. Returns problems (empty = fine)."""
    from datetime import datetime, timedelta, timezone

    problems = []
    store.delete_shop(TEST_CODE)  # leftover from an interrupted run
    try:
        shop = store.create_shop(TEST_WA, shop_code=TEST_CODE, segment="other", status="active",
                                 shop_name="Setup Test Store")
        supplier = store.find_or_create_supplier(shop["id"], "Setup Test Traders")
        bill = store.create_bill(shop["id"], {"supplier_name": "Setup Test Traders", "total_amount": 1000}, {})
        store.confirm_bill(bill["id"], supplier["id"])
        store.add_payment(shop["id"], supplier["id"], 400, "text")
        dues = store.dues(shop["id"])
        if len(dues) != 1 or int(dues[0]["due"]) != 600:
            problems.append(f"dues maths wrong: {[int(d['due']) for d in dues]} (expected [600])")
        store.log_event(shop["id"], "setup_check", {})
        since = datetime.now(timezone.utc) - timedelta(minutes=5)
        if store.count_events("setup_check", since, shop["id"]) != 1:
            problems.append("events not counted")
    finally:
        store.delete_shop(TEST_CODE)
    if store.get_shop_by_wa(TEST_WA) is not None:
        problems.append("test shop not deleted")
    return problems


def photo_test(photos) -> str:
    state = photos.ensure_bucket()
    if not photos.bucket_is_private():
        raise RuntimeError("bucket is PUBLIC. Supabase > Storage > bill-photos > Edit > turn Public off.")
    ref = photos.save(TEST_CODE, b"hisaabai-setup-check")
    if photos.get(ref) != b"hisaabai-setup-check":
        raise RuntimeError("uploaded file came back different")
    photos.delete_shop(TEST_CODE)
    return state


def main(out=print) -> int:
    from dotenv import load_dotenv
    import os

    load_dotenv(ROOT / ".env")
    url = os.getenv("DATABASE_URL", "").strip()
    if not url or "paste" in url:
        out("FAIL  DATABASE_URL is empty in .env. Supabase > Connect > Session pooler > URI (put your password in).")
        return 1
    if "@db." in url and "supabase.co" in url:
        out("WARN  This looks like the direct connection; if it fails, use the Session pooler URI instead.")

    try:
        load_schema(url)
        out("OK    Schema loaded (tables, views, row-level security)")
    except Exception as err:
        out(f"FAIL  Could not load the schema ({type(err).__name__}). Run RUN_check_setup.bat to see why.")
        return 1

    from bot.store import PgStore
    problems = smoke_test(PgStore(url))
    if problems:
        out("FAIL  Smoke test: " + "; ".join(problems))
        return 1
    out("OK    Smoke test: shop, bill, payment and dues work; test data removed")

    from bot.photos import SupabasePhotos, photos_from_env
    photos = photos_from_env()
    if isinstance(photos, SupabasePhotos):
        try:
            state = photo_test(photos)
            out(f"OK    Photo bucket '{photos.bucket}' {state}, private, upload/read/delete work")
        except Exception as err:
            out(f"FAIL  Photo bucket: {str(err)[:160]}")
            return 1
    else:
        out("SKIP  Photo bucket: SUPABASE_URL / SUPABASE_SERVICE_KEY not set (photos stay on this laptop)")

    out("\nDatabase ready. Next: RUN_check_setup.bat, then restart the bot so it uses the database.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

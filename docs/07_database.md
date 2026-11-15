# Database (Supabase / Postgres)

Schema: `db/schema.sql`. Tested on Postgres 16 on 2026-10-12 with sample data (dues, funnel, weekly activity, correction rate all checked by hand).

## Tables
| Table | One row per | Why it exists |
|---|---|---|
| shops | pilot shop | Shop code (S01...), segment, experiment group, consent and onboarding times, own shop name (sales-bill guard; asked once after the first saved entry, the WhatsApp profile name is offered but never stored), eval consent |
| suppliers | supplier per shop | Dues are grouped by supplier |
| messages | WhatsApp message in or out | Raw record; Meta's message id stops double processing |
| bills | bill photo read or bill typed / spoken | `kind`: bill / udhaar (old dues, no bill) / return (goods sent back: counts minus). AI reading (`model = 'typed'` for typed bills), `source` (photo / pdf / voice / typed), owner status (pending_confirm / confirmed / corrected / rejected = own sales bill or cancelled / deleted = removed by the owner), `credit_days`, `new_supplier` (owner tapped "naya supplier"), proof photo (`proof_image_path`, `proof_total`, `proof_model`), tokens and latency for cost, when the photo was deleted. `reason` (3 Nov): 'pair:<payment id>' on the goods-return saved with a 'maal lautaya' refund, 'sudhaar:…' on a correcting entry for an entry older than 7 days, 'purana baaki' on old dues typed before a first payment |
| bill_fields | field per bill | AI value vs owner's final value; `corrected` is computed. Feeds the eval |
| payments | payment logged | `kind`: payment / refund (money back from the supplier: counts minus). From voice or text, linked to a supplier. `status`: pending_confirm (a card waiting for the tap) / confirmed / cancelled / deleted. **Only confirmed payments count anywhere** (dues view, answers, summaries). `reason` (3 Nov): 'goods' / 'over' on a refund (why the money came back), 'sudhaar:…' on a correcting entry |
| entry_edits | change to a saved bill or payment | Field, old value, new value, via (voice / typed / button / proof), or a removal. Nothing is hard-deleted |
| events | product action | Funnel, retention and the experiment come from here |
| reminders | reminder an owner set | "Sharma ko Friday yaad dilana": supplier (optional), text, day; `sent_at` once the daily job sends it |

## Ready-made queries (views)
| View | Answers |
|---|---|
| v_supplier_dues | How much does each shop owe each supplier, and when is the next due date? Dues = bills + udhaar − returns − (payments − refunds), confirmed rows only; returns have no due date |
| v_shop_weeks | Bills (`kind='bill'` only) logged per shop per week since onboarding |
| v_funnel | Invited, onboarded, first bill, third bill, active in week 2 (`kind='bill'` only) |
| v_correction_rate | Share of bills the owner had to correct, by week |

Migration (31 Oct 2026): `db/schema.sql` adds the new columns with `add column if not exists`, replaces the two status
checks and creates `entry_edits`. Run `RUN_setup_database.bat` once on the existing Supabase project; it is safe to run
again. Old payments become `confirmed` (the column default). Tested on a copy of the old schema with data, twice.
Note: buttons that may work only once ("Bill aaya / Payment diya", proof answers) reserve a row in `messages` with
`type = 'choice'` and a synthetic id (the table's unique id makes this race-free); count inbound messages with
`type <> 'choice'`.

## Safety
- Row-level security is on for every table: the public key sees nothing; only the bot's service key reads and writes.
- Deleting a shop deletes all its rows (the 48-hour deletion promise in `06_privacy.md`).
- `wa_id` (full number) is only in `shops`, because the bot needs it to reply. Never exported.

## Setup (Phase 2, about 15 minutes)
1. Free account at supabase.com, new project (region: Mumbai).
2. Connect > Session pooler > copy the URI, put your database password in it, paste as `DATABASE_URL` in `.env` (never anywhere else).
3. For photos: Project settings > API keys: copy the project URL to `SUPABASE_URL` and the secret (or service_role) key to `SUPABASE_SERVICE_KEY`.
4. Double-click `RUN_setup_database.bat`: loads the schema, runs a test, makes the private photo bucket. Safe to re-run after any schema change.
5. Double-click `RUN_check_setup.bat`, then restart the bot. It now saves everything to Supabase.

## Pilot admin
- `python -m bot.admin add-shop S01 kirana 98XXXXXXXX` registers an invited shop (then set `PILOT_OPEN=` empty).
- `python -m bot.admin list-shops` shows codes and status, never phone numbers.
- `python -m bot.admin set-name S01 "Gupta General Store"` sets the name printed on the shop's own bills.
- `python -m bot.admin eval-consent S01 yes` keeps that shop's photos for the reading test (default: deleted after 30 days).
- `python -m bot.admin delete-shop S01` deletes a shop, all its data and its photos.

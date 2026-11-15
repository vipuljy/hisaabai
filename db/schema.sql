-- HisaabAI database (Postgres / Supabase). Run once in Supabase: SQL Editor > New query > paste > Run.
-- Design rules:
--   * Every action is an event (events table) so the funnel, retention and experiment come from SQL.
--   * The AI's reading and the owner's final answer are both kept (bill_fields) so every correction feeds the eval.
--   * Dues are calculated (v_supplier_dues), never stored, so they cannot drift.
--   * Deleting a shop deletes all its data (ON DELETE CASCADE): the 48-hour deletion promise.

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------- shops
create table if not exists shops (
    id               uuid primary key default gen_random_uuid(),
    shop_code        text unique not null,                -- S01, S02 ... used everywhere outside the DB
    wa_id            text unique not null,                -- WhatsApp number, needed to reply; never exported
    segment          text check (segment in ('kirana', 'medical', 'hardware', 'other')),
    status           text not null default 'invited'
                     check (status in ('invited', 'active', 'opted_out')),
    experiment_group text check (experiment_group in ('treatment', 'control')),
    consent_at       timestamptz,
    onboarded_at     timestamptz,                         -- first opt-in message received
    state            jsonb not null default '{}'::jsonb,  -- where the conversation is (e.g. waiting for a total)
    created_at       timestamptz not null default now()
);
alter table shops add column if not exists state jsonb not null default '{}'::jsonb;  -- for databases made before 13 Oct
alter table shops add column if not exists shop_name text;                         -- as printed on its own bills (sales-bill guard)
alter table shops add column if not exists eval_consent boolean not null default false;  -- owner agreed photos may be kept for the reading test

-- ---------------------------------------------------------------- suppliers
create table if not exists suppliers (
    id         uuid primary key default gen_random_uuid(),
    shop_id    uuid not null references shops(id) on delete cascade,
    name       text not null,                             -- as the owner knows it
    name_norm  text not null,                             -- bot.extract.norm_supplier(name), for matching
    created_at timestamptz not null default now(),
    unique (shop_id, name_norm)
);

-- ---------------------------------------------------------------- messages
create table if not exists messages (
    id            uuid primary key default gen_random_uuid(),
    shop_id       uuid references shops(id) on delete cascade,
    direction     text not null check (direction in ('in', 'out')),
    wa_message_id text unique,                            -- Meta's id; stops double processing
    type          text not null,                          -- text, image, audio, template ...
    body          text,
    media_id      text,
    created_at    timestamptz not null default now()
);

-- ---------------------------------------------------------------- bills
create table if not exists bills (
    id                uuid primary key default gen_random_uuid(),
    shop_id           uuid not null references shops(id) on delete cascade,
    supplier_id       uuid references suppliers(id) on delete set null,
    message_id        uuid references messages(id) on delete set null,
    image_path        text,                               -- storage path, private bucket
    supplier_name_raw text,
    bill_date         date,
    invoice_number    text,
    total_amount      numeric(12, 2) check (total_amount >= 0),
    due_date          date,
    status            text not null default 'pending_confirm'
                      check (status in ('pending_confirm', 'confirmed', 'corrected', 'rejected')),
    model             text,
    prompt_version    text,
    input_tokens      int,
    output_tokens     int,
    latency_ms        int,
    created_at        timestamptz not null default now(),
    confirmed_at      timestamptz,
    photo_deleted_at  timestamptz                         -- set when the 30-day clean-up removes the photo
);
alter table bills add column if not exists photo_deleted_at timestamptz;  -- for databases made before this column existed
create index if not exists bills_shop_created on bills (shop_id, created_at);
-- Voice / typed entries, proof photos, fixes (31 Oct 2026). Nothing is hard-deleted: a removed bill gets status 'deleted'.
alter table bills add column if not exists source           text;        -- photo | pdf | voice | typed (null = before 31 Oct)
alter table bills add column if not exists raw_text         text;        -- the owner's words for a voice / typed bill
alter table bills add column if not exists credit_days      int check (credit_days is null or credit_days between 0 and 365);
alter table bills add column if not exists new_supplier     boolean not null default false;  -- owner tapped "naya supplier"
alter table bills add column if not exists proof_image_path text;        -- a photo sent later as proof of a voice / typed bill
alter table bills add column if not exists proof_total      numeric(12, 2);
alter table bills add column if not exists proof_model      text;
alter table bills add column if not exists proof_deleted_at timestamptz;
alter table bills add column if not exists deleted_at       timestamptz;
-- kind (1 Nov 2026): 'bill' = a supplier's bill; 'udhaar' = money owed with no bill ("Gupta ko 3000 dena hai" ->
-- "Baaki mein jodo", old dues); 'return' = goods sent back / credit note: it LOWERS the dues. Only 'bill' counts as
-- a bill for the experiment and "kitne bill aaye".
alter table bills add column if not exists kind text not null default 'bill';
alter table bills drop constraint if exists bills_kind_check;
alter table bills add constraint bills_kind_check check (kind in ('bill', 'udhaar', 'return'));
alter table bills drop constraint if exists bills_status_check;
alter table bills add constraint bills_status_check
    check (status in ('pending_confirm', 'confirmed', 'corrected', 'rejected', 'deleted'));

-- One row per field per bill: what the AI read vs what the owner kept.
create table if not exists bill_fields (
    id          uuid primary key default gen_random_uuid(),
    bill_id     uuid not null references bills(id) on delete cascade,
    field_name  text not null check (field_name in
                ('supplier_name', 'bill_date', 'invoice_number', 'total_amount', 'due_date')),
    ai_value    text,
    final_value text,
    ai_unsure   boolean not null default false,
    corrected   boolean generated always as (ai_value is distinct from final_value) stored,
    unique (bill_id, field_name)
);

-- ---------------------------------------------------------------- payments
create table if not exists payments (
    id          uuid primary key default gen_random_uuid(),
    shop_id     uuid not null references shops(id) on delete cascade,
    supplier_id uuid references suppliers(id) on delete set null,
    amount      numeric(12, 2) not null check (amount > 0),
    paid_on     date not null default current_date,
    source      text not null check (source in ('voice', 'text')),
    raw_text    text,                                     -- the transcript or message it came from
    message_id  uuid references messages(id) on delete set null,
    created_at  timestamptz not null default now()
);
-- Payments wait for the owner's Confirm like bills do (31 Oct 2026). Rows made before stay 'confirmed' (the default).
alter table payments add column if not exists status            text not null default 'confirmed';
alter table payments add column if not exists supplier_name_raw text;    -- the name as said, until a supplier is picked
alter table payments add column if not exists new_supplier      boolean not null default false;
alter table payments add column if not exists confirmed_at      timestamptz;
alter table payments add column if not exists deleted_at        timestamptz;
-- kind (1 Nov 2026): 'payment' = the owner paid; 'refund' = the supplier gave money back ("Gupta se 500 wapas aaye"):
-- it RAISES the dues again.
alter table payments add column if not exists kind text not null default 'payment';
alter table payments drop constraint if exists payments_kind_check;
alter table payments add constraint payments_kind_check check (kind in ('payment', 'refund'));
-- why an entry exists when it is not an ordinary one (3 Nov): 'goods' / 'over' on a refund (why the money came back),
-- 'pair:<payment id>' on the goods-return saved with a 'maal lautaya, paisa wapas' refund, 'sudhaar:...' on a
-- correcting entry for an entry older than 7 days (old entries are never rewritten)
alter table payments add column if not exists reason text;
alter table bills add column if not exists reason text;
alter table payments drop constraint if exists payments_status_check;
alter table payments add constraint payments_status_check
    check (status in ('pending_confirm', 'confirmed', 'cancelled', 'deleted'));
create index if not exists payments_shop_created on payments (shop_id, created_at);

-- Every change to a saved bill or payment after it was confirmed: what, from what, to what, how.
create table if not exists entry_edits (
    id          bigint generated always as identity primary key,
    shop_id     uuid not null references shops(id) on delete cascade,
    entry_type  text not null check (entry_type in ('bill', 'payment')),
    entry_id    uuid not null,
    action      text not null check (action in ('edit', 'delete')),
    field       text,                                    -- amount, supplier, date, credit_days, invoice_number
    old_value   text,
    new_value   text,
    via         text not null check (via in ('voice', 'typed', 'button', 'proof')),
    created_at  timestamptz not null default now()
);
create index if not exists entry_edits_entry on entry_edits (entry_type, entry_id);

-- ---------------------------------------------------------------- reminders
-- Set by the owner ("Sharma ko Friday yaad dilana"); bot/jobs.py sends each one once (sent_at), to both experiment groups.
create table if not exists reminders (
    id          uuid primary key default gen_random_uuid(),
    shop_id     uuid not null references shops(id) on delete cascade,
    supplier_id uuid references suppliers(id) on delete set null,
    text        text not null,                            -- what to remind about (supplier name or the owner's words)
    remind_on   date not null,
    sent_at     timestamptz,
    created_at  timestamptz not null default now()
);
create index if not exists reminders_pending on reminders (shop_id, remind_on) where sent_at is null;

-- ---------------------------------------------------------------- events
-- Voice / typed entries (31 Oct): entry_parsed (intents, source, understood_by), payment_proposed, bill_cancelled,
-- payment_cancelled, supplier_pick_asked, supplier_picked, supplier_created, kind_asked, received_asked, future_asked,
-- fix_asked, entry_edited, entry_deleted, fix_not_found, proof_asked, proof_attached, proof_mismatch, proof_declined.
-- Names match docs: opt_in, bill_received, bill_confirmed, bill_corrected, payment_logged,
-- dues_asked, summary_sent, reminder_sent, off_topic, opt_out, send_failed.
-- Questions: question_asked (intent, understood_by = rules/llm/menu/none), question_answered (intent, via);
-- dues_all keeps dues_asked with the same props. Owner reminders: reminder_set, owner_reminder_sent,
-- reminder_cancelled (each with the shop's experiment group). reminder_sent stays the treatment-only due nudge.
create table if not exists events (
    id         bigint generated always as identity primary key,
    shop_id    uuid references shops(id) on delete cascade,
    event      text not null,
    props      jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);
create index if not exists events_event_time on events (event, created_at);
create index if not exists events_shop_time  on events (shop_id, created_at);

-- ---------------------------------------------------------------- views

-- What each shop owes each supplier right now.
create or replace view v_supplier_dues with (security_invoker = on) as
with billed as (  -- bills and udhaar add, returns take away
    select shop_id, supplier_id, sum(case when kind = 'return' then -total_amount else total_amount end) as billed,
           min(due_date) filter (where due_date >= current_date and kind <> 'return') as next_due
    from bills where status in ('confirmed', 'corrected') and supplier_id is not null
    group by shop_id, supplier_id
), paid as (      -- payments add, refunds take away
    select shop_id, supplier_id, sum(case when kind = 'refund' then -amount else amount end) as paid
    from payments where supplier_id is not null and status = 'confirmed'
    group by shop_id, supplier_id
)
select s.shop_id, s.id as supplier_id, s.name as supplier,
       coalesce(b.billed, 0) as billed,
       coalesce(p.paid, 0) as paid,
       coalesce(b.billed, 0) - coalesce(p.paid, 0) as due,
       b.next_due
from suppliers s
left join billed b on b.shop_id = s.shop_id and b.supplier_id = s.id
left join paid   p on p.shop_id = s.shop_id and p.supplier_id = s.id;

-- Bills logged per shop per week since onboarding (week 1 = days 0-6).
create or replace view v_shop_weeks with (security_invoker = on) as
select sh.id as shop_id, sh.shop_code, sh.experiment_group,
       floor(extract(epoch from (b.created_at - sh.onboarded_at)) / 604800)::int + 1 as week_no,
       count(*) as bills
from shops sh
join bills b on b.shop_id = sh.id and b.status in ('confirmed', 'corrected') and b.kind = 'bill'
where sh.onboarded_at is not null
group by 1, 2, 3, 4;

-- The pilot funnel: one row per stage. Active = 3+ bills in week 2.
create or replace view v_funnel with (security_invoker = on) as
with per_shop as (
    select sh.id,
           sh.onboarded_at is not null as onboarded,
           count(distinct b.id) filter (where b.status in ('confirmed', 'corrected') and b.kind = 'bill') as bills,
           coalesce(max(w.bills) filter (where w.week_no = 2), 0) as week2_bills
    from shops sh
    left join bills b on b.shop_id = sh.id
    left join v_shop_weeks w on w.shop_id = sh.id
    group by sh.id
)
select stage, shops from (
    select 1 as ord, 'invited' as stage, count(*) as shops from per_shop
    union all select 2, 'onboarded',         count(*) filter (where onboarded)       from per_shop
    union all select 3, 'first_bill',        count(*) filter (where bills >= 1)      from per_shop
    union all select 4, 'third_bill',        count(*) filter (where bills >= 3)      from per_shop
    union all select 5, 'active_in_week_2',  count(*) filter (where week2_bills >= 3) from per_shop
) t order by ord;

-- Live correction rate: share of confirmed bills where the owner changed at least one field.
create or replace view v_correction_rate with (security_invoker = on) as
select date_trunc('week', b.created_at)::date as week,
       count(*) as bills,
       count(*) filter (where b.status = 'corrected') as corrected,
       round(count(*) filter (where b.status = 'corrected')::numeric / nullif(count(*), 0), 3) as correction_rate
from bills b
where b.status in ('confirmed', 'corrected') and coalesce(b.model, '') <> 'typed'  -- typed bills have no AI reading
  and coalesce(b.source, '') not in ('typed', 'voice')                         -- nor do voice ones
group by 1 order by 1;

-- ---------------------------------------------------------------- security
-- The bot uses the service key (bypasses these). Anyone with the public anon key sees nothing.
alter table shops       enable row level security;
alter table suppliers   enable row level security;
alter table messages    enable row level security;
alter table bills       enable row level security;
alter table bill_fields enable row level security;
alter table payments    enable row level security;
alter table events      enable row level security;
alter table reminders   enable row level security;
alter table entry_edits enable row level security;

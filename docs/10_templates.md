# WhatsApp message templates (submit in Phase 2, by 30 Oct)

The bot can reply freely within 24 hours of an owner's message. Messages it starts itself (reminders, Sunday summary) must use templates that Meta approves first.

**Where:** WhatsApp Manager > Message templates > Create template. **Category: Utility** (not Marketing). **Language: English** (the text is Hinglish in Latin script; if Meta rejects it as not English, resubmit as Hindi and set `TEMPLATE_LANGUAGE=hi` in `.env`). Names must match exactly, because `bot/jobs.py` uses them.

## 1. `due_reminder` (4 variables)
**Body:**
```
Namaste! Yaad dilana tha: {{1}} ka bill ({{2}}) {{3}} ko due hai. Is supplier ka kul baaki abhi {{4}} hai. Payment kar diya ho toh yahan likh dijiye. Band karne ke liye STOP likhein.
```
**Sample values for Meta's review:** {{1}} Sharma Traders · {{2}} ₹4,580 · {{3}} 20 Oct 2026 · {{4}} ₹6,200

## 2. `weekly_summary` (6 variables)
**Body:**
```
Namaste! Is hafte ({{1}}) ka hisaab:
Bills: {{2}}, kul {{3}}
Payments: {{4}}
Abhi kul baaki: {{5}}
Sabse zyada baaki: {{6}}

Kabhi bhi "kitna baaki hai" likh ke hisaab dekhiye. Band karne ke liye STOP likhein.
```
**Sample values:** {{1}} 26 Oct 2026 - 1 Nov 2026 · {{2}} 7 · {{3}} ₹18,450 · {{4}} ₹9,000 · {{5}} ₹21,300 · {{6}} Sharma Traders (₹8,200)

## 3. `owner_reminder` (2 variables) — ⚠️ SUBMIT TO META (with the two above)
Sent by `bot/jobs.py` on the day an owner asked for ("Sharma ko Friday yaad dilana"). Goes to **both** experiment groups: the owner asked for it.
**Body:**
```
Namaste! Aapne aaj ke liye yaad dilane ko kaha tha: {{1}}. Abhi baaki: {{2}}. Payment kar diya ho toh yahan likh dijiye. Band karne ke liye STOP likhein.
```
**Sample values:** {{1}} Sharma Traders · {{2}} ₹12,400
({{1}} is the supplier's name, or the owner's own words, or "hisaab dekhna"; {{2}} is that supplier's baaki, or the total baaki.)

## Rules followed
- Body never starts or ends with a variable; variables are numbered in order and used once.
- Every message says how to stop (STOP), and only shops that tapped "Haan, shuru karo" receive them.
- Utility only: no offers, no promotion. Promotional wording gets a template re-categorised as Marketing, which costs more.
- Approval usually takes from minutes to a day. Check status in WhatsApp Manager before 1 Nov.

# WhatsApp message templates

The bot can reply freely within 24 hours of an owner's message. Messages it starts itself (bill reminders, the Sunday summary, reminders the owner asked for) must use templates that Meta approves first.

**The text lives in `bot/wa_templates.py`** (one place). `RUN_create_templates.bat` shows the three messages, asks, and submits them to Meta through the API (category **Utility**, language **English**: the text is Hinglish in Latin script; if Meta rejects it as not English, set `TEMPLATE_LANGUAGE=hi` in `.env` and run it again). `tests/test_wa_templates.py` checks that this page, the code and `bot/jobs.py` agree. Names must match exactly.

**Every template has the same parts** (updated 18 Nov 2026):
- a short **header** (plain text, no emoji: Meta's rule),
- the **body** (Hinglish, the numbers in bold, starts with *Namaste 🙏*),
- the **footer** `Band karne ke liye STOP likhein`,
- one **quick-reply button** `Sabka baaki dekhein`: a tap works exactly like typing *sabka kitna baaki hai* (the bot answers with every supplier's dues).

No brand name in any template, so changing the WhatsApp display name never needs new templates.

## 1. `due_reminder` (4 variables)
Sent by `bot/jobs.py` 2 days before a bill's due date, to the treatment group only (docs/09), and only while that supplier still has dues.

**Header:** Payment reminder

**Body:**
```
Namaste 🙏

*{{1}}* ka *{{2}}* ka bill *{{3}}* ko due hai.
Is supplier ka kul baaki: *{{4}}*

Payment ho gaya ho toh yahan likh dijiye kitna diya, hisaab apne aap update ho jayega.
```
**Footer:** Band karne ke liye STOP likhein · **Button:** Sabka baaki dekhein

**Sample values for Meta's review:** {{1}} Sharma Traders · {{2}} ₹4,580 · {{3}} 20 Nov 2026 · {{4}} ₹6,200

**As the owner sees it (sample values):**
```
*Payment reminder*
Namaste 🙏

*Sharma Traders* ka *₹4,580* ka bill *20 Nov 2026* ko due hai.
Is supplier ka kul baaki: *₹6,200*

Payment ho gaya ho toh yahan likh dijiye kitna diya, hisaab apne aap update ho jayega.
_Band karne ke liye STOP likhein_
[ Sabka baaki dekhein ]
```

## 2. `weekly_summary` (6 variables)
Sent on Sunday, to the treatment group only, when the shop had bills this week or still has dues. {{6}} is "koi nahi" when nobody has dues.

**Header:** Is hafte ka hisaab

**Body:**
```
Namaste 🙏 Aapka hisaab, {{1}}:

🧾 Bills: {{2}} (kul {{3}})
💸 Payments: {{4}}
📒 Abhi kul baaki: *{{5}}*
📌 Sabse zyada baaki: {{6}}

Kisi ek supplier ka hisaab chahiye toh uska naam likh ke poochhiye.
```
**Footer:** Band karne ke liye STOP likhein · **Button:** Sabka baaki dekhein

**Sample values for Meta's review:** {{1}} 16 Nov 2026 - 22 Nov 2026 · {{2}} 7 · {{3}} ₹18,450 · {{4}} ₹9,000 · {{5}} ₹21,300 · {{6}} Sharma Traders (₹8,200)

**As the owner sees it (sample values):**
```
*Is hafte ka hisaab*
Namaste 🙏 Aapka hisaab, 16 Nov 2026 - 22 Nov 2026:

🧾 Bills: 7 (kul ₹18,450)
💸 Payments: ₹9,000
📒 Abhi kul baaki: *₹21,300*
📌 Sabse zyada baaki: Sharma Traders (₹8,200)

Kisi ek supplier ka hisaab chahiye toh uska naam likh ke poochhiye.
_Band karne ke liye STOP likhein_
[ Sabka baaki dekhein ]
```

## 3. `owner_reminder` (2 variables)
Sent on the day an owner asked for ("Sharma ko Friday yaad dilana"). Goes to **both** experiment groups: the owner asked for it. {{1}} is the supplier's name, the owner's own words, or "hisaab dekhna"; {{2}} is that supplier's baaki, or the total baaki.

**Header:** Aapka reminder

**Body:**
```
Namaste 🙏 Aapne aaj ke liye yaad dilane ko kaha tha:
*{{1}}*

Abhi baaki: *{{2}}*

Payment ho gaya ho toh yahan likh dijiye kitna diya, hisaab apne aap update ho jayega.
```
**Footer:** Band karne ke liye STOP likhein · **Button:** Sabka baaki dekhein

**Sample values for Meta's review:** {{1}} Sharma Traders · {{2}} ₹12,400

**As the owner sees it (sample values):**
```
*Aapka reminder*
Namaste 🙏 Aapne aaj ke liye yaad dilane ko kaha tha:
*Sharma Traders*

Abhi baaki: *₹12,400*

Payment ho gaya ho toh yahan likh dijiye kitna diya, hisaab apne aap update ho jayega.
_Band karne ke liye STOP likhein_
[ Sabka baaki dekhein ]
```

## Rules followed
- Body never starts or ends with a variable; variables are numbered in order and used once; every variable has a sample.
- Header, footer and button are plain text (no variables, no emoji, no formatting) and within Meta's limits (60 / 60 / 25 characters).
- Every message says how to stop (STOP), and only shops that tapped "Shuru karein" receive them.
- Utility only: no offers, no promotion. Promotional wording gets a template re-categorised as Marketing (India: about ₹0.86 a message against about ₹0.115 for Utility, Oct 2026 list prices).
- Sending templates needs a payment method on the WhatsApp account in Meta (Billing). Approval usually takes from minutes to a day: `RUN_check_setup.bat` shows the status.

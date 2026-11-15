# Voice and typed entries: bills, payments, fixes, proof photos

Built 31 Oct 2026. Before this, voice notes and typed messages could only log a payment ("Sharma ko 5000 diye") or a
bill in one fixed format ("Sharma Traders 4580"). Now the owner can say or type anything a shopkeeper would, and the
bot turns it into cards the owner confirms. Updated 1 Nov 2026 after Vipul's phone test: money received, money still
to pay, old dues and goods sent back are now counted (five entry kinds), and signup asks nothing (the brand owners see is
**Vahi**). Updated 3 Nov 2026 after the second phone test: Hindi script and spelling slips, guards against the owner's
own mistakes (duplicates, unusual amounts, before → after on every card), photos read before any proof question,
dated corrections for old entries, and "mera data delete karo". Every doubt and its fix: `docs/18_guardrails_and_trust.md`.

## What the owner can do (voice or typing: the same engine)
| Owner says | The bot | Saved? |
|---|---|---|
| "Sharma Traders ka bill aaya 8000 ka, 15 din ka udhaar" | Bill card: supplier, total, date (today unless said), udhaar 15 din → due date | On "Sahi hai" |
| "Sharma ko 5000 diye", "paid 4200 to Balaji" | Payment card | On "Sahi hai" |
| "Om Steel ka bill 7000" (a supplier the shop does not have) | The card says "➕ naya supplier" | The supplier is created only on "Sahi hai" |
| "Sharma ko 2000 diye" with Sharma Traders AND Sharma Medical | List: both (with their baaki), "➕ Naya: Sharma", "✍️ Koi aur (likhunga)" | After the pick, a card |
| "Burma Medical ko..." (speech-to-text for Verma) | List: "Verma Medical?" (sounds alike), or the new name | After the pick |
| "Verma se 2000 aaye" | "Ye kya hai?" [🧾 Maal aaya] [💰 Paisa wapas mila] [Kuch nahi]; money back then asks *why*: [📦 Maal lautaya tha] (a return + a refund: the baaki stays the same) [💸 Zyada diya tha] (a refund: the baaki goes **up**) | A bill card or a refund card |
| "Gupta se 500 ka bartan liya", "Gupta se 2000 udhaar liya" | Bill card (bought from Gupta) | On "Sahi hai" |
| "शर्मा जी को 2000 दिए", "sharma ka hiasb" | Hindi script is turned into Roman letters, a slip into the word ("hiasb" → hisaab) before the rules read it; supplier names are never "corrected" | As usual |
| "Sharma ne 2000 diya" | "Paisa aapne diya, ya aapko mila?" [💸 Maine diye] [💰 Mujhe mile] ("ne" can mean *to* in Haryanvi) | A payment card or a refund card |
| "Gupta ko 5000 dena hai" | Shows the dues the bot already has for Gupta. More than that: [➕ Baaki mein jodo] [⏰ Reminder lagao] [Kuch nahi]. Already inside it: [⏰ Reminder lagao] first, then [➕ Naya udhaar hai] with a note | "Jodo" → an udhaar card; nothing is added silently (it may already be in a bill) |
| "Sharma ka 2000 purana baaki hai", "Sharma ka 1000 pichla hisab jodo", "pehle ka udhaar 4000" | Udhaar card ("📝 Udhaar jodna (bina bill)") | On "Sahi hai"; dues go up, not counted as a bill |
| "Mehta ko 2000 diye" (no dues known for Mehta) | "Pehle ka koi baaki tha?" [📝 Haan, baaki tha] [Nahi, advance hai] [Cancel]: the old dues are typed once, then the payment card | Each on its own tap |
| "Gupta ko 800 ka maal wapas kiya" | Return card ("↩️ Maal wapas (baaki kam hoga)") | On "Sahi hai"; dues go down |
| "Gupta ne 500 refund diya", "Gupta ne mujhe 500 diye" | Refund card ("💰 Paisa wapas mila (baaki badhega)") | On "Sahi hai" |
| "Gupta ko 900" (no verb) | [Bill aaya] [Payment diya] [Kuch nahi] | After the choice, a card |
| "Gupta ko 2000 diye aur Verma ka bill 3000" | "2 entry mili" + one card each (max 3 per message) | Each on its own tap |
| "8000 nahi 8500", "Balaji wala bill 15150 galat hai, 15,500 karo" | Fix card: ₹8,000 → ₹8,500 [Haan, badlo] [Nahi] | On "Haan"; kept in the edit history |
| The same fix for an entry older than 7 days | "Purani entry ka sudhaar": the old entry stays as it was; a dated correcting entry (udhaar / return / payment / refund, `reason` 'sudhaar:…') is offered [✅ Sudhaar jodo] [Rehne do] | On the tap; shown on its own line ("🔧 Sudhaar") in the supplier's hisaab |
| "last wala galat hai", "Agarwal wala payment hata do" | The entry: [Badlo] [Hata do] [Rehne do] | "Hata do" removes it (status 'deleted', never erased) |
| Any photo | Read first. Not a bill → "ye photo bill jaisi nahi lag rahi". Looks like a saved voice / typed bill (same supplier + amount, or same supplier in the last 30 min within 20%) → "Ye photo us bill ka proof lagti hai" [✅ Haan, proof hai] [➕ Naya bill hai]. Anything else → its own bill card | Proof: attached, no second bill; a different total is shown next to the owner's and the owner picks |
| "ye sharma ke bill ka proof hai" (after a photo) | The last photo becomes that bill's proof (once: a photo is never attached twice) | At once |
| The same entry sent twice (same supplier, kind and amount within 7 days, or the same bill number) | The card says "⚠️ Ye pehle se save hai?" with the earlier entry; buttons [✅ Naya hai, save] [Badlo] [🗑️ Duplicate hai]. Also checked again at the tap | Only on "Naya hai, save" |
| "mera data delete karo", "DELETE" (also after STOP) | Counts what will go, then [🗑️ Haan, sab mitao] [Nahi, rakho] | "Haan": every row and photo of the shop is deleted |
| "Gupta ko 2000 diye, total 8000" | The payment card + "Ye hissa samajh nahi aaya: total 8000" | A piece is never dropped silently |

**Five kinds of entry, one formula:** dues = bills + udhaar − returns − (payments − refunds), confirmed rows only.
Only real bills (`kind='bill'`) count as bills in the experiment, "kitne bill aaye" and the Sunday summary.

Every card shows **the baaki before → after** ("*Baaki:* ₹5,000 → *₹3,000*"), the name the owner said when the supplier
was a guess ("Sharma Traders _(aapne 'Sharma' likha)_"), and a warning line for an amount 10× the supplier's usual, a
payment above the baaki, or a date ahead.

Rules that never bend:
- **Nothing counts before the tap.** Bills and payments wait as `pending_confirm`; dues, answers, the Sunday summary,
  reminders and the experiment only see confirmed rows.
- **No supplier is created or picked silently.** A clear match is shown on the card; anything less is a list.
- **Fixes only by message for the last 7 days**; every change goes to `entry_edits` (old value, new value, via). Older
  entries are never rewritten: a dated correcting entry is added instead (what accountants do with a closed period).
- **Every button works once and only for its own shop** (unique ids in the messages table; ids are checked).
- **Voice: rules first.** Only if the rules find nothing and there is no question word, one Haiku call may fill cards
  (`VOICE_LLM=1`). Its answer is checked (known types, positive amounts under ₹1 crore, a supplier the owner actually
  said) and an amount our own number reader cannot find in the transcript gets ⚠️ on the card.

## How it is built
- `bot/numbers.py`: amounts as owners write and say them (8k, 1,20,000, 1.5 lakh, saade teen hazaar, dhai sau,
  paune paanch hazaar, saat sau assi). Number words alone are not money ("do" = give, "saath" = with).
- `bot/entries.py`: the text engine. Takes out numbers that are not money (credit periods, dates, invoice numbers,
  counts, phone numbers), splits the message into parts (each with its own amount), reads each part's type and
  supplier, spots fixes. Rules only.
- `bot/entry_flow.py`: the cards, the supplier list, fixes, proof photos (mixed into `bot/conversation.py`).
- Wording: `bot/messages.py`. Database: `db/schema.sql` (see `docs/07_database.md`).

## How well it works (all measured, all reproducible: `RUN_STEP12_entries_eval.bat`, `RUN_STEP11*.bat`)
Scores: **correct** (the right card / question), **partial** (nothing wrong shown, one more tap), **safe** (nothing
recorded, nothing wrong shown), **WRONG** (a card with the wrong type, supplier, amount or date, or a lost entry).
A WRONG card still needs the owner's tap, but it can be tapped by mistake: it is the number to watch.

| Message set | Written by | First run (headline) | After fixes |
|---|---|---|---|
| main, 79 typed | Claude, frozen **before** the engine was written | 75 correct, 1 WRONG (a scoring bug, not the bot) | 78 correct, 0 WRONG |
| blind, 70 typed | a separate Claude that never saw the code | **50 correct (71%), 10 WRONG (14%)** | 68 correct, 0 WRONG (tuned on it) |
| blind2, 80 typed | a new separate Claude, after tuning on 'blind' | **59 correct (74%), 10 WRONG (12.5%)** | 77 correct, 0 WRONG (tuned on it) |
| voice, 20 real notes | Vipul's voice; Sarvam transcripts; held out until the engine was done | 11 correct, 4 partial, 4 safe, 1 WRONG * | 13 correct, 6 partial, 1 safe, 0 WRONG |

\* Voice labels confirmed by Vipul on 1 Nov 2026 (the scores match the provisional run exactly). With the
scoring rule used before the voice run, the first run had 3 WRONG: two were "Sharma ne 2000 diya" asked as
"paid or received?". That question saves nothing, so it now scores partial (changed after seeing the voice notes,
for all sets). Honest reading: **on messages nobody had seen, about 3 in 4 got the right card first time and about 1 in
8 got a wrong card.** The patterns behind every wrong card were fixed, but the after-scores are on messages the rules
were tuned on: the pilot's real messages are the true test. Not changed by this work: the question eval (97% correct,
0 WRONG, same as before) and the bill-reading eval (identical table).

3 Nov re-run after the guardrails: same scores, except blind2 76 → 77 correct ("ne galti se zyada liye the, 500
lauta diye" is now a refund, not a fix) and two "undo_none" messages (an entry older than 7 days) now score correct for
offering a dated correction instead of "not found". Voice: unchanged (13 / 6 / 1 / 0). Questions: unchanged (97%, 0 WRONG).

Also checked: 705 tests on both stores (memory and Postgres; 549 + 157 skipped on a laptop without Postgres; 3 Nov, after the guardrails), earlier 652 (1 Nov), 320,000 random messages parsed with no crash and no
amount, credit period or date out of range, a migration test on a copy of the old schema with data (run twice), and two
rounds of review by a separate Claude (14 findings, all fixed, each with a regression test).

## What real voice notes taught us
- "Sharma **ne** do hazaar diya" meant *to* Sharma (Haryanvi). Standard Hindi reads it as *by* Sharma. The bot now asks.
- Speech-to-text mishears: "Burma" for Verma, "Thai so" for dhai sau, "Eight." for paid. Handled: sounds-alike list,
  number aliases; an unknown verb asks [Bill aaya] [Payment diya].
- Long notes ramble; the entry is often at the very end ("...aur haan, Bansal ko chhah hazaar diya"). Read correctly.

## Recording more voice notes
`src/templates/voice_script_v2.csv`: the 20 lines already recorded plus 10 new ones (bills, a fix, undo, two entries,
a new supplier). The voice eval's fake shop (`src/11_voice_eval.py`: SUPPLIERS, RECENT) has the suppliers and recent
entries these lines refer to.

## The chat, as the owner sees it (`python scripts/entry_demo.py`, fake shop, regenerated 3 Nov)

### A new shop: the intro, an example, one tap, and the name asked only after the first entry
```

  Owner: hi
  Bot:   *Vahi 📒 supplier ka hisaab*
         Namaste 🙏 Main aapke *supplier ka hisaab* WhatsApp pe rakhta hoon.
         
         📷 *Bill ki photo* bhejiye: main padh lunga
         🎙️ *Boliye*: _Sharma ko 5000 diye_: main likh lunga
         📒 *Poochiye*: _kitna baaki hai_: turant jawab
         ⏰ Payment ki date pe *yaad dilaunga*
         
         ✅ Har entry pehle aapko dikhaunga. *Sahi hai* dabane par hi save hogi.
         🔒 Hisaab sirf aapka. Bill AI padhta hai, koi insaan nahi.
         _🔒 Photo 30 din mein delete · STOP likh ke band_
         [✅ Shuru karein]  [👀 Example dekhein]  [Abhi nahi]

  Owner: [tap: 👀 Example dekhein]
  Bot:   Aise chalta hai 👇
         
         *Aap:* _Sharma ko 5000 diye_
         
         *Main:*
         💸 *Naya payment*
         *Supplier:* Sharma Traders
         *Diye:* ₹5,000
         *Baaki:* ₹12,000 → *₹7,000*
         Sahi hai?
         
         Aap *Sahi hai* dabate hain. Bas, hisaab update ✅
         Photo bhejo toh bill padh ke yahi card aata hai.
         [✅ Shuru karein]  [Abhi nahi]

  Owner: [tap: ✅ Shuru karein]
  Bot:   ✅ Taiyaar! Bas bhejiye:
         📷 Bill ki photo
         🎙️ Voice note: _Sharma ko 5000 diye_
         ✍️ Ya likh ke: _Gupta ka bill 4580_
         
         Har entry pehle aapko dikhaunga. *Sahi hai* dabane par hi save hogi.
         📒 Hisaab dekhna ho: _kitna baaki hai_

  Owner: Sharma Traders ka bill 4580
  Bot:   🧾 *Naya bill* (likha hua)
         *Supplier:* Sharma Traders ➕ _naya supplier_
         *Total:* ₹4,580
         *Date:* aaj (28 Oct)
         *Baaki:* ₹0 → *₹4,580*
         
         Sahi hai?
         [Sahi hai ✅]  [Badlo ✏️]  [Cancel ❌]

  Owner: [tap: Sahi hai ✅]
  Bot:   ✅ Save ho gaya.
         *Sharma Traders* ka kul baaki: *₹4,580*
         
         📷 Proof ke liye bill ki photo bhej sakte hain (zaroori nahi).
  Bot:   Ek chhoti si baat 🙏 Aapki dukaan ka naam *Ramesh Kirana* hai?
         _(Taaki aapke apne bill, supplier ke bill se alag pehchaan sakoon.)_
         [Haan, sahi hai]  [✏️ Badlo]  [Skip]

  Owner: [tap: Haan, sahi hai]
  Bot:   ✅ Dukaan ka naam save: *Ramesh Kirana*
         _(Badalna ho toh likhiye: naam: sahi naam)_
```

### A voice bill with credit days, then a photo that is READ and offered as its proof
```

  Owner: 🎙️ (voice note)
  Bot:   🎙️ Maine suna: _Gupta Agency ka bill aaya 12 hazaar ka, 15 din ka udhaar_
  Bot:   🧾 *Naya bill* (voice)
         *Supplier:* Gupta Agency
         *Total:* ₹12,000
         *Date:* aaj (28 Oct)
         *Udhaar:* 15 din → due 12 Nov 2026
         *Baaki:* ₹9,000 → *₹21,000*
         
         Sahi hai?
         [Sahi hai ✅]  [Badlo ✏️]  [Cancel ❌]

  Owner: [tap: Sahi hai ✅]
  Bot:   ✅ Save ho gaya.
         *Gupta Agency* ka kul baaki: *₹21,000*
         
         📷 Proof ke liye bill ki photo bhej sakte hain (zaroori nahi).

  Owner: 📷 (bill photo)
  Bot:   📷 Bill mil gaya, padh raha hoon...
  Bot:   📷 Ye photo *Gupta Agency* ke *₹12,000* wale bill (aaj, voice se) ka *proof* lagti hai. Photo mein bhi *₹12,000* hai.
         
         Proof ki tarah jod doon? _(Naya bill nahi banega, hisaab wahi rahega)_
         [✅ Haan, proof hai]  [➕ Naya bill hai]

  Owner: [tap: ✅ Haan, proof hai]
  Bot:   ✅ Proof jod diya. Photo mein bhi ₹12,000 hai.
```

### A photo that is not a bill: read, nothing saved, no proof question
```

  Owner: Gupta Agency ka bill 900
  Bot:   🧾 *Naya bill* (likha hua)
         *Supplier:* Gupta Agency
         *Total:* ₹900
         *Date:* aaj (28 Oct)
         *Baaki:* ₹9,000 → *₹9,900*
         
         Sahi hai?
         [Sahi hai ✅]  [Badlo ✏️]  [Cancel ❌]

  Owner: [tap: Sahi hai ✅]
  Bot:   ✅ Save ho gaya.
         *Gupta Agency* ka kul baaki: *₹9,900*
         
         📷 Proof ke liye bill ki photo bhej sakte hain (zaroori nahi).

  Owner: 📷 (bill photo)
  Bot:   📷 Bill mil gaya, padh raha hoon...
  Bot:   Ye photo bill jaisi nahi lag rahi, isliye kuch save nahi kiya. Supplier ke bill ki saaf photo bhejiye.
```

### Proof said in words after tapping 'Naya bill hai'
```

  Owner: Sharma Traders ka bill 4000
  Bot:   🧾 *Naya bill* (likha hua)
         *Supplier:* Sharma Traders
         *Total:* ₹4,000
         *Date:* aaj (28 Oct)
         *Baaki:* ₹5,000 → *₹9,000*
         
         Sahi hai?
         [Sahi hai ✅]  [Badlo ✏️]  [Cancel ❌]

  Owner: [tap: Sahi hai ✅]
  Bot:   ✅ Save ho gaya.
         *Sharma Traders* ka kul baaki: *₹9,000*
         
         📷 Proof ke liye bill ki photo bhej sakte hain (zaroori nahi).

  Owner: 📷 (bill photo)
  Bot:   📷 Bill mil gaya, padh raha hoon...
  Bot:   📷 Ye photo *Sharma Traders* ke *₹4,000* wale bill (aaj, likha hua se) ka *proof* lagti hai. Photo mein bhi *₹4,000* hai.
         
         Proof ki tarah jod doon? _(Naya bill nahi banega, hisaab wahi rahega)_
         [✅ Haan, proof hai]  [➕ Naya bill hai]

  Owner: [tap: ➕ Naya bill hai]
  Bot:   Bill padh liya:
         *Supplier:* Sharma Traders
         *Total:* ₹4,000
         *Date:* aaj _(bill pe date nahi mili)_
         *Baaki:* ₹9,000 → *₹13,000*
         
         ⚠️ *Ye pehle se save hai?* aaj (28 Oct): Bill ₹4,000, likha hua se. Naya ho tabhi save kijiye.
         
         Sahi hai?
         [✅ Naya hai, save]  [Badlo ✏️]  [🗑️ Duplicate hai]

  Owner: ye sharma ke bill ka proof hai
  Bot:   ✅ Proof jod diya. Photo mein bhi ₹4,000 hai.
```

### Hindi script and a spelling slip
```

  Owner: गुप्ता को 2000 दिए
  Bot:   💸 *Naya payment* (likha hua)
         *Supplier:* Gupta Agency _(aapne 'Gupta' likha)_
         *Diye:* ₹2,000
         *Date:* aaj (28 Oct)
         *Baaki:* ₹9,000 → *₹7,000*
         
         Sahi hai?
         [Sahi hai ✅]  [Badlo ✏️]  [Cancel ❌]

  Owner: [tap: Sahi hai ✅]
  Bot:   ✅ Payment save ho gaya.
         *Gupta Agency* ka baaki ab: *₹7,000*

  Owner: gupta ka hiasb
  Bot:   📒 *Gupta Agency*: *₹7,000* baaki
         🧾 2 bill: +₹9,000
         💸 1 payment: −₹2,000
         _(10–28 Oct)_
```

### The same payment sent twice: the second card says so
```

  Owner: Gupta ko 1000 diye
  Bot:   💸 *Naya payment* (likha hua)
         *Supplier:* Gupta Agency _(aapne 'Gupta' likha)_
         *Diye:* ₹1,000
         *Date:* aaj (28 Oct)
         *Baaki:* ₹9,000 → *₹8,000*
         
         Sahi hai?
         [Sahi hai ✅]  [Badlo ✏️]  [Cancel ❌]

  Owner: [tap: Sahi hai ✅]
  Bot:   ✅ Payment save ho gaya.
         *Gupta Agency* ka baaki ab: *₹8,000*

  Owner: Gupta ko 1000 diye
  Bot:   💸 *Naya payment* (likha hua)
         *Supplier:* Gupta Agency _(aapne 'Gupta' likha)_
         *Diye:* ₹1,000
         *Date:* aaj (28 Oct)
         *Baaki:* ₹8,000 → *₹7,000*
         
         ⚠️ *Ye pehle se save hai?* aaj (28 Oct): Payment ₹1,000, likha hua se. Naya ho tabhi save kijiye.
         
         Sahi hai?
         [✅ Naya hai, save]  [Badlo ✏️]  [🗑️ Duplicate hai]

  Owner: [tap: 🗑️ Duplicate hai]
  Bot:   Theek hai, duplicate save nahi kiya 👍
```

### A typed payment to a name that fits two suppliers
```

  Owner: Sharma ko 2000 diye
  Bot:   Payment ₹2,000: *Sharma* naam ke kai supplier hain. Kaunsa? 👇
         - Sharma Medical (Baaki ₹900)
         - Sharma Traders (Baaki ₹5,000)
         - ➕ Naya: Sharma (Naya supplier banega)
         - ✍️ Koi aur (likhunga) (Naam likh ke bhejiye)

  Owner: [tap: Sharma Traders]
  Bot:   💸 *Naya payment* (likha hua)
         *Supplier:* Sharma Traders _(aapne 'Sharma' likha)_
         *Diye:* ₹2,000
         *Date:* aaj (28 Oct)
         *Baaki:* ₹5,000 → *₹3,000*
         
         Sahi hai?
         [Sahi hai ✅]  [Badlo ✏️]  [Cancel ❌]

  Owner: [tap: Sahi hai ✅]
  Bot:   ✅ Payment save ho gaya.
         *Sharma Traders* ka baaki ab: *₹3,000*
```

### A first payment to a supplier the bot does not know: dues from before?
```

  Owner: Mehta Steel ko 2000 diye
  Bot:   *Mehta Steel* ka bot mein koi baaki nahi hai, isliye ye *₹2,000* *advance* dikhega.
         
         Kya bot shuru karne se *pehle ka koi baaki* tha?
         [📝 Haan, baaki tha]  [Nahi, advance hai]  [Cancel ❌]

  Owner: [tap: 📝 Haan, baaki tha]
  Bot:   Kitna purana baaki tha? Amount likhiye (jaise 12000):

  Owner: 12000
  Bot:   📝 *Mehta Steel* ka purana baaki *₹12,000* jod diya. Ab payment check kijiye 👇
  Bot:   💸 *Naya payment* (likha hua)
         *Supplier:* Mehta Steel
         *Diye:* ₹2,000
         *Date:* aaj (28 Oct)
         *Baaki:* ₹12,000 → *₹10,000*
         
         Sahi hai?
         [Sahi hai ✅]  [Badlo ✏️]  [Cancel ❌]

  Owner: [tap: Sahi hai ✅]
  Bot:   ✅ Payment save ho gaya.
         *Mehta Steel* ka baaki ab: *₹10,000*
```

### A new supplier: created only on the tap
```

  Owner: Om Steel ka bill saat hazaar ka aaya
  Bot:   🧾 *Naya bill* (likha hua)
         *Supplier:* Om Steel ➕ _naya supplier_
         *Total:* ₹7,000
         *Date:* aaj (28 Oct)
         *Baaki:* ₹0 → *₹7,000*
         
         Sahi hai?
         [Sahi hai ✅]  [Badlo ✏️]  [Cancel ❌]

  Owner: [tap: Sahi hai ✅]
  Bot:   ✅ Save ho gaya.
         *Om Steel* ka kul baaki: *₹7,000*
         
         📷 Proof ke liye bill ki photo bhej sakte hain (zaroori nahi).
```

### Bought something, said the everyday way
```

  Owner: Gupta se 500 ka bartan liya
  Bot:   🧾 *Naya bill* (likha hua)
         *Supplier:* Gupta Agency _(aapne 'Gupta' likha)_
         *Total:* ₹500
         *Date:* aaj (28 Oct)
         *Baaki:* ₹9,000 → *₹9,500*
         
         Sahi hai?
         [Sahi hai ✅]  [Badlo ✏️]  [Cancel ❌]

  Owner: [tap: Sahi hai ✅]
  Bot:   ✅ Save ho gaya.
         *Gupta Agency* ka kul baaki: *₹9,500*
         
         📷 Proof ke liye bill ki photo bhej sakte hain (zaroori nahi).
```

### Two entries in one message
```

  Owner: Gupta ko 1000 diye aur Sharma Traders ka bill 4580
  Bot:   2 entry mili. Har ek alag se check kijiye 👇
  Bot:   💸 *Naya payment* (likha hua)
         *Supplier:* Gupta Agency _(aapne 'Gupta' likha)_
         *Diye:* ₹1,000
         *Date:* aaj (28 Oct)
         *Baaki:* ₹9,000 → *₹8,000*
         
         Sahi hai?
         [Sahi hai ✅]  [Badlo ✏️]  [Cancel ❌]
  Bot:   🧾 *Naya bill* (likha hua)
         *Supplier:* Sharma Traders
         *Total:* ₹4,580
         *Date:* aaj (28 Oct)
         *Baaki:* ₹5,000 → *₹9,580*
         
         Sahi hai?
         [Sahi hai ✅]  [Badlo ✏️]  [Cancel ❌]
```

### Money that came IN: what was it, and why
```

  Owner: Gupta se 500 aaye
  Bot:   *Gupta Agency* se *₹500* aaye: ye kya hai?
         [🧾 Maal aaya]  [💰 Paisa wapas mila]  [Kuch nahi]

  Owner: [tap: 💰 Paisa wapas mila]
  Bot:   💰 *Gupta Agency* se *₹500* wapas mile. *Kyun?*
         
         📦 Maal lautaya tha: maal bhi wapas, paisa bhi: baaki wahi rahega
         💸 Zyada diya tha: galti se zyada payment hua tha: baaki utna badhega
         [📦 Maal lautaya tha]  [💸 Zyada diya tha]  [Kuch nahi]

  Owner: [tap: 📦 Maal lautaya tha]
  Bot:   💰 *Supplier se paisa wapas mila* (likha hua)
         *Supplier:* Gupta Agency
         *Mile:* ₹500
         *Date:* aaj (28 Oct)
         *Kyun:* maal lautaya tha
         *Baaki:* ₹9,000 → *₹9,000* _(baaki wahi rahega)_
         _(maal bhi wapas gaya, paisa bhi aaya: dono entry judengi)_
         
         Sahi hai?
         [Sahi hai ✅]  [Badlo ✏️]  [Cancel ❌]

  Owner: [tap: Sahi hai ✅]
  Bot:   ✅ Save ho gaya: maal wapas aur paisa wapas, dono.
         *Gupta Agency* ka baaki ab: *₹9,000*
```

### Money back because of paying too much
```

  Owner: Sharma Traders ne galti se zyada liye the, 300 wapas kiye
  Bot:   💰 *Supplier se paisa wapas mila* (likha hua)
         *Supplier:* Sharma Traders
         *Mile:* ₹300
         *Date:* aaj (28 Oct)
         *Kyun:* zyada diya tha
         *Baaki:* ₹5,000 → *₹5,300*
         
         Sahi hai?
         [Sahi hai ✅]  [Badlo ✏️]  [Cancel ❌]

  Owner: [tap: Sahi hai ✅]
  Bot:   ✅ Save ho gaya: paisa wapas mila.
         *Sharma Traders* ka baaki ab: *₹5,300*
```

### "Ne" can mean to or from: the bot asks the direction
```

  Owner: Sharma Traders ne 2000 diye
  Bot:   *Sharma Traders* — *₹2,000*: ye paisa *aapne diya*, ya *aapko mila*?
         [💸 Maine diye]  [💰 Mujhe mile]  [Kuch nahi]

  Owner: [tap: 💸 Maine diye]
  Bot:   💸 *Naya payment* (likha hua)
         *Supplier:* Sharma Traders
         *Diye:* ₹2,000
         *Date:* aaj (28 Oct)
         *Baaki:* ₹5,000 → *₹3,000*
         
         Sahi hai?
         [Sahi hai ✅]  [Badlo ✏️]  [Cancel ❌]

  Owner: [tap: Sahi hai ✅]
  Bot:   ✅ Payment save ho gaya.
         *Sharma Traders* ka baaki ab: *₹3,000*
```

### Money still to pay, more than the bot knows: added only on a tap
```

  Owner: Gupta ko 12000 dene hai
  Bot:   *Gupta Agency* ko *₹12,000* dena hai.
         Bot mein abhi *Gupta Agency* ka baaki: *₹9,000*
         
         Kya karoon?
         [➕ Baaki mein jodo]  [⏰ Reminder lagao]  [Kuch nahi]

  Owner: [tap: ➕ Baaki mein jodo]
  Bot:   📝 *Udhaar jodna* (bina bill) (likha hua)
         *Supplier:* Gupta Agency
         *Baaki mein jodna:* ₹12,000
         *Date:* aaj (28 Oct)
         *Baaki:* ₹9,000 → *₹21,000*
         
         Sahi hai?
         [Sahi hai ✅]  [Badlo ✏️]  [Cancel ❌]

  Owner: [tap: Sahi hai ✅]
  Bot:   ✅ Save ho gaya.
         *Gupta Agency* ka kul baaki: *₹21,000*

  Owner: kitna baaki hai
  Bot:   📒 *Baaki hisaab*
         Gupta Agency: ₹21,000
         Sharma Traders: ₹5,000
         Sharma Medical: ₹900
         
         *Kul baaki: ₹26,900*
         (3 supplier, sab confirm kiye hue bill aur payment, ab tak)
```

### Money still to pay, already inside the baaki: a reminder first
```

  Owner: Sharma Traders ko kal 3000 dena hai
  Bot:   *Sharma Traders* ko *₹3,000* dena hai (29 Oct (Thu)).
         Bot mein abhi *Sharma Traders* ka baaki: *₹5,000*
         _Ye shayad isi baaki mein shamil hai. Naya udhaar ho tabhi jodiye._
         
         Kya karoon?
         [⏰ Reminder lagao]  [➕ Naya udhaar hai]  [Kuch nahi]

  Owner: [tap: ⏰ Reminder lagao]
  Bot:   ✅ 29 Oct (Thu) ko yaad dilaunga: Sharma Traders: ₹3,000 dena — baaki ₹5,000
         Hatana ho toh: _menu_ > Reminder hatao
```

### Old dues with no bill, and goods sent back
```

  Owner: Sharma Traders ka 2000 pichla hisab jodo
  Bot:   📝 *Udhaar jodna* (bina bill) (likha hua)
         *Supplier:* Sharma Traders
         *Baaki mein jodna:* ₹2,000
         *Date:* aaj (28 Oct)
         *Baaki:* ₹5,000 → *₹7,000*
         
         Sahi hai?
         [Sahi hai ✅]  [Badlo ✏️]  [Cancel ❌]

  Owner: [tap: Sahi hai ✅]
  Bot:   ✅ Save ho gaya.
         *Sharma Traders* ka kul baaki: *₹7,000*

  Owner: Gupta ko 800 ka maal wapas kiya
  Bot:   ↩️ *Maal wapas* (baaki kam hoga) (likha hua)
         *Supplier:* Gupta Agency _(aapne 'Gupta' likha)_
         *Wapas maal:* ₹800
         *Date:* aaj (28 Oct)
         *Baaki:* ₹9,000 → *₹8,200*
         
         Sahi hai?
         [Sahi hai ✅]  [Badlo ✏️]  [Cancel ❌]

  Owner: [tap: Sahi hai ✅]
  Bot:   ✅ Save ho gaya.
         *Gupta Agency* ka kul baaki: *₹8,200*
```

### No verb: the owner says what it is
```

  Owner: Gupta ko 900
  Bot:   *Gupta Agency* — *₹900*. Ye kya hai?
         [Bill aaya 🧾]  [Payment diya 💸]  [Kuch nahi]

  Owner: [tap: Payment diya 💸]
  Bot:   💸 *Naya payment* (likha hua)
         *Supplier:* Gupta Agency
         *Diye:* ₹900
         *Date:* aaj (28 Oct)
         *Baaki:* ₹9,000 → *₹8,100*
         
         Sahi hai?
         [Sahi hai ✅]  [Badlo ✏️]  [Cancel ❌]

  Owner: [tap: Sahi hai ✅]
  Bot:   ✅ Payment save ho gaya.
         *Gupta Agency* ka baaki ab: *₹8,100*
```

### Fixing an amount saved by mistake
```

  Owner: Gupta ko 3200 diye
  Bot:   💸 *Naya payment* (likha hua)
         *Supplier:* Gupta Agency _(aapne 'Gupta' likha)_
         *Diye:* ₹3,200
         *Date:* aaj (28 Oct)
         *Baaki:* ₹9,000 → *₹5,800*
         
         Sahi hai?
         [Sahi hai ✅]  [Badlo ✏️]  [Cancel ❌]

  Owner: [tap: Sahi hai ✅]
  Bot:   ✅ Payment save ho gaya.
         *Gupta Agency* ka baaki ab: *₹5,800*

  Owner: 3200 nahi 2300
  Bot:   ✏️ *Badlav*
         💸 Payment: *Gupta Agency* — *₹3,200*, 28 Oct 2026
         Amount: ₹3,200 → *₹2,300*
         
         Badal doon?
         [Haan, badlo ✅]  [Nahi ❌]

  Owner: [tap: Haan, badlo ✅]
  Bot:   ✅ Badal diya.
         *Gupta Agency* ka baaki ab: *₹6,700*
```

### Removing the last entry
```

  Owner: Gupta ko 700 diye
  Bot:   💸 *Naya payment* (likha hua)
         *Supplier:* Gupta Agency _(aapne 'Gupta' likha)_
         *Diye:* ₹700
         *Date:* aaj (28 Oct)
         *Baaki:* ₹9,000 → *₹8,300*
         
         Sahi hai?
         [Sahi hai ✅]  [Badlo ✏️]  [Cancel ❌]

  Owner: [tap: Sahi hai ✅]
  Bot:   ✅ Payment save ho gaya.
         *Gupta Agency* ka baaki ab: *₹8,300*

  Owner: last wala galat hai
  Bot:   💸 Payment: *Gupta Agency* — *₹700*, 28 Oct 2026
         
         Is entry ka kya karna hai?
         [Badlo ✏️]  [Hata do 🗑️]  [Rehne do]

  Owner: [tap: Hata do 🗑️]
  Bot:   🗑️ Hata diya.
         *Gupta Agency* ka baaki ab: *₹9,000*
```

### An entry older than 7 days: never rewritten, a dated correction instead
```

  Owner: Gupta ka 6000 wala bill 5000 tha
  Bot:   🔧 *Purani entry ka sudhaar*
         🧾 Bill: *Gupta Agency* — *₹6,000*, 10 Oct 2026
         
         Ye 7 din se purani hai, isliye badli nahi jaati: purana hisaab jaisa tha waisa dikhe. Main aaj ki date se ek *sudhaar entry* jodta hoon:
         Amount: ₹6,000 → *₹5,000* (farq ₹1,000)
         *Baaki:* ₹9,000 → *₹8,000*
         
         Jod doon?
         [✅ Sudhaar jodo]  [Rehne do]

  Owner: [tap: ✅ Sudhaar jodo]
  Bot:   🔧 Sudhaar entry jud gayi (purani entry jaisi thi waisi hai).
         *Gupta Agency* ka baaki ab: *₹8,000*

  Owner: gupta agency ka hisaab
  Bot:   📒 *Gupta Agency*: *₹8,000* baaki
         🧾 2 bill: +₹9,000
         🔧 Sudhaar: −₹1,000
         _(10–28 Oct)_
```

### STOP keeps the hisaab; DELETE removes everything after one confirm tap
```

  Owner: STOP
  Bot:   ✅ Vahi band. Ab main koi message nahi bhejunga.
         
         Aapka hisaab *safe rakha hai*: wapas aane ke liye *START* likhein.
         Sab kuch hamesha ke liye mitana ho toh likhein: *DELETE*

  Owner: mera data delete karo
  Bot:   ⚠️ *Sab data hamesha ke liye mit jayega:*
         3 supplier, 4 bill, 0 payment, sab photo aur reminder.
         
         Ye wapas nahi aayega. Pakka mitaun?
         [🗑️ Haan, sab mitao]  [Nahi, rakho]

  Owner: [tap: 🗑️ Haan, sab mitao]
  Bot:   🗑️ Sab mita diya: hisaab, photo, reminder. Vahi ke paas aapka koi data nahi bacha.
         Phir se shuru karna ho toh *hi* likhiye 🙏
```

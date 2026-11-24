# Pilot kit: onboarding, check-ins, exit interview

Everything to run the pilot by hand (Phase 3): what to say at the shop, when to check in, and the exit interview
that gives the two numbers the case study needs (time per bill, willingness to pay). Track each shop in
`discovery/private/pilot_tracker.csv` (copy `discovery/templates/pilot_tracker.csv`; shop codes only outside
`private/`). Leave the printed card `discovery/vahi_card.html` with every owner.

## 1. Before the visit (5 minutes, at home)
- Double-click `RUN_add_shop.bat`: shop code (S01, S02...), type (kirana / medical / hardware / other), the owner's
  WhatsApp number (it goes only into the database), shop name if you know it. `PILOT_OPEN` stays empty, so only
  added shops can use the bot. (Same as `python -m bot.admin add-shop S01 kirana 98xxxxxxxx`.)
- **Family and friends get F codes** (F01, F02...): same bot, but they never join the experiment and never count in
  the case study (the health report shows "Real pilot shops (S codes)" apart). Real shops get S codes.
- Have the production number saved on your phone as "VahiBook" so you can share the contact.
- One printed card per shop.

## 2. At the shop (10 minutes)
Say it in your own words; the points matter, not the script.
1. **Why you are here:** "Main ek chhota WhatsApp tool test kar raha hoon: supplier ke bill aur payment ka hisaab
   apne aap. 2 hafte free, bas aapki raay chahiye."
2. **Trust first (before any photo):** "Photo ek AI program padhta hai, koi insaan nahi. 30 din mein photo delete.
   Aapka hisaab sirf aapka. Kabhi bhi STOP likh ke band; DELETE likh ke sab mit jaata hai."
3. **Let the owner do the first entry, on their own phone:** share the VahiBook contact, they send *hi*, read the intro,
   tap **Shuru karein**. Then one real thing: a bill photo from today, or a voice note "Sharma ko 5000 diye".
   They tap **Sahi hai** themselves. Do not type for them: the pilot measures their use, not yours.
4. **Show one answer:** they type *kitna baaki hai*.
5. **Set expectations:** "3 din baad ek message karunga, 2 hafte baad 10 minute baat." No promises of features.
   **And how to complain:** "Koi dikkat ho to isi chat mein *feedback* likh dijiye ya bol dijiye: seedha mujh tak
   aata hai." (It is listed first in the evening health report.)
6. Note in the tracker: date, segment, how many suppliers they buy from on credit (their estimate), and anything
   they said about trust.

After all shops are in: double-click `RUN_assign_groups.bat` once and type YES (groups never change after this;
test shops T01... stay out). `RUN_list_shops.bat` shows every shop and its group, never a number.

**If the owner skips the tour** (*baad mein*), fine: *sikhao* any time shows every feature in one message, and the
health report lists "tour not finished" shops for you to nudge on day 3.

**What the owner sees (24 Nov):** after *Shuru karein* the bot runs a 3-step tour by itself (photo → voice payment →
*kitna baaki hai*), so you only stand next to them; then it asks the shop name. Tips for the other features come
later, one after saves 3, 5, 7, 9 and 11. **Before you leave, add their old dues** (ask "kis supplier ka pehle ka
kitna baaki hai?" and let them type *Gupta ka purana baaki 12000*), so *kitna baaki hai* shows the real picture
from day 1.

Owners may type *ok* / *haan* instead of tapping *Sahi hai*: with one card open that saves it (23 Nov), so you do
not need to teach the button, only "card dekh ke *Sahi hai* bol dijiye ya daba dijiye".

## 3. Check-ins (from your own WhatsApp, not the bot)
- **Day 3:** "Namaste ji, VahiBook kaisa chal raha hai? Koi dikkat ya koi cheez samajh nahi aayi?" Write the answer
  in the tracker. If nothing was entered since onboarding, ask what got in the way (not "please use it").
- **Day 10:** same, plus fix the exit-interview time.
- **Every morning:** `RUN_pilot_health.bat` (5 minutes), then `RUN_mistakes.bat` (every message the bot did not
  understand, numbers hidden). Each new phrasing found there is a fix for that day: add it, run the tests, push.
- **Cost and speed guards (set once):** Render env `AI_CALLS_PER_SHOP_PER_DAY=30`, `AI_CALLS_PER_DAY=300`; a monthly
  spend limit in the Anthropic console; a free uptime ping (cron-job.org) to `/health` every 5 minutes so the first
  reply of the day is not 10-15 s.
- **In the health report, watch:** cards cancelled, fixes soon after a save (a wrong
  card), duplicate warnings kept as new, spelling fixes that look wrong, slow replies, shops silent for 3+ days.
- **Day 7 rule:** fewer than 8 shops active one week after the last onboarding → stop adding features; fix
  onboarding first (`docs/02_plan.md`).

## 4. Exit interview (day 14, 10 minutes, in person or a call)
Ask in this order; write the answers in the tracker as said.

| # | Question | What it measures |
|---|---|---|
| 1 | "Pehle ek supplier bill ka hisaab rakhne mein kitna time lagta tha? Aakhri baar kab aur kitna?" | Minutes per bill before (anchor on the last real bill, not a guess) |
| 2 | "VahiBook se ek bill mein kitna time lagta hai?" Then ask them to do one now while you watch the clock | Minutes per bill with VahiBook (observed, not only said) |
| 3 | "Pichhle 2 hafte mein kya kya kiya VahiBook se?" | Real use (compare with the health report) |
| 4 | "Kabhi VahiBook ne kuch galat dikhaya? Aapne kaise pakda?" | Wrong cards the owner saw, and whether the card design caught them |
| 5 | "Kis cheez par sabse zyada bharosa hua? Kis par sabse kam?" | Trust drivers |
| 6 | "Agar kal se VahiBook band ho jaye, toh kaisa lagega? Bahut bura / thoda bura / farak nahi" | Must-have test (share "bahut bura") |
| 7 | "Agar ye ₹99 mahine ka ho, toh lenge?" Yes → "₹199?" Yes → "₹299?" Stop at the first no | Willingness to pay (the highest yes) |
| 8 | "Kaun aur dukaandaar isse use karega? Kya aap unka number denge?" | Referral (a real yes gives a number) |
| 9 | "Ek cheez badalni ho toh kya badlenge?" | The next feature, in their words |
| 10 | "Aapki baat bina naam ke case study mein likh sakta hoon?" | Consent to quote (no name, no number) |

Rules: never argue with an answer; ask "aur?" once after each; write their words, not your summary. A polite yes is
not a yes: for question 7, the strong signal is "abhi de doon?". Question 7 is the Rs 99 test: write down weak yes /
strong yes (pays now or asks to) and the highest price said yes to; the go / no-go rule is in `docs/09_experiment.md`
(note of 21 Nov). If an owner pays, VahiBook runs for that shop for the paid month, or the money goes back the same day.

## 5. What goes in the case study
- Minutes per bill before vs with VahiBook: the median of the shops, with the count (e.g. "8 shops").
- Must-have share (question 6) and the price ladder (question 7): counts, not percentages, below 20 shops.
- The experiment readout: `python src/02_experiment_readout.py`, reported exactly as pre-registered (`docs/09_experiment.md`).
- Quotes only with consent (question 10), never with a shop's name.

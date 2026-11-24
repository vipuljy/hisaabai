"""Every message the bot sends to shop owners, in one place (the bot's copy deck).
Edit wording here; the logic in conversation.py never hard-codes text.
WhatsApp formatting: *bold*, _italic_. Button titles must be 20 characters or less.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def inr(amount) -> str:
    """4580 -> '₹4,580'; 180000 -> '₹1,80,000' (Indian grouping)."""
    n = int(Decimal(str(amount)).quantize(Decimal("1")))
    sign, n = ("-" if n < 0 else ""), abs(n)
    s = str(n)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups + [tail])
    return f"{sign}₹{s}"



def due_line(supplier: str, due, word: str = "baaki ab") -> str:
    """'*Gupta* ka baaki ab: *₹700*'. Paid more than owed: '*Gupta* ko *₹200* zyada diye hain (advance)', never '-₹200'."""
    if Decimal(str(due or 0)) < 0:
        return f"*{supplier}* ko *{inr(-Decimal(str(due)))}* zyada diye hain (advance)"
    return f"*{supplier}* ka {word}: *{inr(due)}*"

def nice_date(value) -> str:
    if value in (None, ""):
        return "—"
    d = value if isinstance(value, date) else date.fromisoformat(str(value))
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


FIELD_LABELS = {
    "supplier_name": "Supplier",
    "total_amount": "Total",
    "bill_date": "Date",
    "invoice_number": "Invoice",
    "due_date": "Due",
}

# ---------------------------------------------------------------- onboarding
BRAND = "VahiBook"  # the name owners see (20 Nov: Vahi -> VahiBook, from vahi-khata, the ledger book). Repo: HisaabAI.
NOT_IN_PILOT = (f"Namaste 🙏 *{BRAND}* abhi kuch chuni hui dukaanon ke saath pilot mein hai. "
                "Aapki dilchaspi ke liye shukriya.")
# The first message a new number gets (whatever it sent: 'hi', a photo, a voice note). One screen: what it does in
# four lines, the one promise that builds trust (nothing is saved without your tap), privacy in the footer.
# 24 Nov (Vipul): modern and aspirational, never "udhaar ka hisaab" or "munshi". What it is, why it helps, the three
# ways in, the card promise and privacy. For an owner who has never used a bot: no jargon, one example each.
WELCOME_HEADER = f"{BRAND} 📒 dukaan ka smart hisaab"
WELCOME = (f"Namaste 🙏 Main *{BRAND}* hoon: aapki dukaan ka *smart hisaab*, isi WhatsApp pe.\n\n"
           "Supplier ke *bill*, unko kiye *payment*, aur *kisko kitna dena hai*: sab ek jagah, apne aap. "
           "Na register, na calculator, na bhool.\n"
           "✨ Bade business jaisa pakka hisaab, ab aapki dukaan mein bhi.\n\n"
           "*Jo aasaan lage, waise bataiye:*\n"
           "📷 Bill ki *photo* bhejiye\n"
           "🎙️ *Bol dijiye*: _Sharma ko 5000 diye_\n"
           "✍️ Ya *likh dijiye*: _Gupta ka bill 4580_\n"
           "📒 Phir kabhi bhi poochiye: _kitna baaki hai_\n\n"
           "✅ Har entry pehle *card* pe dikhaunga. Sahi ho toh *Sahi hai* dabaiye ya _ok_ likhiye.\n"
           "🔒 Hisaab sirf aapka. Bill AI padhta hai, koi insaan nahi.")
WELCOME_FOOTER = "Photo 30 din mein delete · STOP likh ke band"
BTN_CONSENT_YES = "✅ Shuru karein"
BTN_CONSENT_EXAMPLE = "👀 Example dekhein"
BTN_CONSENT_NO = "Abhi nahi"
EXAMPLE = ("Aise chalta hai 👇\n\n"
           "*Aap:* _Sharma ko 5000 diye_\n\n"
           "*Main:*\n💸 *Naya payment*\n*Supplier:* Sharma Traders\n*Diye:* ₹5,000\n"
           "*Baaki:* ₹12,000 → *₹7,000*\nSahi hai?\n\n"
           "Aap *Sahi hai* dabate hain. Bas, hisaab update ✅\n"
           "Photo bhejo toh bill padh ke yahi card aata hai.")
CONSENT_DECLINED = "Theek hai 🙏 Jab chahein, *START* likh dijiye."
CONSENT_NUDGE = "Shuru karne ke liye neeche *Shuru karein* dabaiye 👇"

MENU = ("Aap ye bhej sakte hain:\n"
        "📷 Bill ki *photo*\n"
        "🎙️ *Voice note* ya ✍️ message: _Sharma ko 5000 diye_, _Gupta ka bill 4580_\n"
        "✏️ Galti sudharne: _8000 nahi 8500_, _last wala galat hai_\n"
        "📒 Hisaab: _kitna baaki hai_\n"
        "💬 Koi dikkat ya sujhav: _feedback_ likhiye")
READY = ("✅ Taiyaar! Bas bhejiye:\n"
         "📷 Bill ki photo\n"
         "🎙️ Voice note: _Sharma ko 5000 diye_\n"
         "✍️ Ya likh ke: _Gupta ka bill 4580_\n\n"
         "Har entry pehle aapko dikhaunga. *Sahi hai* dabane par hi save hogi.\n"
         "📒 Hisaab dekhna ho: _kitna baaki hai_")
# 24 Nov: the first-day tour. After 'Shuru', the owner tries each way in once (photo, voice, a question), so no
# shop stays on photos only without knowing the rest. Live bot and simulator only (Bot(coach=True)).
# 24 Nov: each step on its own, so a lost owner can be shown the step he is on again
# 24 Nov (night): "Step", not "kadam" (owners did not know the word); each step says in plain words what a bill and a
# payment are and what happens to the baaki; steps 1 and 2 say a card comes and *Sahi hai* opens the next step.
# 25 Nov: the tour is a PRACTICE ROUND. Examples use the supplier "Demo", whose entries are removed by themselves at
# the end; anything else saved in the tour (a real bill photo) is kept or removed by one tap. Then a clear line:
# from now on, what you send and confirm is saved for real.
PRACTICE_SUPPLIER = "Demo"
TOUR_STEP = {1: "*Step 1 / 3: Supplier ka bill* 🧾\n"
                "Supplier se maal aata hai to bill aata hai. Practice ke liye likhiye: _Demo ka bill 500_\n"
                "(Ya kisi asli bill ki *photo* bhejiye.)\n"
                "👉 Card aayega: *Sahi hai* dabaiye, phir Step 2.",
             2: "*Step 2 / 3: Supplier ko payment* 💸\n"
                "Supplier ko paisa diya? Mic 🎤 dabake *bol dijiye*: _Demo ko 200 diye_\n"
                "(Ya likh dijiye.) Uska baaki apne aap kam ho jayega.\n"
                "👉 Card aayega: *Sahi hai* dabaiye, phir Step 3.",
             3: "*Step 3 / 3: Pura hisaab* 📒\n"
                "Likhiye ya boliye: _kitna baaki hai_\n"
                "Har supplier ko kitna dena hai, sab ek saath dikhega."}
TOUR_SKIP_HINT = "_Abhi nahi? Likhiye: baad mein_"
TOUR_1 = ("✅ Shuru karte hain! 🧪 *Practice round*: 3 chhote steps, 2 minute.\n"
          "_Practice ki entries end mein hat jayengi; asli hisaab uske baad shuru hoga._\n\n"
          + TOUR_STEP[1] + "\n\n" + TOUR_SKIP_HINT)
TOUR_2 = "👏 Bahut badhiya! Step 1 ho gaya.\n\n" + TOUR_STEP[2]
TOUR_3 = "👏 Shabaash! Ab sabse kaam ki cheez.\n\n" + TOUR_STEP[3]
REAL_START = ("✅ *Ab se asli hisaab shuru.* Jo bhi bill ya payment bhejenge aur *Sahi hai* dabayenge, "
              "wo aapke hisaab mein save hoga. Galti ho to: _last wala galat hai_")
def practice_summary(removed: int, kept: int) -> str:
    """The end of the tour, in one honest line: what was removed, what was kept."""
    parts = []
    if removed:
        parts.append(f"🧹 Practice ki {removed} entry hata di.")
    if kept:
        parts.append(f"📌 {kept} entry aapke hisaab mein rakhi.")
    elif removed:
        parts.append("Aapka hisaab ab khaali hai: asli shuruaat yahin se.")
    return " ".join(parts)


def practice_keep_ask(lines: list[str], demo_removed: int = 0) -> str:
    shown = "\n".join(f"• {x}" for x in lines[:5]) + ("\n• ..." if len(lines) > 5 else "")
    head = "🧪 Practice khatam!"
    if demo_removed:
        head += f" *Demo* wali {demo_removed} entry hata di."
    return (f"{head}\nTour mein ye entry bhi save hui:\n{shown}\n\n"
            "Ye *asli* hain (rakhni hain) ya sirf *practice* thi (hata dein)?")


BTN_PRACTICE_DEL = "🗑️ Hata do"
BTN_PRACTICE_KEEP = "✅ Rakho, asli hai"
TOUR_REMIND = "👋 Chaliye, wahin se aage badhte hain.\n\n{step}\n\n" + TOUR_SKIP_HINT
TOUR_DONE = ("🎉 Bas! Aap taiyaar hain. Ab hisaab apne aap chalega.\n\n"
             "Kaam ki 3 baatein:\n"
             "✏️ Galti ho jaye: _last wala galat hai_ ya _5000 nahi 5500_\n"
             "📝 Purana baaki: _Gupta ka purana baaki 12000_\n"
             "⏰ Yaad dilana: _Sharma ko Friday yaad dilana_\n\n"
             "Sab options: _menu_ · Phir se seekhna: _sikhao_ · Koi dikkat: _feedback_")
# the owner saved again instead of asking at step 3: the question is the one thing he must not miss
TOUR_DONE_NO_QUESTION = "📒 Jab chahein likhiye _kitna baaki hai_: sab supplier ka baaki ek saath.\n\n" + TOUR_DONE
TOUR_SKIPPED = ("👍 Theek hai! Seedha kaam shuru kijiye: bill ki *photo* bhejiye, ya bol ke / likh ke bataiye.\n\n"
                "Jab mann ho, likhiye _sikhao_: sab kuch 1 message mein.")
# 'sikhao' / 'kaise use kare': everything the owner can do, in one message (any time, tour or not)
GUIDE = ("📒 *VahiBook se kya kya kar sakte hain*\n\n"
         "*Bhejiye* (jo aasaan lage):\n"
         "📷 Bill ki *photo* ya PDF\n"
         "🎙️ *Bol ke*: _Sharma ko 5000 diye_\n"
         "✍️ *Likh ke*: _Gupta ka bill 4580_\n"
         "📝 Purana baaki: _Gupta ka purana baaki 12000_\n"
         "↩️ Maal wapas: _Gupta ne 300 ka maal wapas liya_\n\n"
         "*Poochiye*:\n"
         "💰 _kitna baaki hai_ · _Sharma ka kitna baaki_\n"
         "📅 _aaj ka hisaab_ · _is hafte kitna diya_\n"
         "⏰ _Sharma ko Friday yaad dilana_\n\n"
         "*Galti ho jaye*: _last wala galat hai_ ya _5000 nahi 5500_\n"
         "Koi dikkat: _feedback_ · Sab options: _menu_")
# One tip at a time, added under a save after the tour (saves 3, 5, 7, 9, 11; at most one a day)
TIPS = ("💡 *Tip:* galti ho jaye toh likhiye _last wala galat hai_",
        "💡 *Tip:* ek supplier ka poora hisaab: _Sharma ka kitna baaki_",
        "💡 *Tip:* yaad dilana ho toh: _Sharma ko Friday yaad dilana_",
        "💡 *Tip:* maal wapas kiya? Likhiye: _Gupta ne 300 ka maal wapas liya_",
        "💡 *Tip:* is hafte ka hisaab: _is hafte kitna diya_")
TIP_AT_SAVES = (3, 5, 7, 9, 11)
ANSWER_THE_BUTTONS = "👆 Upar wale sawaal ka jawab *button dabake* dijiye."
WELCOME_DONE = READY        # no question at signup any more (1 Nov): the shop's name is asked after the first bill
WELCOME_DONE_NAMED = READY
SKIP_WORDS = {"skip", "nahi", "nahin", "baad mein", "baad me", "no", "chhodo"}

# the shop's own name: asked ONCE, after the first bill is saved (it tells the shop's own sales bills apart)
NAME_WHY = "\n_(Taaki aapke apne bill, supplier ke bill se alag pehchaan sakoon.)_"
BTN_SN_YES = "Haan, sahi hai"
BTN_SN_EDIT = "✏️ Badlo"
BTN_SN_SKIP = "Skip"
BTN_SN_TYPE = "✏️ Naam likhein"
ASK_SHOP_NAME = "Ek chhoti si baat 🙏 Apni *dukaan ka naam* save kar lein?" + NAME_WHY
ASK_SHOP_NAME_TYPE = "Dukaan ka sahi naam likhiye, jaise _Gupta General Store_:"
SHOP_NAME_SKIPPED = "Theek hai 👍 Kabhi bhi likh sakte hain: _naam: Gupta General Store_"


def shop_name_check(name: str) -> str:
    return f"Ek chhoti si baat 🙏 Aapki dukaan ka naam *{name}* hai?" + NAME_WHY


def shop_name_known(name: str) -> str:
    return f"Dukaan ka naam pehle se save hai: *{name}*\n_(Badalna ho toh likhiye: naam: sahi naam)_"


def shop_name_saved(name: str) -> str:
    return f"✅ Dukaan ka naam save: *{name}*\n_(Badalna ho toh likhiye: naam: sahi naam)_"


# ---------------------------------------------------------------- limits (cost and abuse guards)
SHOP_DAILY_LIMIT = ("Aaj ke liye kaafi bill aa gaye 🙏 Baaki kal bhej dijiye, ya abhi likh ke bhejiye: "
                    "_Sharma Traders 4580_")
BUSY = "Abhi system pe bahut kaam hai. Thodi der baad photo bhejiye, ya likh ke bhejiye: _Sharma Traders 4580_"
PHOTO_TOO_BIG = "Photo bahut badi hai. Normal camera photo bhejiye (document ki tarah nahi)."
OPTED_OUT = (f"✅ {BRAND} band. Ab main koi message nahi bhejunga.\n\n"
             "Aapka hisaab *safe rakha hai*: wapas aane ke liye *START* likhein.\n"
             "Sab kuch hamesha ke liye mitana ho toh likhein: *DELETE*")
OPTED_OUT_HINT = f"{BRAND} abhi band hai. Wapas shuru: *START*. Sab data mitana: *DELETE*"
BTN_DELETE_YES = "🗑️ Haan, sab mitao"
BTN_DELETE_NO = "Nahi, rakho"
DELETE_KEPT = "Theek hai, kuch nahi mitaya. Aapka hisaab safe hai 🙏"
DELETED_ALL = (f"🗑️ Sab mita diya: hisaab, photo, reminder. {BRAND} ke paas aapka koi data nahi bacha.\n"
               "Phir se shuru karna ho toh *hi* likhiye 🙏")


def delete_ask(counts: dict) -> str:
    what = (f"{counts.get('suppliers', 0)} supplier, {counts.get('bills', 0)} bill, {counts.get('payments', 0)} payment, "
            "sab photo aur reminder")
    return f"⚠️ *Sab data hamesha ke liye mit jayega:*\n{what}.\n\nYe wapas nahi aayega. Pakka mitaun?"
WELCOME_BACK = "Wapas swagat hai! 🙏\n\n" + MENU
# Live feedback (bot/feedback_flow.py, 20 Nov)
FB_ASK = ("🙏 Zaroor batayiye: kya dikkat aayi, ya kya behtar ho sakta hai?\n"
          "Likh dijiye ya voice note bhejiye.")
FB_THANKS = ("🙏 Shukriya! Aapki baat note kar li hai, hum ise jaldi dekhenge.\n\n"
             "Apna kaam jaari rakhiye: bill ki photo, voice note ya message.")
FB_OFFER = f"Lagta hai kuch theek se nahi hua 🙏 Kya dikkat aayi, batayenge? Isse {BRAND} behtar banega."
BTN_FB_START = "💬 Dikkat batayein"
BTN_FB_NO = "Sab theek hai"
FB_NONE = "👍 Theek hai, shukriya! Kabhi bhi *feedback* likh ke bata sakte hain."
FB_NO_REPLY = "👍 Theek hai. Kabhi bhi *feedback* likh ke bata sakte hain."
FB_ROW_TITLE = "Dikkat / sujhav batayein"
FB_ROW_DESC = "Koi dikkat ya idea: seedha hum tak"
RATE_ASK = f"Ek chhota sa sawaal 🙏 {BRAND} aapko kaisa lag raha hai?"
BTN_RATE_GOOD = "😊 Achha"
BTN_RATE_OK = "😐 Theek-thaak"
BTN_RATE_BAD = "😟 Dikkat hai"
RATE_THANKS_GOOD = "Shukriya! 🙏 Aise hi bhejte rahiye."
RATE_THANKS_OK = "Shukriya! 🙏 Kuch behtar ho sakta hai to kabhi bhi *feedback* likh ke bata dijiye."

# 23 Nov: a typed 'ok' / 'haan' with several cards open; thanks and greetings (bot/conversation.py)
TAP_THE_BUTTON = "👆 Kaunsi entry? Jo sahi hai, uske card ke neeche *Sahi hai* dabaiye."
THANKS_REPLY = "🙏"
GREET_REPLY = "Namaste 🙏 Bill ki photo bhejiye, ya poochiye: _kitna baaki hai_"
OFF_TOPIC = "Maaf kijiye, main sirf bill aur supplier ke hisaab mein madad karta hoon.\n\n" + MENU
VOICE_SOON = "Voice note jaldi samajhna shuru karunga. Abhi likh ke bhejiye, jaise: _Sharma Traders ko 5000 diye_"
VOICE_FAILED = "Voice note sun nahi paaya. Dobara bhejiye, ya likh dijiye: _Sharma Traders ko 5000 diye_"
VOICE_TOO_LONG = ("Ye voice note 2 minute se lamba hai. Chhota voice note bhejiye (ek baat ek note mein), "
                  "ya likh dijiye: _Sharma Traders ko 5000 diye_")


def voice_not_understood(transcript: str) -> str:
    return (f"🎙️ Maine suna: _{transcript[:200]}_\n\n"
            "Payment ke liye aise boliye: _Sharma Traders ko paanch hazaar diye_\n"
            "Baaki jaanne ke liye: _kitna baaki hai_")

# ---------------------------------------------------------------- bills
READING = "📷 Bill mil gaya, padh raha hoon... (10–20 second)"
READING_PDF = "📄 PDF mil gaya, padh raha hoon..."
DOC_NOT_SUPPORTED = "Ye file main nahi padh sakta. Supplier ke bill ki *photo* ya *PDF* bhejiye."
PDF_FAILED = "Ye PDF khul nahi paayi (shayad password laga hai ya file kharab hai). Bill ki photo bhejiye."
PDF_NOT_A_BILL = ("📄 Ye *quotation / rate list* lag rahi hai, bill nahi: isliye save nahi kiya.\n"
                  "Maal aa gaya ho toh uska *bill* bhejiye, ya likh dijiye: _Mehta ka bill 7176_")
PDF_TOO_BIG = "PDF bahut badi hai. Sirf bill wale page ki photo bhejiye."
READ_FAILED = "Maaf kijiye, ye bill abhi padh nahi paaya. Thodi der mein dobara photo bhejiye, roshni mein aur seedha."
NOT_A_BILL = "Ye photo bill jaisi nahi lag rahi, isliye kuch save nahi kiya. Supplier ke bill ki saaf photo bhejiye."
BTN_OK = "Sahi hai ✅"
BTN_EDIT = "Badlo ✏️"
EDIT_WHAT = "Kya badalna hai?"
BTN_FIELD = {"supplier_name": "Supplier ka naam", "total_amount": "Total amount", "bill_date": "Bill ki date"}
ASK_FIELD = {
    "supplier_name": "Supplier (jisne bill diya) ka naam likhiye:",
    "total_amount": "Bill ka *total amount* likhiye (jaise 4580):",
    "bill_date": "Bill ki date likhiye (jaise 05/10/2026, ya *aaj* / *kal*):",
}
MISSING_PREFIX = "Bill pe ye saaf nahi padh paaya. "
BAD_VALUE = {
    "supplier_name": "Naam samajh nahi aaya. Supplier ka naam likhiye:",
    "total_amount": "Amount samajh nahi aaya. Sirf number likhiye, jaise 4580:",
    "bill_date": "Date samajh nahi aayi. Aise likhiye: 05/10/2026",
}
BILL_GONE = "Ye bill ab badla nahi ja sakta. Nayi photo bhejiye."
BTN_OWN_YES = "Haan, mera bill"
BTN_OWN_NO = "Nahi, supplier ka"
OWN_BILL_SKIPPED = (f"Theek hai, ise hisaab mein nahi joda. {BRAND} abhi sirf *supplier se aaye* bills ka hisaab "
                    "rakhta hai. Supplier ke bill ki photo bhejiye.")


def own_bill_check(supplier: str) -> str:
    return (f"Is bill pe naam *{supplier}* hai, jo aapki apni dukaan ka naam lagta hai.\n\n"
            "Kya ye aapka apna bill hai (jo aapne customer ko diya)?")


def date_tap(bill: dict, old: bool = False) -> str:
    """P5: the bill date is doubtful -> one tap instead of a Sonnet call (most bills are photographed the day they come).
    The read date is shown with its own 'sahi hai' button (18 Nov phone test: only aaj / kal / doosri was offered)."""
    read = bill.get("bill_date")
    if not read:
        return "📅 Bill pe date nahi padh paaya.\n\nBill ki date aaj ki hai?"
    if old:
        return (f"📅 Bill pe date *{nice_date(read)}* padhi. Ye 6 mahine se purani hai, isliye ek baar poochh raha "
                "hoon.\n\nKya ye date sahi hai?")
    return f"📅 Bill pe date saaf nahi thi. Maine *{nice_date(read)}* padhi.\n\nKya ye date sahi hai?"


def btn_date_read(read) -> str:
    return f"✅ {nice_date(read)} sahi"   # at most 18 characters (WhatsApp's limit is 20)


BTN_DATE_TODAY = "Haan, aaj"
BTN_DATE_TODAY_ALT = "Aaj ki hai"
BTN_DATE_YDAY = "Kal"
BTN_DATE_OTHER = "Doosri date"


NO_DATE_ON_BILL = "aaj _(bill pe date nahi mili)_"


def bill_summary(bill: dict, supplier: str | None = None, new_supplier: bool = False, before=None, after=None,
                 notes: tuple = ()) -> str:
    """A photo / PDF bill before it is saved. `supplier`: the shop's supplier it was matched to (shown with the name
    printed on the bill when they differ); new_supplier: the card says a new supplier will be added."""
    unsure = set(bill.get("unsure") or []) - set(bill.get("checked") or [])  # the owner already checked these
    lines = ["Bill padh liya:"]
    read = bill.get("supplier_name_raw") or "—"
    if supplier and supplier.strip().lower() != read.strip().lower():
        shown = f"{supplier} _(bill pe: {read})_"
    else:
        shown = supplier or read
    values = {
        "supplier_name": shown + (NEW_SUPPLIER_TAG if new_supplier else ""),
        "total_amount": inr(bill["total_amount"]) if bill.get("total_amount") is not None else "—",
        "bill_date": nice_date(bill.get("bill_date")) if bill.get("bill_date") else NO_DATE_ON_BILL,
        "invoice_number": bill.get("invoice_number") or "",
        "due_date": nice_date(bill.get("due_date")),
    }
    for f, label in FIELD_LABELS.items():
        if f in ("due_date", "invoice_number") and not bill.get(f):
            continue          # an empty row is noise: shown only when the bill has it
        flag = " ⚠️" if f in unsure else ""
        lines.append(f"*{label}:* {values[f]}{flag}")
    if before is not None and after is not None:
        lines.append(due_change(before, after))
    lines += [n for n in notes if n]
    if unsure & set(FIELD_LABELS):
        lines.append("\n⚠️ = pakka nahi hoon, ek baar check kar lijiye.")
    lines.append("\nSahi hai?")
    return "\n".join(lines)


def bill_saved(supplier: str, due) -> str:
    return "✅ Save ho gaya.\n" + due_line(supplier, due, "kul baaki")


# ---------------------------------------------------------------- payments and dues
BTN_PAY_YES = "Haan, sahi ✅"
BTN_PAY_NO = "Nahi ❌"
PAY_NO_SUPPLIER = ("Supplier ka naam samajh nahi aaya. Aise likhiye: _Sharma Traders ko 5000 diye_\n"
                   "(Pehle us supplier ka ek bill bhejna zaroori hai.)")
PAY_CANCELLED = "Theek hai, payment save nahi kiya."


def pay_confirm(supplier: str, amount) -> str:
    return f"💸 *{supplier}* ko *{inr(amount)}* diye. Save karoon?"


def pay_saved(supplier: str, due) -> str:
    return "✅ Payment save ho gaya.\n" + due_line(supplier, due)


NO_DUES = "📒 Abhi koi baaki nahi. Supplier ke bill ki photo bhejiye."


def dues_list(rows: list[dict]) -> str:
    lines = ["📒 *Baaki hisaab*"]
    total = 0
    for r in rows:
        due = r["due"]
        total += due
        if due < 0:
            line = f"{r['supplier']}: advance {inr(-due)}"
        else:
            line = f"{r['supplier']}: {inr(due)}"
        if r.get("next_due") and due > 0:
            line += f" (due {nice_date(r['next_due'])})"
        lines.append(line)
    lines.append(f"\n*Kul baaki: {inr(total)}*")
    return "\n".join(lines)


# ---------------------------------------------------------------- questions about the hisaab (bot/answers.py)
# Every number here comes from a database query. Each answer says what it is based on.
WEEKDAYS_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MENU_BUTTON = "📋 Hisaab dekhein"
LIST_BTN_SUPPLIER = "Supplier chuniye"
LIST_BTN_PERIOD = "Samay chuniye"
LIST_BTN_DAY = "Din chuniye"
LIST_BTN_REMINDER = "Reminder chuniye"
INTENT_ROWS = [  # (intent, title <= 24 chars, description <= 72 chars)
    ("dues_all", "Kul baaki", "Sab suppliers ka baaki"),
    ("dues_supplier", "Ek supplier ka baaki", "Bill, payment aur baaki"),
    ("paid_period", "Kitna payment diya", "Aaj, is hafte, is mahine..."),
    ("bills_period", "Kitne bill aaye", "Kitne bill, kitne rupaye ke"),
    ("top_dues", "Sabse zyada baaki", "Kiska sabse zyada dena hai"),
    ("last_payment", "Last payment kab", "Aakhri payment kab, kitna"),
    ("set_reminder", "Yaad dilao", "Kisi din reminder set karein"),
    ("list_reminders", "Mere reminders", "Aage ke reminders dekhein"),
    ("cancel_reminder", "Reminder hatao", "Koi reminder cancel karein"),
]
INTENT_LABEL = {i: t for i, t, _ in INTENT_ROWS} | {"period_summary": "Din ka hisaab"}
PERIOD_ROWS = [("today", "Aaj"), ("yesterday", "Kal"), ("this_week", "Is hafte"), ("last_week", "Pichhle hafte"),
               ("this_month", "Is mahine"), ("last_month", "Pichhle mahine"), ("all", "Ab tak (shuru se)")]
ALL_SUPPLIERS_ROW = "Sab suppliers"
NO_SUPPLIER_ROW = "Koi supplier nahi"

ASK_SUPPLIER = "Kis supplier ka? Neeche list se chuniye 👇"
ASK_PERIOD = "Kis samay ka? Neeche list se chuniye 👇"
ASK_DAY = "Kis din yaad dilaun? Neeche list se chuniye 👇 (ya date likhiye: _28 tarikh_)"
ASK_CANCEL = "Kaunsa reminder hatana hai? 👇"
NO_SUPPLIERS = ("Abhi koi supplier nahi hai. Bill ki photo bhejiye, ya purana baaki likhiye: "
                "_Gupta ka purana baaki 12000_")
NO_REMINDERS = "⏰ Abhi koi reminder nahi.\nNaya reminder: _Sharma ko Friday yaad dilana_"
REMINDER_GONE = "Ye reminder ab nahi hai (shayad bhej diya gaya ya pehle hi hata diya)."
QUESTION_EXPIRED = "Ye sawaal purana ho gaya. Neeche se dobara chuniye 👇"


def short_date(d: date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]}"


def day_label(d: date) -> str:
    """'Fri 31 Oct'."""
    return f"{WEEKDAYS_SHORT[d.weekday()]} {short_date(d)}"


def span(start: date, end: date) -> str:
    """'20–25 Oct', '28 Sep–3 Oct', '25 Oct'."""
    if start == end:
        return short_date(end)
    if start.year != end.year:
        return f"{nice_date(start)}–{nice_date(end)}"
    if start.month == end.month:
        return f"{start.day}–{short_date(end)}"
    return f"{short_date(start)}–{short_date(end)}"


def period_label(key: str, start: date, end: date) -> str:
    """'Is hafte (20–25 Oct)'. Always names the dates, so the owner can check the basis."""
    words = {"today": "Aaj", "yesterday": "Kal", "this_week": "Is hafte", "last_week": "Pichhle hafte",
             "this_month": "Is mahine", "last_month": "Pichhle mahine"}
    if key == "all":
        return f"Ab tak ({span(start, end)})" if start.year > 2000 else "Ab tak"
    if key == "day":
        return day_label(start)
    return f"{words[key]} ({span(start, end)})"


def _ago(d: date, today: date) -> str:
    n = (today - d).days
    return "aaj" if n == 0 else "kal" if n == 1 else f"{n} din pehle"


def supplier_dues(s: dict, today: date) -> str:
    if not s["bills"] and not s["payments"]:
        return f"📒 *{s['supplier']}* ka abhi koi bill ya payment save nahi hai."
    head = (f"📒 *{s['supplier']}*: *{inr(s['due'])}* baaki" if s["due"] >= 0
            else f"📒 *{s['supplier']}*: *{inr(-s['due'])}* advance diya hua")
    if "bill_sum" not in s:   # an older summary without the breakdown
        basis = (f"({s['bills']} bill {inr(s['billed'])}, {s['payments']} payment {inr(s['paid'])}, "
                 f"{span(s['first_day'], today)})")
        return f"{head}\n{basis}"
    rows = [("🧾", f"{s['n_bill']} bill", s["bill_sum"], "+"), ("📝", "Udhaar / purana baaki", s["udhaar_sum"], "+"),
            ("↩️", "Maal wapas", s["return_sum"], "−"), ("💸", f"{s['n_payment']} payment", s["payment_sum"], "−"),
            ("💰", "Paisa wapas mila", s["refund_sum"], "+")]
    lines = [head] + [f"{icon} {label}: {sign}{inr(v)}" for icon, label, v, sign in rows if v]
    adj = Decimal(str(s.get("adjust_sum") or 0))
    if adj:
        lines.append(f"🔧 Sudhaar: {'+' if adj > 0 else '−'}{inr(abs(adj))}")
    lines.append(f"_({span(s['first_day'], today)})_")
    return "\n".join(lines)


DUES_DETAIL_HINT = ("\n\nKisi supplier ke bill aur payment dekhne ke liye *Detail dekhein* dabaiye."
                    "\nAur poochiye: _is hafte kitna diya_ · _mere reminders_")
LIST_BTN_DETAIL = "Detail dekhein"


def dues_row_note(r: dict) -> str:
    """One supplier's row in the 'Detail dekhein' list (at most 72 characters)."""
    due = r["due"]
    text = f"Advance {inr(-due)}" if due < 0 else f"Baaki {inr(due)}"
    if r.get("next_due") and due > 0:
        text += f" · due {nice_date(r['next_due'])}"
    return text[:72]


def dues_all(rows: list[dict]) -> str:
    return dues_list(rows) + f"\n({len(rows)} supplier, sab confirm kiye hue bill aur payment, ab tak)"


def _per_supplier(rows: list[dict], amount_key: str) -> list[str]:
    totals: dict[str, list] = {}
    for r in rows:
        t = totals.setdefault(r["supplier"] or "?", [0, 0])
        t[0] += r[amount_key]
        t[1] += 1
    ordered = sorted(totals.items(), key=lambda kv: -kv[1][0])
    lines = [f"• {name}: {inr(amt)} ({n})" for name, (amt, n) in ordered[:8]]
    if len(ordered) > 8:
        lines.append(f"• aur {len(ordered) - 8} supplier")
    return lines


def paid_answer(rows: list[dict], label: str, supplier: str | None) -> str:
    who = f"{supplier} ko " if supplier else ""
    if not rows:
        return f"💸 {label} {who}koi payment nahi."
    total = sum(r["amount"] for r in rows)
    if supplier:
        items = ", ".join(f"{short_date(r['paid_on'])} {inr(r['amount'])}" for r in rows[-6:])
        more = f" (+{len(rows) - 6} aur)" if len(rows) > 6 else ""
        return f"💸 {label} {who}*{inr(total)}* diye\n({len(rows)} payment: {items}{more})"
    return "\n".join([f"💸 {label} kul *{inr(total)}* diye ({len(rows)} payment)"] + _per_supplier(rows, "amount"))


def bills_answer(rows: list[dict], label: str, supplier: str | None) -> str:
    who = f"{supplier} ka " if supplier else ""
    if not rows:
        return f"🧾 {label} {who}koi bill nahi aaya."
    total = sum(r["total_amount"] for r in rows)
    lines = [f"🧾 {label} {who}*{len(rows)} bill* aaye, kul *{inr(total)}*"]
    if not supplier:
        lines += _per_supplier(rows, "total_amount")
    lines.append("(Sirf confirm kiye hue bill, bot mein aane ki date se)")
    return "\n".join(lines)


def period_summary(label: str, bills: list[dict], pays: list[dict], dues: list[dict]) -> str:
    """'aaj ka hisaab': what came in and went out in the period, then the baaki as it stands now."""
    total_due = sum((r["due"] for r in dues), 0)
    lines = [f"📒 *{label} ka hisaab*"]
    if bills:
        lines.append(f"🧾 {len(bills)} bill aaye: *{inr(sum(r['total_amount'] for r in bills))}*")
    if pays:
        lines.append(f"💸 {len(pays)} payment diye: *{inr(sum(r['amount'] for r in pays))}*")
    if not bills and not pays:
        lines.append("Koi bill ya payment nahi.")
    lines.append(f"Kul baaki ab: *{inr(total_due)}*" + (f" ({len(dues)} supplier)" if dues else ""))
    return "\n".join(lines)


def top_dues(rows: list[dict]) -> str:
    owed = [r for r in rows if r["due"] > 0]
    if not owed:
        return NO_DUES
    total = sum(r["due"] for r in owed)
    lines = [f"🔝 Sabse zyada baaki: *{owed[0]['supplier']}* — *{inr(owed[0]['due'])}*"]
    lines += [f"{i}. {r['supplier']}: {inr(r['due'])}" for i, r in enumerate(owed[1:3], start=2)]
    lines.append(f"(Kul baaki {inr(total)}, {len(owed)} supplier mein)")
    return "\n".join(lines)


def last_payment(row: dict | None, supplier: str | None, today: date) -> str:
    if row is None:
        return f"💸 {supplier} ko abhi tak koi payment save nahi hua." if supplier else "💸 Abhi tak koi payment save nahi hua."
    when = f"{nice_date(row['paid_on'])} ({_ago(row['paid_on'], today)})"
    if supplier:
        return f"💸 {supplier} ko last payment: *{inr(row['amount'])}*, {when}"
    return f"💸 Last payment: *{inr(row['amount'])}* {row['supplier']} ko, {when}"


def reminder_date(d: date) -> str:
    """'28 Oct (Wed)'."""
    return f"{short_date(d)} ({WEEKDAYS_SHORT[d.weekday()]})"


def reminder_saved(day: date, about: str, due=None) -> str:
    baaki = f" — baaki {inr(due)}" if due is not None else ""
    return (f"✅ {reminder_date(day)} ko yaad dilaunga: {about}{baaki}\n"
            "Hatana ho toh: _menu_ > Reminder hatao")


def reminder_about(supplier: str | None, note: str | None) -> str:
    """What the reminder is about, as stored and sent: 'Sharma Traders: vapas karne'."""
    if supplier and note:
        return f"{supplier}: {note}"
    return supplier or note or GENERAL_REMINDER


ASK_DAY_MORE = "Kaunsi date? 👇 (Ya likh dijiye, jaise _28 tarikh_ ya _28/10_)"
DAY_MORE_ROW = "Doosri date chuniye"


def reminders_list(rows: list[dict]) -> str:
    if not rows:
        return NO_REMINDERS
    return "\n".join(["⏰ *Aapke reminders*"] + [f"• {day_label(r['remind_on'])}: {r['text']}" for r in rows])


def reminder_cancelled(r: dict) -> str:
    return f"✅ Reminder hata diya: {day_label(r['remind_on'])}, {r['text']}."


GENERAL_REMINDER = "hisaab dekhna"


def clarify(understood: list[tuple[str, str]], problem: str = "") -> str:
    """'Kya aap ye poochh rahe hain?' + what was understood, before showing a list."""
    label = next((v for k, v in understood if k == "Sawaal"), "")
    rest = [v for k, v in understood if k != "Sawaal"]
    if not label:
        return "🤔 Pura nahi samjha. Neeche se chuniye 👇" + (f"\n{problem}" if problem else "")
    head = f"🤔 Samjha: *{label}*" + "".join(f" · {v}" for v in rest)
    return f"{head}\n{problem or 'Bas ek cheez aur, neeche se chuniye 👇'}"


def _as_typed(name: str) -> str:
    return name if any(c.isupper() for c in name) else name.title()


def supplier_not_found(typed: str) -> str:
    return f"*{_as_typed(typed)}* naam ka supplier nahi mila."


def supplier_ambiguous(typed: str, n: int) -> str:
    return f"*{_as_typed(typed)}* naam ke {n} supplier hain. Kaunsa? 👇"


# ---------------------------------------------------------------- voice / typed entries (bot/entry_flow.py)
# One card per entry. Nothing is saved until the owner taps "Sahi hai".
BTN_CANCEL = "Cancel ❌"
SOURCE_LABEL = {"voice": "voice", "typed": "likha hua", "text": "likha hua", "photo": "photo", "pdf_text": "PDF",
                "pdf_scan": "PDF", "llm": "voice"}
NEW_SUPPLIER_TAG = " ➕ _naya supplier_"
UNSURE_NOTE = "\n⚠️ = pakka nahi hoon, ek baar check kar lijiye."
ENTRY_GONE = "Ye button ab kaam nahi karta (entry pehle hi save ya cancel ho chuki hai)."


def gone(e: dict | None, today: date, old: bool = False) -> str:
    """A tap on an old card: say what happened to the entry, and what to do now."""
    if e is None:
        return ENTRY_GONE
    what = f"{ENTRY_WORD.get(e.get('kind') or e.get('type'), 'Entry')} {inr(e['amount'])}"
    label = f"*{e['supplier']}* — {what}" if e.get("supplier") else what
    status = e.get("status")
    if status in ("confirmed", "corrected"):
        if old:
            return (f"{label} ({nice_date(e.get('day'))}): ye {FIX_WINDOW_DAYS} din se purani hai, isliye badli nahi "
                    f"jaati. Sudhaar ke liye likhiye, jaise: _{int(Decimal(str(e['amount'])))} nahi <sahi amount>_")
        return f"✅ {label}: ye pehle hi save ho chuki hai. Badalna ho toh likhiye: _last wala galat hai_"
    if status == "deleted":
        return f"🗑️ {label}: ye entry hata di gayi thi."
    if status in ("cancelled", "rejected"):
        return f"{label}: ye entry cancel ho chuki thi, kuch save nahi hua."
    return ENTRY_GONE
    what = f"{ENTRY_WORD.get(e.get('kind') or e.get('type'), 'Entry')} {inr(e['amount'])}"
    who = f" ({e['supplier']})" if e.get("supplier") else ""
    status = e.get("status")
    if status in ("confirmed", "corrected"):
        if old:
            return (f"Ye entry ({what}{who}, {nice_date(e.get('day'))}) {FIX_WINDOW_DAYS} din se purani hai, isliye "
                    f"badli nahi jaati. Sudhaar ke liye likhiye, jaise: _{inr(e['amount'])[1:]} nahi <sahi amount>_")
        return f"Ye entry pehle hi save ho chuki hai ✅ ({what}{who}). Badalna ho toh likhiye: _last wala galat hai_"
    if status == "deleted":
        return f"Ye entry ({what}{who}) hata di gayi thi."
    if status in ("cancelled", "rejected"):
        return f"Ye entry ({what}{who}) cancel ho chuki thi, kuch save nahi hua."
    return ENTRY_GONE
BILL_CANCELLED = "Theek hai, ye bill save nahi kiya."
TOO_MANY = "Ek message mein 3 se zyada entry hain. 3-3 karke bhejiye 🙏"
PROOF_OFFER = "\n\n📷 Proof ke liye bill ki photo bhej sakte hain (zaroori nahi)."


def heard(transcript: str) -> str:
    return f"🎙️ Maine suna: _{transcript[:200]}_"


def entries_intro(n: int) -> str:
    return f"{n} entry mili. Har ek alag se check kijiye 👇"


def _day_word(d: date, today: date) -> str:
    n = (today - d).days
    return {0: f"aaj ({short_date(d)})", 1: f"kal ({short_date(d)})"}.get(n, nice_date(d))


BILL_CARD_TITLE = {"bill": "🧾 *Naya bill*", "udhaar": "📝 *Purana baaki jodna* (bina bill)",
                   "return": "↩️ *Maal wapas* (baaki kam hoga)"}
BILL_CARD_AMOUNT = {"bill": "Total", "udhaar": "Baaki mein jodna", "return": "Wapas maal"}


def _bal(x) -> str:
    """A baaki for the before -> after line: '₹3,000', or '₹2,000 advance' when paid more than owed."""
    x = Decimal(str(x or 0))
    return f"{inr(-x)} advance" if x < 0 else inr(x)


def due_change(before, after) -> str:
    """'*Baaki:* ₹3,000 → *₹11,000*': every card shows what tapping Sahi hai will do to the supplier's baaki."""
    same = " _(baaki wahi rahega)_" if Decimal(str(before or 0)) == Decimal(str(after or 0)) else ""
    return f"*Baaki:* {_bal(before)} → *{_bal(after)}*{same}"


def said_note(said: str | None, label: str, source: str | None) -> str:
    """The supplier was matched from a shorter or different name: show what the owner said, so a wrong guess shows."""
    if not said or not label or said.strip().lower() == label.strip().lower():
        return ""
    verb = "kaha" if source in ("voice", "llm") else "likha"
    return f" _(aapne '{said}' {verb})_"


def bill_card(bill: dict, supplier: str, today: date, new_supplier: bool = False, unsure: tuple = (),
              before=None, after=None, said: str | None = None, notes: tuple = ()) -> str:
    """A voice / typed bill (or udhaar / goods sent back), before it is saved."""
    src = SOURCE_LABEL.get(bill.get("source") or "typed", "likha hua")
    kind = bill.get("kind") or "bill"
    lines = [f"{BILL_CARD_TITLE[kind]} ({src})",
             f"*Supplier:* {supplier}{NEW_SUPPLIER_TAG if new_supplier else said_note(said, supplier, bill.get('source'))}",
             f"*{BILL_CARD_AMOUNT[kind]}:* {inr(bill['total_amount'])}{' ⚠️' if 'total_amount' in unsure else ''}",
             f"*Date:* {_day_word(_as_date(bill.get('bill_date')) or today, today)}"]
    if bill.get("credit_days"):
        due = f" → due {nice_date(bill['due_date'])}" if bill.get("due_date") else ""
        lines.append(f"*Udhaar:* {bill['credit_days']} din{due}")
    if bill.get("invoice_number"):
        lines.append(f"*Bill no:* {bill['invoice_number']}")
    if before is not None and after is not None:
        lines.append(due_change(before, after))
    lines += [n for n in notes if n]
    if unsure:
        lines.append(UNSURE_NOTE)
    lines.append("\nSahi hai?")
    return "\n".join(lines)


REFUND_WHY = {"goods": "maal lautaya tha", "over": "zyada diya tha"}


def payment_card(pay: dict, supplier: str, today: date, new_supplier: bool = False, unsure: tuple = (),
                 before=None, after=None, said: str | None = None, notes: tuple = ()) -> str:
    src = SOURCE_LABEL.get(pay.get("source") or "text", "likha hua")
    refund = pay.get("kind") == "refund"
    lines = [f"{'💰 *Supplier se paisa wapas mila*' if refund else '💸 *Naya payment*'} ({src})",
             f"*Supplier:* {supplier}{NEW_SUPPLIER_TAG if new_supplier else said_note(said, supplier, pay.get('source'))}",
             f"*{'Mile' if refund else 'Diye'}:* {inr(pay['amount'])}{' ⚠️' if 'amount' in unsure else ''}",
             f"*Date:* {_day_word(_as_date(pay.get('paid_on')) or today, today)}"]
    if refund and pay.get("reason") in REFUND_WHY:
        lines.append(f"*Kyun:* {REFUND_WHY[pay['reason']]}")
    if before is not None and after is not None:
        lines.append(due_change(before, after))
        if refund and pay.get("reason") == "goods":
            lines.append("_(maal bhi wapas gaya, paisa bhi aaya: dono entry judengi)_")
    lines += [n for n in notes if n]
    if unsure:
        lines.append(UNSURE_NOTE)
    lines.append("\nSahi hai?")
    return "\n".join(lines)


# guards on cards: a likely duplicate, an unusual amount, more than the baaki, a date ahead
BTN_DUP_SAVE = "✅ Naya hai, save"
BTN_DUP_CANCEL = "🗑️ Duplicate hai"
DUP_CANCELLED = "Theek hai, duplicate save nahi kiya 👍"


def dup_note(e: dict, today: date) -> str:
    kind = ENTRY_WORD.get(e.get("kind") or e.get("type"), "Entry")
    src = SOURCE_LABEL.get(e.get("source") or "", "")
    via = f", {src} se" if src else ""
    inv = f" (bill no {e['invoice']})" if e.get("invoice") else ""
    return (f"\n⚠️ *Ye pehle se save hai?* {_day_word(_as_date(e['day']) or today, today)}: "
            f"{kind} {inr(e['amount'])}{inv}{via}. Naya ho tabhi save kijiye.")


def warn_big(amount, usual) -> str:
    return f"⚠️ Is supplier ki entry aam taur pe ~{inr(usual)} ki hoti hai. *{inr(amount)}* sahi hai?"


def warn_advance(extra) -> str:
    return f"⚠️ Ye baaki se *{inr(extra)}* zyada hai: utna advance ho jayega."


WARN_FUTURE = "⚠️ Date aage ki hai. Ek baar check kar lijiye."


# a first payment that would show as advance: the dues from before the bot are probably missing
BTN_OLD_YES = "📝 Haan, baaki tha"
BTN_OLD_NO = "Nahi, advance hai"
ASK_OLD_AMOUNT = "Kitna purana baaki tha? Amount likhiye (jaise 12000):"


def olddue_ask(name: str, amount) -> str:
    return (f"*{name}* ka bot mein koi baaki nahi hai, isliye ye *{inr(amount)}* *advance* dikhega.\n\n"
            "Kya bot shuru karne se *pehle ka koi baaki* tha?")


def olddue_saved(name: str, amount) -> str:
    return f"📝 *{name}* ka purana baaki *{inr(amount)}* jod diya. Ab payment check kijiye 👇"


def _as_date(value):
    if value in (None, ""):
        return None
    return value if isinstance(value, date) else date.fromisoformat(str(value))


# editing a card before it is saved (list rows: id suffix, title <= 24, description)
EDIT_ROWS_BILL = [("supplier_name", "Supplier ka naam", ""), ("total_amount", "Amount", ""),
                  ("bill_date", "Bill ki date", ""), ("credit_days", "Udhaar ke din", "Kitne din mein dena hai"),
                  ("invoice_number", "Bill number", "")]
EDIT_ROWS_PAYMENT = [("supplier_name", "Supplier ka naam", ""), ("amount", "Amount", ""),
                     ("paid_on", "Payment ki date", "")]
LIST_BTN_EDIT = "Chuniye"
ASK_VALUE = {
    "supplier_name": "Supplier ka naam likhiye:",
    "total_amount": "Sahi amount likhiye (jaise 4580):",
    "amount": "Sahi amount likhiye (jaise 4580):",
    "bill_date": "Bill ki date likhiye (jaise 05/10/2026, ya _aaj_ / _kal_):",
    "paid_on": "Payment ki date likhiye (jaise 05/10/2026, ya _aaj_ / _kal_):",
    "date": "Sahi date likhiye (jaise 05/10/2026, ya _aaj_ / _kal_):",
    "credit_days": "Kitne din ka udhaar? Sirf number likhiye (jaise 15). Udhaar nahi hai toh 0 likhiye:",
    "invoice_number": "Bill number likhiye:",
}
BAD_VALUE_ANY = {
    "supplier_name": "Naam samajh nahi aaya. Supplier ka naam likhiye:",
    "total_amount": "Amount samajh nahi aaya. Sirf number likhiye, jaise 4580:",
    "amount": "Amount samajh nahi aaya. Sirf number likhiye, jaise 4580:",
    "bill_date": "Date samajh nahi aayi. Aise likhiye: 05/10/2026 (ya aaj / kal)",
    "paid_on": "Date samajh nahi aayi. Aise likhiye: 05/10/2026 (ya aaj / kal)",
    "date": "Date samajh nahi aayi. Aise likhiye: 05/10/2026 (ya aaj / kal)",
    "credit_days": "Sirf din ka number likhiye, jaise 15 (0 se 365 tak):",
    "invoice_number": "Bill number samajh nahi aaya. Dobara likhiye:",
}

# picking the supplier (a new supplier is created only when the owner taps it)
ADD_SUPPLIER_ROW = "➕ Naya: "
ADD_SUPPLIER_DESC = "Naya supplier banega"
TYPE_SUPPLIER_ROW = "✍️ Koi aur (likhunga)"
TYPE_SUPPLIER_DESC = "Naam likh ke bhejiye"
ASK_SUPPLIER_NAME = "Supplier ka naam likhiye:"


def same_name_ask(existing: str) -> str:
    """A second supplier with the same name: the place (or full name) tells them apart, now and in every message."""
    return (f"*{existing}* pehle se hai. Naye wale ki pehchaan ke liye *jagah ya poora naam* likhiye, jaise: "
            f"_{existing} Sadar_ ya _{existing} Indore_")


def pick_supplier(name: str | None, amount, kind: str, close: bool) -> str:
    what = f"{'Bill' if kind == 'b' else 'Payment'} {inr(amount)}"
    if not name:
        return f"{what}: kis supplier ka? Neeche se chuniye 👇"
    if close:
        return f"{what}: *{name}* naam ka supplier pakka nahi pehchana. Inmein se koi hai, ya naya supplier? 👇"
    return f"{what}: *{name}* naam ke kai supplier hain. Kaunsa? 👇"


# amount + name with no verb, money that came IN, money still to pay
BTN_KIND_BILL = "Bill aaya 🧾"
BTN_KIND_PAY = "Payment diya 💸"
BTN_NOTHING = "Kuch nahi"
NOTHING_SAVED = "Theek hai, kuch save nahi kiya."
BTN_RCV_YES = "Maine diye 💸"          # old buttons still work
BTN_RCV_PAY = "💸 Maine diye"
BTN_RCV_GOT = "💰 Mujhe mile"
BTN_RCV_GOODS = "🧾 Maal aaya"
BTN_RCV_MONEY = "💰 Paisa wapas mila"
RECEIVED_NO = "Theek hai, kuch save nahi kiya."
BTN_FUT_REMIND = "⏰ Reminder lagao"
BTN_FUT_PAID = "Payment ho gaya"       # old buttons still work
BTN_FUT_ADD = "➕ Baaki mein jodo"
BTN_FUT_NEW = "➕ Naya udhaar hai"


def kind_ask(name: str, amount) -> str:
    return f"*{name}* — *{inr(amount)}*. Ye kya hai?"


def received_ask(name: str, amount) -> str:
    """'Sharma ne 2000 diya' can mean either way (in Haryanvi 'ne' = to): the owner says which."""
    return f"*{name}* — *{inr(amount)}*: ye paisa *aapne diya*, ya *aapko mila*?"


def received_what(name: str, amount) -> str:
    """'Gupta se 500 aaye': goods worth 500 came (a bill), or 500 came back in cash (a refund)?"""
    return f"*{name}* se *{inr(amount)}* aaye: ye kya hai?"


def future_ask(name: str, amount, day: date | None, due=None) -> str:
    """'Gupta ko 3000 dena hai': not paid yet. It may be dues the bot does not know yet (add them) or a reminder."""
    when = f" ({reminder_date(day)})" if day else ""
    known = f"\nBot mein abhi *{name}* ka baaki: *{inr(due or 0)}*" if due is not None else \
        f"\nBot mein abhi *{name}* ka koi hisaab nahi."
    if due is not None and Decimal(str(due)) >= Decimal(str(amount)):
        known += "\n_Ye shayad isi baaki mein shamil hai. Naya udhaar ho tabhi jodiye._"
    return f"*{name}* ko *{inr(amount)}* dena hai{when}.{known}\n\nKya karoon?"


def refund_saved(supplier: str, due, why: str | None = None) -> str:
    what = "maal wapas aur paisa wapas, dono" if why == "goods" else "paisa wapas mila"
    return f"✅ Save ho gaya: {what}.\n" + due_line(supplier, due)


BTN_WHY_GOODS = "📦 Maal lautaya tha"
BTN_WHY_OVER = "💸 Zyada diya tha"


def refund_why(name: str, amount) -> str:
    return (f"💰 *{name}* se *{inr(amount)}* wapas mile. *Kyun?*\n\n"
            "📦 Maal lautaya tha: kharab ya extra maal wapas gaya\n"
            "💸 Zyada diya tha: galti se zyada payment hua tha: baaki utna badhega")


BTN_CASH_CUT = "✂️ Baaki se kata"
BTN_CASH_GOT = "💵 Paisa mila"


def goods_back_how(name: str, amount, due, goods=None) -> str:
    """Goods went back while the shop still owes at least that much: were the dues cut (usual), or did cash come?"""
    after = Decimal(str(due or 0)) - Decimal(str(amount))
    what = (f"*{inr(goods)}* ka maal lautaya, *{inr(amount)}* maana" if goods and Decimal(str(goods)) > Decimal(str(amount))
            else f"*{inr(amount)}* ka maal lautaya")
    return (f"📦 *{name}* ko {what}.\n"
            f"Bot mein *{name}* ka baaki: *{inr(due or 0)}*\n\n"
            "*Paisa haath mein mila, ya baaki se kata?*\n"
            f"✂️ Baaki se kata: baaki {_bal(due)} → {_bal(after)} _(aksar yahi hota hai)_\n"
            f"💵 Paisa mila: maal bhi wapas, paisa bhi: baaki {_bal(due)} hi rahega")


def partial_ask(name: str, goods) -> str:
    """'500 ka maal wapas kiya, 300 hi kata': which amount did the supplier accept?"""
    return (f"↩️ *{inr(goods)}* ka maal wapas gaya. *{name} ne kitna maana?*\n"
            "_Hisaab mein utna hi judega jitna supplier ne maana._")


def btn_amount(amount) -> str:
    return inr(amount)


def partial_note(goods, accepted) -> str:
    return f"_({inr(goods)} ka maal, supplier ne {inr(accepted)} maana)_"


def future_note(amount, name: str | None = None) -> str:
    """The reminder text for 'Gupta ko 5000 dena hai'. A known supplier is already named by the reminder;
    a name the shop does not have yet goes into the text."""
    return f"{name} ko {inr(amount)} dena" if name else f"{inr(amount)} dena"


def due_now(supplier: str, due) -> str:
    return due_line(supplier, due)


def parts_skipped(pieces: list[str]) -> str:
    shown = "\n".join(f"• _{p[:80]}_" for p in pieces[:3])
    return f"Ye hissa samajh nahi aaya, isliye save nahi kiya:\n{shown}\nAlag se likh ke bhejiye 🙏"


# fixing an entry already saved (last 7 days); every change is kept in the edit history
FIX_WINDOW_DAYS = 7
BTN_FIX_YES = "Haan, badlo ✅"
BTN_FIX_NO = "Nahi ❌"
BTN_UNDO_EDIT = "Badlo ✏️"
BTN_UNDO_DELETE = "Hata do 🗑️"
BTN_UNDO_KEEP = "Rehne do"
FIX_PICK = "Kaunsi entry? 👇"
LIST_BTN_ENTRY = "Entry chuniye"
KEPT = "Theek hai, kuch nahi badla."
EDIT_ROWS_FIX_BILL = [("amount", "Amount", ""), ("supplier_id", "Supplier", ""), ("date", "Bill ki date", ""),
                      ("credit_days", "Udhaar ke din", "")]
EDIT_ROWS_FIX_PAYMENT = [("amount", "Amount", ""), ("supplier_id", "Supplier", ""), ("date", "Payment ki date", "")]
ENTRY_KIND = {"bill": "🧾 Bill", "udhaar": "📝 Udhaar", "return": "↩️ Maal wapas", "payment": "💸 Payment",
              "refund": "💰 Paisa wapas"}
ENTRY_WORD = {"bill": "Bill", "udhaar": "Udhaar", "return": "Maal wapas", "payment": "Payment", "refund": "Paisa wapas"}
FIX_FIELD_LABEL = {"amount": "Amount", "supplier_id": "Supplier", "date": "Date", "credit_days": "Udhaar ke din",
                   "invoice_number": "Bill no"}


def _is_adjustment(e: dict) -> bool:
    return str(e.get("reason") or "").startswith("sudhaar")


def entry_line(e: dict) -> str:
    kind = "🔧 Sudhaar" if _is_adjustment(e) else ENTRY_KIND[e.get('kind') or e['type']]
    return f"{kind}: *{e['supplier']}* — *{inr(e['amount'])}*, {nice_date(e['day'])}"


def entry_row(e: dict) -> tuple[str, str]:
    """(title <= 24, description <= 72) for the 'which entry?' list."""
    kind = "Sudhaar" if _is_adjustment(e) else ENTRY_WORD[e.get("kind") or e["type"]]
    return e["supplier"][:24], f"{kind} {inr(e['amount'])} · {nice_date(e['day'])}"[:72]


def undo_card(e: dict) -> str:
    return f"{entry_line(e)}\n\nIs entry ka kya karna hai?"


def fix_card(e: dict, field: str, old: str, new: str) -> str:
    return f"✏️ *Badlav*\n{entry_line(e)}\n{FIX_FIELD_LABEL[field]}: {old} → *{new}*\n\nBadal doon?"


def fix_not_found(old_amount=None) -> str:
    what = f" {inr(old_amount)} ki" if old_amount else ""
    return (f"Pichhle {FIX_WINDOW_DAYS} din mein aisi koi{what} entry nahi mili. "
            f"Yahan se sirf pichhle {FIX_WINDOW_DAYS} din ki entry badal sakte hain.")


# an entry older than 7 days is never rewritten: a dated correcting entry ("sudhaar") is added instead, with the
# reason, so last month's hisaab still reads the same and the change is visible (how accountants fix old entries)
BTN_ADJ_YES = "✅ Sudhaar jodo"
BTN_ADJ_NO = "Rehne do"
ASK_ADJ_AMOUNT = "Is entry ka *sahi amount* likhiye (jaise 4580). Main farq ki sudhaar entry jodunga:"


def adjust_card(e: dict, new_amount, before, after) -> str:
    head = f"🔧 *Purani entry ka sudhaar*\n{entry_line(e)}"
    why = (f"Ye {FIX_WINDOW_DAYS} din se purani hai, isliye badli nahi jaati: purana hisaab jaisa tha waisa dikhe. "
           "Main aaj ki date se ek *sudhaar entry* jodta hoon:")
    if new_amount is None:
        change = f"Entry radd: *{inr(e['amount'])}* ulta"
    else:
        change = f"Amount: {inr(e['amount'])} → *{inr(new_amount)}* (farq {inr(abs(Decimal(str(new_amount)) - Decimal(str(e['amount']))))})"
    return f"{head}\n\n{why}\n{change}\n{due_change(before, after)}\n\nJod doon?"


def adjusted(supplier: str, due) -> str:
    return "🔧 Sudhaar entry jud gayi (purani entry jaisi thi waisi hai).\n" + due_line(supplier, due)


def old_found(n: int) -> str:
    return (f"Pichhle {FIX_WINDOW_DAYS} din mein nahi mili, par purani entry mili. Purani entry badalti nahi, "
            "uska *sudhaar* judta hai. Kaunsi? 👇")


def fixed(supplier: str, due) -> str:
    return "✅ Badal diya.\n" + due_line(supplier, due)


def deleted(supplier: str, due) -> str:
    return "🗑️ Hata diya.\n" + due_line(supplier, due)


def credit_label(days) -> str:
    return "nahi" if not days else f"{days} din"


# proof photo for a voice / typed bill (asked when a photo comes within 30 minutes of saving one)
PROOF_WINDOW_MINUTES = 30
BTN_PROOF_YES = "Haan, proof hai"
BTN_PROOF_NO = "Nahi, naya bill"
PROOF_READING = "📷 Proof padh raha hoon..."
PROOF_UNREAD = "✅ Photo proof ke roop mein jod di. (Photo se total padh nahi paaya.)"
BTN_PROOF_OWN = "Khud likhunga"


BTN_PROOF_ATTACH = "✅ Haan, proof hai"
BTN_PROOF_NEWBILL = "➕ Naya bill hai"
PROOF_NO_PHOTO = "Kaunsi photo? Pehle bill ki photo bhejiye, phir likhiye: _ye Sharma ke bill ka proof hai_"


def proof_match_ask(cand: dict, read_total, today: date) -> str:
    """A photo was read and looks like the bill the owner already said / typed: proof, not a second bill."""
    src = SOURCE_LABEL.get(cand.get("source") or "", "")
    said = Decimal(str(cand["total_amount"]))
    if read_total is None:
        photo = ""
    elif abs(Decimal(str(read_total)) - said) <= 1:
        photo = f" Photo mein bhi *{inr(read_total)}* hai."
    else:
        photo = f" Photo mein *{inr(read_total)}* hai."
    day = _as_date(cand.get("bill_date")) or today
    when = "aaj" if day == today else short_date(day)
    return (f"📷 Ye photo *{cand.get('supplier') or cand.get('supplier_name_raw')}* ke *{inr(said)}* wale bill "
            f"({when}, {src} se) ka *proof* lagti hai.{photo}\n\nProof ki tarah jod doon? "
            "_(Naya bill nahi banega, hisaab wahi rahega)_")


def proof_no_target(name: str | None) -> str:
    who = f"*{name}* ka" if name else "Koi"
    return (f"{who} bina-photo wala bill pichhle {FIX_WINDOW_DAYS} din mein nahi mila. Photo naye bill ki tarah "
            "save karni ho toh upar wale card pe *Sahi hai* dabaiye.")


def proof_dup(e: dict) -> str:
    return (f"Isi photo se ek alag bill bhi save hua tha ({inr(e['amount'])}). Wo isi bill ka duplicate hai? "
            "Hata doon?")


def proof_ask(bill: dict) -> str:
    return (f"Ye photo *{bill.get('supplier') or bill.get('supplier_name_raw')}* ke *{inr(bill['total_amount'])}* "
            f"wale bill ({nice_date(bill.get('bill_date'))}) ka proof hai?")


def proof_ok(amount) -> str:
    return f"✅ Proof jod diya. Photo mein bhi {inr(amount)} hai."


def proof_mismatch(said, read) -> str:
    return f"Aapne likha/bola *{inr(said)}*, photo mein *{inr(read)}* hai. Kaunsa sahi hai?"


def proof_kept(amount) -> str:
    return f"Theek hai, {inr(amount)} hi rakha. Proof photo jod di."

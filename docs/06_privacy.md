# Privacy Rules

Shop owners trust us with their business data. These rules are not optional.

1. **Consent first.** No photo, bill or message is stored before the owner agrees (signed slip in discovery, opt-in message in the pilot).
2. **Store only what is needed.** Bill fields and product events. Bill photos are deleted after 30 days (daily job, `bot/jobs.py`), except for shops that agreed to keep them for the reading test (`python -m bot.admin eval-consent S01 yes`). Owners can type bills instead of sending photos.
2a. **Say who reads.** The welcome message tells owners an AI program reads the photo, not a person, and that photos are deleted after 30 days.
3. **No full phone numbers in logs.** Events keep the last 4 digits only.
4. **Masking.** Shop and supplier names are masked in anything shown outside: screenshots, the case study, the demo video, GitHub.
5. **Deletion.** The owner sends "mera data delete karo" (or DELETE, also after STOP) and confirms with one tap: every row and photo of the shop is deleted at once (logged only as an anonymous `shop_data_deleted` event). By email, within 48 hours: `python -m bot.admin delete-shop S01` removes its rows and its photos.
6. **Never in the repository:** `.env`, tokens, `data/`, `discovery/private/`, real names, phone numbers, bill images. `.gitignore` and the pre-commit secret scan (`scripts/secret_scan.py`) enforce this.
6a. **Photos are private.** On the host they live in a private Supabase bucket with no public links; `RUN_check_setup.bat` fails if the bucket is public.
7. **Basics of India's DPDP Act:** clear purpose, consent, deletion on request.

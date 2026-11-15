# 15. Render: environment variables (names only)

`render.yaml` creates the service with all of these. Secrets (`sync: false`) are typed once in the Render dashboard; copy the values from your laptop `.env`. Never put a value in this file or in `render.yaml`.

| Name | Secret? | Value on Render | Used for |
|---|---|---|---|
| `WHATSAPP_TOKEN` | yes | from `.env` | Sending WhatsApp messages |
| `PHONE_NUMBER_ID` | yes | from `.env` | Which WhatsApp number sends |
| `VERIFY_TOKEN` | yes | from `.env` | Meta's one-time webhook check |
| `APP_SECRET` | yes | from `.env` | Checking Meta's signature on every webhook (must be set on Render) |
| `ANTHROPIC_API_KEY` | yes | from `.env` | Haiku / Sonnet bill reading, voice fallback |
| `DATABASE_URL` | yes | from `.env` (Supabase Postgres) | Shops, bills, payments, events (**required**: Render's disk is wiped) |
| `SUPABASE_URL` | yes | from `.env` | Photo bucket (**required**) |
| `SUPABASE_SERVICE_KEY` | yes | from `.env` | Photo bucket (**required**) |
| `SARVAM_API_KEY` | yes | from `.env` | Voice notes (leave empty = voice off) |
| `JOBS_TOKEN` | yes | a NEW random 24+ characters (also the GitHub secret `HISAAB_JOBS_TOKEN`) | Daily jobs endpoint |
| `OCR` | no | `0` | Docker build arg: no RapidOCR in the image (512 MB host) |
| `OCR_CHECK` | no | `0` | The bot doesn't look for OCR |
| `READ_POLICY` | no | `P5_no_ocr` | Read policy (decision 29 Oct) |
| `REQUIRE_PERSISTENT_STORAGE` | no | `1` | `/health` fails if bills or photos would go to the wiped disk |
| `GRAPH_VERSION` | no | `v23.0` | Meta API version |
| `BILL_MODEL` | no | `claude-haiku-5-5` | First reader |
| `ESCALATION_MODEL` | no | `claude-sonnet-5-5` | Second reader |
| `BILL_PROMPT` | no | `v2` | Prompt version |
| `QA_LLM` | no | `0` | AI question classifier off (rules + menu) |
| `VOICE_LLM` | no | `1` | Voice notes the rules cannot read go to one Haiku call that may fill cards (owner confirms) |
| `PHOTO_BUCKET` | no | `bill-photos` | Bucket name |
| `TEMPLATE_LANGUAGE` | no | `en` | Must match the language chosen for the templates in Meta |
| `PILOT_OPEN` | no | empty | Only invited shops |
| `AI_CALLS_PER_SHOP_PER_DAY` | no | `60` | Cost cap |
| `AI_CALLS_PER_DAY` | no | `1000` | Cost cap |
| `MAX_IMAGE_MB` | no | `8` | Photo size limit |
| `MAX_PDF_MB` | no | `10` | PDF size limit |

Set by Render itself, don't add: `PORT`. Laptop-only, not needed on Render: `WABA_ID` (setup checks), `PHOTO_DIR`.

GitHub secrets (repo > Settings > Secrets > Actions): `HISAAB_BOT_URL`, `HISAAB_JOBS_TOKEN`, `HISAAB_KEEPALIVE_URL`.

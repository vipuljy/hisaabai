# 14. Free hosting: two ways

Written 29 Oct 2026 from the providers' own docs (sources at the end). Free tiers change without notice: re-check the
limits on the day you sign up. Nothing here has been signed up for yet; every step is yours.

**Where each command runs** (shown before every block):

| Tag | Where |
|---|---|
| **[Browser]** | A website (Oracle, Render, Meta, GitHub) |
| **[Laptop]** | PowerShell on your Windows laptop, in `C:\Users\vipul\Documents\HisaabAI` unless it says otherwise |
| **[VM]** | Inside the Oracle server, after `ssh` (Ubuntu Linux) |
| **[Phone]** | Your WhatsApp |

## Which one?

| | (1) Oracle Always Free VM | (2) Render free + keep-alive |
|---|---|---|
| Cost | ₹0 (card needed to verify the account) | ₹0 |
| Machine | Arm VM, up to 2 OCPU + 12 GB RAM (Always Free total) | 0.1 CPU + 512 MB RAM |
| Free OCR check (RapidOCR) | Yes (needs ~1 GB): `READ_POLICY=P5_trusted_ocr` | No: build with `OCR=0`, `READ_POLICY=P5_no_ocr` |
| **Decision (29 Oct)** | Fallback option | **Chosen: live host** (no card; same measured safety) |
| Sleeps when idle | No | After 15 min without traffic (wake-up about 1 min), so a ping every 10 min |
| India region | Yes: Mumbai or Hyderabad | No: Singapore is the nearest |
| HTTPS | You set it up (Caddy, 2 commands) | Automatic (`https://<name>.onrender.com`) |
| Setup time | About 1–2 hours the first time | About 30 minutes |
| Main risks | Oracle may **reclaim an idle Always Free VM**; "out of capacity" when creating it | 750 free hours/month per workspace (one service 24/7 fits); disk is wiped on every restart; GitHub may skip some pings |

**OCR or not? (step 9, 136 eval bills, cached)**

| Policy | Silent errors | To Sonnet | Rs/bill |
|---|---|---|---|
| P5 (any OCR total counts) | 1 | 52% | 0.491 |
| **P5_trusted_ocr** (only a proved OCR total counts; Oracle) | 1 | 29% | 0.287 |
| **P5_no_ocr** (Render) | 1 | 17% | 0.205 |

- All three have the same single silent error (an invoice-number digit, not money).
- Every wrong Haiku total in the set was one Haiku itself marked as unsure, so Render without OCR is acceptable for the pilot. Watch the owners' total corrections.

---

## (1) Oracle Cloud Always Free (Arm VM)

### 1.1 Account and home region
1. **[Browser]** Go to oracle.com/cloud/free and sign up. You need a mobile number and a card; Oracle says the card is not charged unless you upgrade.
2. **Home region:** pick **India West (Mumbai)** or **India South (Hyderabad)**. Always Free VMs can only be made in the home region, and you choose it once.
3. Wait for the "account ready" email (can take a few minutes to hours).

### 1.2 Create the VM
1. **[Browser]** Menu > Compute > Instances > **Create instance**. Name: `hisaabai`.
2. Image: **Canonical Ubuntu 24.04** (the Arm build is picked automatically for an Arm shape).
3. Shape: **Ampere > VM.Standard.A1.Flex**, **1 OCPU, 6 GB** (half the free allowance; leaves room for a second VM later). 2 OCPU / 12 GB also stays free.
4. Networking: "Create new virtual cloud network" + "Create new public subnet", and **Assign a public IPv4 address = Yes**.
5. SSH keys: **Generate a key pair for me** > **Save private key**. Save it as `C:\Users\vipul\.ssh\hisaab_oci.key`.
6. Boot volume: default (about 50 GB; 200 GB total is free). Click **Create**.
7. If it says **"Out of host capacity"**: try another availability domain, or try again later (Oracle's own advice).
8. When it is green (Running), copy the **Public IP address**. Below it's written `<IP>`, e.g. `140.238.10.20`.

### 1.3 Open ports 80 and 443 (two places)
1. **[Browser]** On the instance page > Subnet link > Security Lists > Default Security List > **Add Ingress Rules**:
   - Source CIDR `0.0.0.0/0`, IP protocol TCP, destination port `80`
   - Same again for port `443`
2. **[Laptop]** Lock the key file (Windows ssh refuses a key others can read), then log in:
   ```powershell
   icacls $HOME\.ssh\hisaab_oci.key /inheritance:r
   icacls $HOME\.ssh\hisaab_oci.key /grant:r "$($env:USERNAME):(R)"
   ssh -i $HOME\.ssh\hisaab_oci.key ubuntu@<IP>
   ```
   Expect: "Are you sure you want to continue connecting?", type `yes`, then a prompt like `ubuntu@hisaabai:~$`.
3. **[VM]** Oracle's Ubuntu image also blocks ports in its own firewall. Allow 80/443 above the REJECT line and save:
   ```bash
   N=$(sudo iptables -L INPUT --line-numbers | awk '/REJECT/{print $1; exit}')
   sudo iptables -I INPUT $N -p tcp -m multiport --dports 80,443 -m state --state NEW -j ACCEPT
   sudo netfilter-persistent save
   sudo iptables -L INPUT --line-numbers | head
   ```
   Expect: an `ACCEPT ... multiport dports http,https` line **above** the `REJECT` line.

### 1.4 Docker and the code
1. **[VM]**
   ```bash
   sudo apt-get update && sudo apt-get install -y git
   curl -fsSL https://get.docker.com | sudo sh
   sudo usermod -aG docker ubuntu
   exit
   ```
2. **[Laptop]** Log in again (`ssh -i $HOME\.ssh\hisaab_oci.key ubuntu@<IP>`) so the docker group applies.
3. **[VM]** Check Docker:
   ```bash
   docker run --rm hello-world
   ```
   Expect: "Hello from Docker!"
4. **[VM]** Get the code. A public repo needs only the clone. For a private repo, make a GitHub fine-grained token with read-only access to this one repo and paste it when git asks for a password.
   ```bash
   git clone https://github.com/<your-user>/HisaabAI.git
   cd HisaabAI
   ```
5. **[VM]** Settings file, outside the code folder and readable only by you. Paste the same lines as your laptop `.env`, and set `PILOT_OPEN=` empty and a new random `JOBS_TOKEN`.
   ```bash
   nano ~/hisaab.env      # paste, then Ctrl+O, Enter, Ctrl+X
   chmod 600 ~/hisaab.env
   ```
   Set `READ_POLICY=P5_trusted_ocr` and `OCR_CHECK=1` (the Oracle option; this VM has the RAM for OCR).

### 1.5 Build and run the bot as a service
1. **[VM]** Build (Arm; first build about 5–10 minutes):
   ```bash
   docker build -t hisaabai .
   ```
   Expect: last line `naming to docker.io/library/hisaabai`.
2. **[VM]** Run it. `--restart unless-stopped` + Docker starting at boot = the bot comes back after a crash or a reboot:
   ```bash
   docker run -d --name hisaabai --restart unless-stopped --env-file ~/hisaab.env -p 127.0.0.1:8000:8000 hisaabai
   sleep 15 && curl -s http://127.0.0.1:8000/health
   ```
   Expect: `{"status":"ok","db":"ok"}`. If `"down"`, the database settings are wrong; read `docker logs --tail 50 hisaabai`.
3. **[VM]** `sudo systemctl is-enabled docker`. Expect: `enabled`.

### 1.6 Free HTTPS: pick ONE
Meta only calls an `https://` address with a valid certificate.

**Option A: Caddy + sslip.io (no domain to buy; recommended).**
- `sslip.io` turns your IP into a hostname: `140.238.10.20` becomes `140-238-10-20.sslip.io`.
- Caddy gets a free Let's Encrypt certificate for it automatically.

1. **[VM]** Replace `<IP-DASHED>` with your IP using dashes:
   ```bash
   docker run -d --name caddy --restart unless-stopped --network host -v caddy_data:/data caddy:2 \
     caddy reverse-proxy --from <IP-DASHED>.sslip.io --to 127.0.0.1:8000
   sleep 20 && docker logs --tail 20 caddy
   ```
   Expect: a line with `certificate obtained successfully`.
2. **[Laptop]** `curl.exe -s https://<IP-DASHED>.sslip.io/health`. Expect: `{"status":"ok","db":"ok"}`.

**Option B: Cloudflare Tunnel (needs your own domain added to a free Cloudflare account; no open ports).**
1. **[Browser]** Cloudflare dashboard > Zero Trust > Networks > Tunnels > **Create a tunnel** (Cloudflared) > copy the token.
2. **[VM]**
   ```bash
   docker run -d --name cloudflared --restart unless-stopped --network host cloudflare/cloudflared:latest \
     tunnel --no-autoupdate run --token <TOKEN>
   ```
3. **[Browser]** In the tunnel, add a **Public hostname**, e.g. `bot.yourdomain.in`, with service `http://127.0.0.1:8000`.
4. "Quick tunnels" (`trycloudflare.com`) are **not** for this. Cloudflare says they are for testing only, and the address changes every time they start.

### 1.7 Connect WhatsApp and the daily jobs
1. **[Browser]** Meta app > WhatsApp > Configuration > Webhook > **Edit**:
   - Callback URL: `https://<your-host>/webhook`
   - Verify token: the `VERIFY_TOKEN` from `~/hisaab.env`
   - Click **Verify and save**, then subscribe to **messages**.
2. **[Browser]** GitHub repo > Settings > Secrets and variables > Actions:
   - `HISAAB_BOT_URL` = `https://<your-host>`
   - `HISAAB_JOBS_TOKEN` = the `JOBS_TOKEN` from `~/hisaab.env`
   - Do **not** set `HISAAB_KEEPALIVE_URL` (Oracle doesn't sleep).
3. **[Phone]** Send "hi" to the bot number. Expect the welcome message.

### 1.8 Day-to-day
- **[VM] Logs:** `docker logs -f --tail 100 hisaabai` (Ctrl+C to stop watching).
- **[VM] Update** after you push new code:
  ```bash
  cd ~/HisaabAI && git pull && docker build -t hisaabai . && docker rm -f hisaabai && \
  docker run -d --name hisaabai --restart unless-stopped --env-file ~/hisaab.env -p 127.0.0.1:8000:8000 hisaabai
  ```
- **Idle-VM rule:** Oracle may reclaim an Always Free VM if, over 7 days, its 95th-percentile CPU, network and memory use (memory: Arm shapes only) all stay **below 20%**. A small pilot bot will look idle by that test.
  - Oracle's page doesn't say whether Pay As You Go accounts are exempt.
  - Upgrading to Pay As You Go keeps Always Free resources free (Oracle charges only usage above the free limits) and is the safest fix. Decide this yourself; it needs the card on file.
  - Either way, the VM holds nothing you can't rebuild: data and photos live in Supabase, the code in GitHub, and the settings in your `.env`. Re-creating takes about 30 minutes with this page.
- **Backups:** nothing to back up on the VM. Keep `hisaab.env` in sync with your laptop's `.env`.

---

## (2) Render free + GitHub keep-alive (no OCR): the chosen live host

`render.yaml` in the repo root describes the service (free plan, Singapore, Docker, `/health`). On Render: **New > Blueprint** picks it up and asks for each secret once (list in `docs/15_render_env.md`). The manual steps below do the same by hand.

1. **[Laptop]** Code on GitHub (`SETUP_github.bat` already does this). `.env` and `data/` are never pushed (`.gitignore` + secret scan).
2. **[Browser]** render.com > sign up > **New > Web Service** > connect the GitHub repo:
   - Runtime: **Docker** (Render finds the `Dockerfile`)
   - Region: **Singapore** (Render has no India region)
   - Instance type: **Free**
   - Health check path: `/health`
3. **[Browser]** Environment variables: every key from your `.env` (values from your laptop), plus:

   | Key | Value | Why |
   |---|---|---|
   | `OCR` | `0` | Render passes env vars to the Docker build as build args: this skips RapidOCR (needs ~1 GB; the free instance has 512 MB) |
   | `OCR_CHECK` | `0` | The bot doesn't look for OCR |
   | `READ_POLICY` | `P5_no_ocr` | P5 without the OCR checks |
   | `PHOTO_BUCKET` etc. | as in `.env` | **Required here:** Render's disk is wiped on every restart, so photos must go to the Supabase bucket |
   | `PILOT_OPEN` | empty | |
   | `JOBS_TOKEN` | a new random 24+ characters | |

   Don't set `PORT`: Render sets it, and the Dockerfile uses it.
4. **[Browser]** **Create Web Service**. The first deploy takes a few minutes. Then open `https://<name>.onrender.com/health`. Expect: `{"status":"ok","db":"ok"}`.
5. **[Browser]** Meta webhook: same as 1.7, with `https://<name>.onrender.com/webhook`.
6. **Keep-alive:**
   1. **[Laptop]** Run `SETUP_github.bat`. It copies `deploy/github-workflows/keep-alive.yml` into `.github/workflows/` with the other two. Then push.
   2. **[Browser]** GitHub > Settings > Secrets > Actions, add:
      - `HISAAB_KEEPALIVE_URL` = `https://<name>.onrender.com`
      - `HISAAB_BOT_URL` and `HISAAB_JOBS_TOKEN`, as in 1.7
   3. **[Browser]** Actions tab > **keep-alive** > Run workflow. Expect a log line like `HTTP 200 in 0.4s` (up to about a minute if the service was asleep).
7. **Limits to know:**
   - **750 free instance hours** per workspace per month: one service awake 24/7 uses 744 in a 31-day month. Don't run a second free service. If the hours run out, Render suspends free services until the next month.
   - **GitHub may start a scheduled run late or skip it** when busy, so now and then the bot sleeps and the next message waits about a minute.
   - **Keep the repo public, or check the bill:** on a public repo the pings are free. On a private repo, about 4,400 runs a month use Actions minutes (the free plan includes about $12 of compute a month); check Settings > Billing first.
   - **60-day rule:** on a public repo, GitHub turns scheduled workflows off after 60 days without any repo activity. Push something, or re-enable it in the Actions tab.
   - Render may restart a free service at any time, and may suspend one that makes unusually heavy outbound traffic.

---

### Sources
- Oracle, Always Free resources (A1 limits, idle-reclaim rule, home region, capacity): https://docs.oracle.com/en-us/iaas/Content/FreeTier/resourceref.htm
- Oracle, Free Tier overview (card, home region): https://docs.oracle.com/iaas/Content/FreeTier/freetier.htm
- Linuxiac, A1 allowance cut from 4 OCPU / 24 GB to 2 OCPU / 12 GB (June 2026): https://linuxiac.com/oracle-quietly-cuts-free-tier-ampere-a1-resources-in-half/
- Render, free services (15-minute spin-down, 750 hours, ephemeral disk): https://render.com/docs/free
- Render, instance types (Free = 0.1 CPU, 512 MB): https://render.com/docs/compute-plans
- Render, env vars as Docker build args: https://render.com/docs/docker-secrets
- Render, regions: https://render.com/docs/regions
- GitHub, scheduled workflows disabled after 60 days of inactivity: https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows
- GitHub, Actions billing: https://docs.github.com/en/billing/concepts/product-billing/github-actions
- Cloudflare, Quick Tunnels are for testing only: https://developers.cloudflare.com/argo-tunnel/trycloudflare
- nip.io / sslip.io, IP-in-hostname and Let's Encrypt: https://nip.io/

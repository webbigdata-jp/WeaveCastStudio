# WeaveCastStudio GCE Deployment Guide

**Target:** M1 (Data Collection & Summarization) + M3 (Site Crawling & Analysis)
**GCP Project:** weavecaststudio
**Instance:** e2-small / Ubuntu 24.04 LTS / asia-northeast1

---

## Step 1: Create GCE Instance

Execute via Google Cloud Console or gcloud CLI.

```bash
# Project setup
gcloud config set project weavecaststudio

# Enable API (First time only)
gcloud services enable compute.googleapis.com

# Create instance
gcloud compute instances create weavecast-collector \
  --zone=asia-northeast1-b \
  --machine-type=e2-small \
  --image-family=ubuntu-2404-lts-amd64 \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=30GB \
  --boot-disk-type=pd-balanced \
  --tags=weavecast
```

> **Memory Warning:** e2-small has 2GB RAM. If you encounter Out-Of-Memory (OOM) issues with DrissionPage + Chromium, change it to 4GB by running:
> `gcloud compute instances set-machine-type weavecast-collector --zone=asia-northeast1-b --machine-type=e2-medium`
> (Requires stopping the instance first).

---

## Step 2: Connect via SSH

```bash
gcloud compute ssh weavecast-collector --zone=asia-northeast1-b
```

---

## Step 3: Install System Packages

```bash
# Update packages
sudo apt update && sudo apt upgrade -y

# Chromium + Japanese fonts + Virtual display
sudo apt install -y \
  chromium-browser \
  fonts-noto-cjk \
  xvfb \
  git \
  curl

# Check Chromium path (used by DrissionPage)
which chromium-browser
# -> /usr/bin/chromium-browser
```

---

## Step 4: Install `uv`

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env

# Verify installation
uv --version
```

---

## Step 5: Clone the Repository

```bash
cd ~
git clone https://github.com/webbigdata-jp/WeaveCastStudio.git
cd WeaveCastStudio
```

---

## Step 6: Setup Python Environment

```bash
cd ~/WeaveCastStudio

# Install Python and resolve dependencies using uv
uv sync
```

---

## Step 7: Setup `.env` File

```bash
# Single .env at project root (shared by M1, M3, M4)
cat > ~/WeaveCastStudio/.env << 'EOF'
GOOGLE_API_KEY=your_api_key_here
EOF
```

---

## Step 8: Test DrissionPage Operation

Since GCE has no GUI, verify if it runs properly in headless mode.

```bash
cd ~/WeaveCastStudio/compe_M3

uv run main.py crawl
```

**If you encounter a Chromium path error:**

You might need to specify the Chromium path explicitly in DrissionPage.
In that case, configure it in your crawler code as follows:

```python
from DrissionPage import ChromiumOptions
co = ChromiumOptions()
co.set_browser_path('/usr/bin/chromium-browser')
co.headless(True)  # Headless mode is mandatory on GCE
```

> **Verification Point:** It's successful if `main.py crawl` completes normally and records are inserted into `articles.db`.

---

## Step 9: Test M1 Operation

```bash
cd ~/WeaveCastStudio/compe_M1
uv run main.py --phase 1
```

> It's successful if JSON files are generated under the `output/` directory.

---

## Step 10: Setup `cron`

```bash
crontab -e
```

Add the following:

```cron
# === WeaveCastStudio: GCE Data Collection ===

# M3: Full pipeline — crawl all → analyze → compose (Every 2 hours)
0 */2 * * * cd /home/$USER/WeaveCastStudio/compe_M3 && /home/$USER/.local/bin/uv run main.py pipeline >> /home/$USER/WeaveCastStudio/logs/m3_cron.log 2>&1

# M1: Data collection + Summarization (Every 1 hour)
0 * * * * cd /home/$USER/WeaveCastStudio/compe_M1 && /home/$USER/.local/bin/uv run main.py --phase 1 >> /home/$USER/WeaveCastStudio/logs/m1_cron.log 2>&1
```

```bash
# Create log directory
mkdir -p ~/WeaveCastStudio/logs
```

> **Note:** Since the `PATH` is minimal inside `cron`, specify the full path for `uv`.

---

## Step 11: Verify Operations

```bash
# Check if cron is registered
crontab -l

# Run manually once and check the logs
cd ~/WeaveCastStudio/compe_M3
uv run main.py pipeline

cd ~/WeaveCastStudio/compe_M1
uv run main.py --phase 1

# Check if data is inserted into the DB
sqlite3 ~/WeaveCastStudio/compe_M3/data/articles.db "SELECT COUNT(*) FROM articles;"

# Check the logs (after cron execution)
tail -f ~/WeaveCastStudio/logs/m3_cron.log
tail -f ~/WeaveCastStudio/logs/m1_cron.log
```

---

## Step 12: What to Show for GCP Proof Recording

1. **GCP Console** — Screen showing the GCE instance `weavecast-collector` is Running.
2. **SSH Connection** — Screen showing the connection via `gcloud compute ssh`.
3. **Process Verification** — Schedule verification via `crontab -l`.
4. **Log Verification** — Screen showing crawling logs flowing via `tail logs/m3_cron.log`.
5. **DB Verification** — Display record counts via `sqlite3 articles.db`.

---

## Troubleshooting

### DrissionPage cannot find Chromium

```bash
# Check Chromium location
which chromium-browser
dpkg -L chromium-browser | grep bin

# Path differs if it's the snap version
# /snap/bin/chromium
```

### Out of Memory (OOM Killer)

```bash
# Check memory usage
free -h

# Check if OOM occurred
dmesg | grep -i "out of memory"

# -> Change to e2-medium (4GB)
gcloud compute instances stop weavecast-collector --zone=asia-northeast1-b
gcloud compute instances set-machine-type weavecast-collector \
  --zone=asia-northeast1-b --machine-type=e2-medium
gcloud compute instances start weavecast-collector --zone=asia-northeast1-b
```

### Chromium headless mode doesn't work

```bash
# Resolve with virtual display
sudo apt install -y xvfb
Xvfb :99 -screen 0 1920x1080x24 &
export DISPLAY=:99

# If adding to cron
# */30 * * * * export DISPLAY=:99 && cd /home/$USER/WeaveCastStudio/compe_M3 && ...
```

### `uv sync` is slow / fails

```bash
# Specify Python version explicitly
uv python install 3.11
uv sync --python 3.11
```
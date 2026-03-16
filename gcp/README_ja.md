# WeaveCastStudio GCE デプロイ手順書

**対象:** M1 (データ収集・要約) + M3 (サイト巡回・分析)
**GCPプロジェクト:** weavecaststudio
**インスタンス:** e2-small / Ubuntu 24.04 LTS / asia-northeast1

---

## Step 1: GCEインスタンス作成

Google Cloud Console または gcloud CLI で実行。

```bash
# プロジェクト設定
gcloud config set project weavecaststudio

# APIの有効化（初回のみ）
gcloud services enable compute.googleapis.com

# インスタンス作成
gcloud compute instances create weavecast-collector \
  --zone=asia-northeast1-b \
  --machine-type=e2-small \
  --image-family=ubuntu-2404-lts-amd64 \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=30GB \
  --boot-disk-type=pd-balanced \
  --tags=weavecast
```

> **メモリ注意:** e2-small は 2GB RAM。DrissionPage + Chromium でメモリ不足になった場合は
> `gcloud compute instances set-machine-type weavecast-collector --zone=asia-northeast1-b --machine-type=e2-medium`
> で 4GB に変更（要インスタンス停止）。

---

## Step 2: SSH接続

```bash
gcloud compute ssh weavecast-collector --zone=asia-northeast1-b
```

---

## Step 3: システムパッケージのインストール

```bash
# パッケージ更新
sudo apt update && sudo apt upgrade -y

# Chromium + 日本語フォント + 仮想ディスプレイ
sudo apt install -y \
  chromium-browser \
  fonts-noto-cjk \
  xvfb \
  git \
  curl

# Chromium のパス確認（DrissionPage が使う）
which chromium-browser
# → /usr/bin/chromium-browser
```

---

## Step 4: uv のインストール

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env

# 確認
uv --version
```

---

## Step 5: リポジトリのクローン

```bash
cd ~
git clone https://github.com/webbigdata-jp/WeaveCastStudio.git
cd WeaveCastStudio
```

---

## Step 6: Python 環境セットアップ

```bash
cd ~/WeaveCastStudio

# uv で Python インストール + 依存解決
uv sync
```

---

## Step 7: .env の配置

```bash
# M1用
cat > compe_M1/config/.env << 'EOF'
GOOGLE_API_KEY=your_api_key_here
EOF

# M3用（同じ内容）
cp compe_M1/config/.env compe_M3/config/.env
```

---

## Step 8: DrissionPage の動作確認

GCE は GUI がないので、headless モードで動くか確認する。

```bash
cd ~/WeaveCastStudio/compe_M3

uv run test_phase1.py
```

**もし Chromium パスのエラーが出た場合:**

DrissionPage に Chromium のパスを明示的に指定する必要があるかもしれない。
その場合はクローラーのコード内で以下のように設定：

```python
from DrissionPage import ChromiumOptions
co = ChromiumOptions()
co.set_browser_path('/usr/bin/chromium-browser')
co.headless(True)  # GCEではheadless必須
```

> **確認ポイント:** test_phase1.py が正常に完了し、articles.db にレコードが入ればOK。

---

## Step 9: M1 の動作確認

```bash
cd ~/WeaveCastStudio/compe_M1
uv run main.py --phase 1
```

> output/ 配下に JSON が生成されればOK。

---

## Step 10: cron の設定

```bash
crontab -e
```

以下を追記：

```cron
# === WeaveCastStudio: GCE Data Collection ===

# M3: 全ソース一括巡回（30分ごと）
*/30 * * * * cd /home/$USER/WeaveCastStudio/compe_M3 && /home/$USER/.local/bin/uv run test_phase4.py --all >> /home/$USER/WeaveCastStudio/logs/m3_cron.log 2>&1

# M1: 情報収集+要約（1時間ごと）
0 * * * * cd /home/$USER/WeaveCastStudio/compe_M1 && /home/$USER/.local/bin/uv run main.py --phase 1 >> /home/$USER/WeaveCastStudio/logs/m1_cron.log 2>&1
```

```bash
# ログディレクトリ作成
mkdir -p ~/WeaveCastStudio/logs
```

> **注意:** cron 内では PATH が最小限なので `uv` はフルパス指定。

---

## Step 11: 動作確認

```bash
# cron が登録されたか確認
crontab -l

# 手動で一度実行してログ確認
cd ~/WeaveCastStudio/compe_M3
uv run test_phase4.py --all

cd ~/WeaveCastStudio/compe_M1
uv run main.py --phase 1

# DB にデータが入ったか確認
sqlite3 ~/WeaveCastStudio/compe_M3/data/articles.db "SELECT COUNT(*) FROM articles;"

# ログの確認（cron実行後）
tail -f ~/WeaveCastStudio/logs/m3_cron.log
tail -f ~/WeaveCastStudio/logs/m1_cron.log
```

---

## Step 12: GCP Proof Recording で見せるもの

1. **GCPコンソール** — GCEインスタンス `weavecast-collector` が Running の画面
2. **SSH接続** — `gcloud compute ssh` で接続した画面
3. **プロセス確認** — `crontab -l` でスケジュール確認
4. **ログ確認** — `tail logs/m3_cron.log` で巡回ログが流れている画面
5. **DB確認** — `sqlite3 articles.db` でレコード数を表示

---

## トラブルシューティング

### DrissionPage が Chromium を見つけられない

```bash
# Chromium の場所を確認
which chromium-browser
dpkg -L chromium-browser | grep bin

# snap 版の場合パスが異なる
# /snap/bin/chromium
```

### メモリ不足 (OOM Killer)

```bash
# メモリ使用状況確認
free -h

# OOM が発生したか確認
dmesg | grep -i "out of memory"

# → e2-medium (4GB) に変更
gcloud compute instances stop weavecast-collector --zone=asia-northeast1-b
gcloud compute instances set-machine-type weavecast-collector \
  --zone=asia-northeast1-b --machine-type=e2-medium
gcloud compute instances start weavecast-collector --zone=asia-northeast1-b
```

### Chromium headless が動かない

```bash
# 仮想ディスプレイで対応
sudo apt install -y xvfb
Xvfb :99 -screen 0 1920x1080x24 &
export DISPLAY=:99

# cron に入れる場合
# */30 * * * * export DISPLAY=:99 && cd /home/$USER/WeaveCastStudio/compe_M3 && ...
```

### uv sync が遅い / 失敗

```bash
# Python バージョン明示
uv python install 3.11
uv sync --python 3.11
```

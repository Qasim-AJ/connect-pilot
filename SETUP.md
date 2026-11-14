# ConnectPilot — Complete Setup & Agent Onboarding Guide (`SETUP.md`)

Welcome to **ConnectPilot**! All machine-specific paths, browser settings, rate limits, and credentials have been externalized into `.env` so this repository works out of the box across **Windows, macOS, and Linux**—whether you set it up manually or hand it to an AI coding agent (Google Antigravity, Claude Code, Cursor, Codex, etc.).

---

## Quick Start: Setting Up With Your AI Agent

If you are using an AI agent, simply open this repository folder in your agent workspace and paste:

> **"Read `SETUP.md` and set up ConnectPilot on my machine. Walk me through configuring `.env`, signing into the dedicated browser profile once, verifying the setup, and optionally scheduling daily runs."**

---

## Agent Execution Runbook (Instructions for AI Agents)

If you are an AI agent setting up ConnectPilot on a new user's machine, follow these steps in order:

### Step 1: Detect the Host Environment
1. Check the OS (`Windows`, `macOS`, or `Linux`) and verify **Python 3.9+** is installed (`python --version` or `python3 --version`).
2. Check which Chromium browser is installed:
   - **Microsoft Edge** (`BROWSER_TYPE=edge`) — default on Windows, also supported on macOS/Linux.
   - **Google Chrome** (`BROWSER_TYPE=chrome`) — great default on macOS/Linux or if the user prefers Chrome.
   - *(Note: Selenium 4.22+ automatically downloads the matching `msedgedriver` or `chromedriver` via Selenium Manager—no manual WebDriver installation is needed.)*

### Step 2: Create Virtual Environment & Install Dependencies
Run from the repository root:

**Windows (PowerShell):**
```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\pip.exe install -r requirements.txt
```

**macOS / Linux:**
```bash
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt
```

### Step 3: Create & Configure `.env`
1. Copy `.env.example` to `.env` (never modify `.env.example` with personal secrets):
   - Windows: `Copy-Item .env.example .env`
   - macOS/Linux: `cp .env.example .env`
2. Ask the user for their preferences and update `.env`:
   - **`BROWSER_TYPE`**: Set `edge` (uses `EDGE_USER_DATA_DIR=./edge_profile`) or `chrome` (uses `CHROME_USER_DATA_DIR=./chrome_profile`). Leave `EDGE_BINARY_PATH` / `CHROME_BINARY_PATH` blank unless the browser is installed in a non-standard directory.
   - **`SEARCH_LINK`**: Ask the user to run a LinkedIn **People** search in their browser (with any filters like *2nd-degree connections*, *Location*, or *Title/Keywords*), copy the full URL from the address bar, and provide it for `SEARCH_LINK`. *(Alternatively, set `SEARCH_KEYWORDS`.)*
   - **Connection Limits**: Confirm `MAX_CONNECT_REQUESTS_PER_SESSION` (recommended: `10`–`15`), `DAILY_CONNECTION_LIMIT` (recommended: `15`–`30`), and `WEEKLY_CONNECTION_LIMIT` (recommended: `80`–`120`).
   - **Personalized Notes (Optional)**: Ask if they want to send invitations **without a note** (`SEND_WITH_NOTE=false`, higher acceptance rate and works on free LinkedIn accounts without note limits) or **with a custom note** (`SEND_WITH_NOTE=true` and customize `CONNECTION_MESSAGE_TEMPLATES` with their name).

### Step 4: One-Time LinkedIn Authentication (Zero Passwords Needed!)
ConnectPilot supports three authentication modes. **Method A is strongly recommended** because it works seamlessly with Google Sign-In, Apple Sign-In, Passkeys, and 2FA without storing any password in `.env`:

- **Method A — Dedicated Persistent Browser Profile (Recommended):**
  1. Launch the isolated automation browser profile:
     ```bash
     # Cross-platform (uses .venv Python):
     python cli.py browser-login
     ```
     *(On Windows, double-clicking `Sign_In_Once.bat` does the exact same thing.)*
  2. Have the user sign into LinkedIn once in that window and wait until their LinkedIn feed loads.
  3. **Important:** Have the user **close that automation browser window** once signed in so Selenium can launch the profile cleanly without profile-lock conflicts (your personal browser windows are completely separate and untouched).

- **Method B — `LI_AT_COOKIE` in `.env`:**
  - Paste the `li_at` cookie value from LinkedIn DevTools (`Application -> Cookies -> https://www.linkedin.com`) into `LI_AT_COOKIE=` in `.env`.

- **Method C — Email & Password in `.env`:**
  - Set `LINKEDIN_USERNAME` and `LINKEDIN_PASSWORD` in `.env`.

### Step 5: Verify the Installation
1. Run the offline verification unit tests:
   ```bash
   python -m unittest discover -s tests -v
   ```
2. Check rate-limiter database initialization & stats CLI:
   ```bash
   python cli.py stats
   ```
3. Run a live 1-connection verification test:
   ```bash
   python main.py --limit 1
   ```
   Confirm that ConnectPilot logs in via the persistent profile, sends 1 verified connection request, logs it in `python cli.py stats`, and keeps `.env` intact.

### Step 6: Optional — Schedule Daily Unattended Runs
Ask the user if they want ConnectPilot to run automatically every day (e.g., 15 connections every morning at 7:00 AM or 9:00 AM).

#### Option 1: Google Antigravity Scheduled Automation (`sidecar.json`)
1. Read the template at [`automation/sidecar.example.json`](automation/sidecar.example.json).
2. Detect the user's IANA timezone (e.g. `America/New_York`, `Europe/London`, `Asia/Karachi`) and preferred daily schedule.
3. Replace placeholders with the user's actual machine paths:
   - `<IANA_TIMEZONE>` -> e.g. `America/New_York`
   - `<ABSOLUTE_REPO_PATH>` -> absolute path to this repository on the user's machine
   - `<ABSOLUTE_REPO_PATH_FORWARD_SLASHES>` -> same path using forward slashes (`/`)
   - `<ABSOLUTE_VENV_PYTHON>` -> `<repo>/.venv/Scripts/python.exe` (Windows) or `<repo>/.venv/bin/python` (macOS/Linux)
4. Write the populated config to `~/.gemini/config/sidecars/daily-connectpilot/sidecar.json` (or use Antigravity's `/schedule` / `automation` skill) and instruct the user to enable **"Daily ConnectPilot"** in their Antigravity Automations Dashboard.

#### Option 2: Windows Task Scheduler (Standalone without Agent)
```powershell
$RepoPath = (Resolve-Path .).Path
$PythonExe = Join-Path $RepoPath ".venv\Scripts\python.exe"
$MainPy = Join-Path $RepoPath "main.py"
$Action = New-ScheduledTaskAction -Execute $PythonExe -Argument "`"$MainPy`" --limit 15" -WorkingDirectory $RepoPath
$Trigger = New-ScheduledTaskTrigger -Daily -At 9:00AM
Register-ScheduledTask -TaskName "ConnectPilot_Daily" -Action $Action -Trigger $Trigger -Description "Daily ConnectPilot LinkedIn networking run"
```

#### Option 3: macOS / Linux `cron` (Standalone without Agent)
Run `crontab -e` and add (adjusting time and repo path):
```cron
0 9 * * * cd /absolute/path/to/connect-pilot && ./.venv/bin/python main.py --limit 15 >> logs/cron.log 2>&1
```

---

## Manual Setup Summary (Human Quick Reference)

```bash
# 1. Clone the repository
git clone https://github.com/Qasim-AJ/connect-pilot.git
cd connect-pilot

# 2. Create & activate a virtual environment
python -m venv .venv
# Windows:
.\.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Create your local .env config
cp .env.example .env   # (or `copy .env.example .env` on Windows)
# Edit .env to paste your LinkedIn SEARCH_LINK

# 5. Sign into LinkedIn once in the dedicated automation browser profile
python cli.py browser-login
# (Sign in, wait for feed to load, then close that browser window)

# 6. Run ConnectPilot!
python main.py --limit 15

# 7. Check your daily/weekly stats & sent profile links
python cli.py stats
```

---

## Complete `.env` Environment Variables Reference

All system-dependent paths default to portable relative paths inside the repository root and auto-detect browser binaries across OSes.

| Variable | Default | Description |
| :--- | :--- | :--- |
| `BROWSER_TYPE` | `edge` | Browser engine to automate: `edge`, `chrome`, or `firefox`. |
| `EDGE_USER_DATA_DIR` | `./edge_profile` | Dedicated persistent Edge profile directory (relative paths resolve from repo root). |
| `EDGE_PROFILE_DIRECTORY` | `Default` | Profile subfolder inside `EDGE_USER_DATA_DIR`. |
| `CHROME_USER_DATA_DIR` | `./chrome_profile` | Dedicated persistent Chrome profile directory (used when `BROWSER_TYPE=chrome`). |
| `CHROME_PROFILE_DIRECTORY` | `Default` | Profile subfolder inside `CHROME_USER_DATA_DIR`. |
| `EDGE_BINARY_PATH` | *(auto-detected)* | Optional absolute path to `msedge` if installed in a custom location. |
| `CHROME_BINARY_PATH` | *(auto-detected)* | Optional absolute path to `chrome` if installed in a custom location. |
| `FIREFOX_BINARY_PATH` | *(auto-detected)* | Optional absolute path to `firefox` binary. |
| `GECKODRIVER_PATH` | *(auto-managed)* | Optional path to `geckodriver`; leave blank to let Selenium Manager handle it. |
| `EDGE_DEBUGGER_ADDRESS` | *(blank)* | Leave blank for dedicated profile mode; set `auto` or `127.0.0.1:9223` to attach to a running Edge window. |
| `EDGE_REMOTE_DEBUGGING_PORT` | `9223` | Port used by `python cli.py browser-login --remote-debugging` / `login_with_google.bat`. |
| `EDGE_DEVTOOLS_ACTIVE_PORT_FILE` | *(auto-detected)* | Optional custom path to Edge's `DevToolsActivePort` file when `EDGE_DEBUGGER_ADDRESS=auto`. |
| `CDP_DAEMON_HOST` | `127.0.0.1` | Host for the local single-WebSocket CDP bridge daemon. |
| `CDP_DAEMON_PORT` | `9229` | Port for the local single-WebSocket CDP bridge daemon. |
| `LI_AT_COOKIE` | *(blank)* | Optional LinkedIn `li_at` session cookie (alternative to browser sign-in). |
| `LINKEDIN_USERNAME` | *(blank)* | Optional LinkedIn email (only needed if not using persistent browser profile or `LI_AT_COOKIE`). |
| `LINKEDIN_PASSWORD` | *(blank)* | Optional LinkedIn password. |
| `CREDENTIALS_DIR` | `~/.linkedin_bot/credentials` | Optional directory for encrypted local credential storage. |
| `SEARCH_LINK` | People search URL | Target LinkedIn People Search URL. Auto-updates `page=N` after successful runs! |
| `SEARCH_KEYWORDS` | `software engineer` | Fallback search keywords if `SEARCH_LINK` is omitted. |
| `MAX_CONNECT_REQUESTS_PER_SESSION` | `15` | Max verified invitations to send per run (`--limit` overrides this). |
| `DAILY_CONNECTION_LIMIT` | `25` | Hard daily cap enforced by the local SQLite rate limiter. |
| `WEEKLY_CONNECTION_LIMIT` | `100` | Hard weekly cap enforced by the local SQLite rate limiter. |
| `SEND_WITH_NOTE` | `false` | `false` clicks *Send without a note*; `true` attaches a personalized template. |
| `CONNECTION_MESSAGE_TEMPLATES` | Generic templates | Pipe-separated (`\|`) templates supporting `{name}`, `{title}`, `{company}`, `{location}`. |
| `HEADLESS` | `false` | Set `true` to run browser headless (visible mode `false` is recommended for safety). |
| `DATABASE_PATH` | `data/bot.db` | SQLite database tracking sent/failed invitations and LinkedIn weekly quota status. |
| `SESSION_DIR` | `sessions` | Directory for encrypted cookie session backups. |
| `LOG_LEVEL` | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `LOG_FILE` | `logs/bot.log` | Log file location (relative to repo root). |

---

## Safety & Troubleshooting

1. **Profile Lock / "Browser failed to start"**:
   - Make sure the dedicated `edge_profile` / `chrome_profile` window from `browser-login` is closed before running `python main.py` (unless you intentionally configured `EDGE_DEBUGGER_ADDRESS` to attach to a live window).
2. **LinkedIn Weekly Invitation Limit (`HTTP 429 FUSE_LIMIT_EXCEEDED`)**:
   - ConnectPilot inspects both Chromium CDP network responses and Shadow DOM toasts on every single click. If LinkedIn's server-side weekly cap is reached, ConnectPilot **stops immediately**, records the block in `python cli.py stats`, and **does not advance** your `SEARCH_LINK` page so zero profiles are skipped.
3. **Never Commit Personal State**:
   - `.env`, `edge_profile/`, `chrome_profile/`, `sessions/`, `data/`, and `logs/` are all git-ignored by default. Never force-add them to Git.

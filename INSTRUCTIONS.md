# ConnectPilot — Usage & Reference Instructions

> **Looking to set up ConnectPilot on a new computer (or with an AI agent)?**
> Start with **[SETUP.md](SETUP.md)** for the complete human + AI-agent setup runbook and `.env` variable reference.

---

## Everyday Commands

### 1. One-Time Browser Sign-In
```bash
python cli.py browser-login
```
*(Windows shortcut: double-click `Sign_In_Once.bat`.)*  
Sign into LinkedIn in the dedicated browser window, wait for your feed to load, and close that window.

### 2. Send Connection Requests
```bash
# Unattended / script-friendly runner (uses MAX_CONNECT_REQUESTS_PER_SESSION from .env by default)
python main.py --limit 15

# Interactive Rich CLI runner with progress bar
python cli.py run --limit 15

# Override search URL or message template for a single run
python cli.py run --search-url "https://www.linkedin.com/search/results/people/?keywords=recruiter" --limit 10
```

### 3. View Statistics & Recent Profile Links
```bash
# Rich summary table + latest 15 connection attempts with clickable LinkedIn URLs
python cli.py stats

# Today's counts only
python cli.py stats --today

# Machine-readable JSON / YAML output
python cli.py stats --format json
python cli.py stats --format yaml
```

### 4. Session & Config Utilities
```bash
# List or clean encrypted cookie sessions
python cli.py sessions
python cli.py clean --max-age 48 --execute

# Inspect active configuration
python cli.py config --list

# Run diagnostics
python cli.py test --check-rate-limits
python cli.py test --check-browser
```

---

## Recommended Safety Practices

- **Daily Volume**: Keep between **10–25** connection requests per day.
- **Weekly Volume**: Stay under **80–120** connection requests per week.
- **Notes vs. No Notes**: Leaving `SEND_WITH_NOTE=false` in `.env` avoids LinkedIn's monthly personalized-note cap on free accounts and often yields higher acceptance rates. Set `SEND_WITH_NOTE=true` if you have LinkedIn Premium or want personalized notes from `CONNECTION_MESSAGE_TEMPLATES`.
- **Automatic Weekly Limit Protection**: If LinkedIn returns `HTTP 429 (FUSE_LIMIT_EXCEEDED)` or displays a weekly limit banner, ConnectPilot stops immediately without advancing your `SEARCH_LINK` page and marks the active weekly limit in `python cli.py stats`. Wait until your weekly LinkedIn quota resets before running again.

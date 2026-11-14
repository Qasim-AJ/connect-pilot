# ConnectPilot — Architecture & Developer Guide

## Project Overview

**ConnectPilot** is a modular Python + Selenium LinkedIn networking automation system engineered for reliability, multi-layered invitation verification, persistent browser-profile authentication, and zero hardcoded paths.

For full installation, `.env` configuration, and AI-agent onboarding instructions, see **[SETUP.md](SETUP.md)**.

## Core Architecture

- **`main.py`**: Primary entry point (`LinkedInBot` context manager). Coordinates browser initialization, login, search pagination continuity, `.env` `SEARCH_LINK` updates, and clean teardown.
- **`cli.py`**: Click + Rich CLI providing `browser-login`, `run`, `stats`, `sessions`, `clean`, `config`, and `test` commands.
- **`config/settings.py`**: Loads `.env` / YAML / JSON settings and resolves all relative paths (`EDGE_USER_DATA_DIR`, `CHROME_USER_DATA_DIR`, `DATABASE_PATH`, `SESSION_DIR`, `LOG_FILE`) relative to `PROJECT_ROOT`.
- **`config/security.py`**: Manages optional encrypted credentials (`Fernet` + `PBKDF2HMAC`) and OS keyring integration.
- **`modules/authenticator.py`**: Supports 4 authentication layers:
  1. Already signed-in dedicated browser profile (`EDGE_USER_DATA_DIR` / `CHROME_USER_DATA_DIR`)
  2. `LI_AT_COOKIE` injection
  3. Encrypted saved cookie sessions (`sessions/`)
  4. Interactive browser sign-in or `LINKEDIN_USERNAME` / `LINKEDIN_PASSWORD`
- **`modules/connector.py`**: Traverses LinkedIn's modern SDUI/React search cards and Shadow DOM modals (`#interop-outlet`), verifying every invitation via:
  1. Chromium CDP `Network.responseReceived` + `Network.getResponseBody` (`2xx` vs `429 FUSE_LIMIT_EXCEEDED`)
  2. Live DOM & Shadow DOM toast/banner inspection
  3. Button state transition (`Connect` -> `Pending`)
- **`modules/rate_limiter.py`**: SQLite-backed hourly/daily/weekly quota tracker (`data/bot.db`). Tracks verified `sent` vs `failed` attempts separately so failed clicks never inflate sent counts, and persists server-side weekly limit status.
- **`utils/anti_detection.py` & `utils/cdp_daemon.py`**: Cross-platform Edge/Chrome/Firefox stealth driver factory, browser binary auto-discovery, human behavior simulation, and single-WebSocket CDP bridge (`CDP_DAEMON_HOST` / `CDP_DAEMON_PORT`).

## Running Tests

```bash
python -m unittest discover -s tests -v
```

## Security Invariants

- **Never hardcode machine paths, usernames, cookies, or passwords** in source code or batch scripts. Use `.env` (documented in `.env.example` and `SETUP.md`).
- **Never commit** `.env`, `edge_profile/`, `chrome_profile/`, `sessions/`, `data/`, or `logs/`. Verify `.gitignore` coverage before committing.

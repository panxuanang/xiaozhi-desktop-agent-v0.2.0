from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
SRC_DIR = PACKAGE_DIR.parent
APP_DIR = SRC_DIR.parent
INSTALL_ROOT = APP_DIR.parent
RUNTIME_DIR = INSTALL_ROOT / "runtime"
PRIVATE_PYTHON = RUNTIME_DIR / "python.exe"
PRIVATE_PYTHONW = RUNTIME_DIR / "pythonw.exe"

LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
DATA_DIR = LOCALAPPDATA / "XiaoZhiAssistant"
STATE_DIR = DATA_DIR / "state"
LOG_DIR = DATA_DIR / "logs"
INBOX_DIR = DATA_DIR / "inbox"
OUTBOX_DIR = DATA_DIR / "outbox"
VERSIONS_DIR = DATA_DIR / "versions"
DSH_HOME = DATA_DIR / "dsh-home"
CONFIG_FILE = DATA_DIR / "config.json"
SECRETS_FILE = DATA_DIR / "secrets.json"
DB_FILE = DATA_DIR / "tasks.db"
QR_FILE = STATE_DIR / "weixin_login_qr.png"
WEIXIN_META_FILE = STATE_DIR / "weixin_account.json"
WEIXIN_SYNC_FILE = STATE_DIR / "weixin_sync.json"
WEIXIN_SEEN_FILE = STATE_DIR / "weixin_seen.json"
APP_LOG = LOG_DIR / "app.log"

UI_DIR = APP_DIR / "assets" / "ui"
SKILLS_DIR = APP_DIR / "assets" / "skills"

for p in (DATA_DIR, STATE_DIR, LOG_DIR, INBOX_DIR, OUTBOX_DIR, VERSIONS_DIR, DSH_HOME):
    p.mkdir(parents=True, exist_ok=True)

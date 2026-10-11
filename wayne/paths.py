"""Filesystem locations, anchored to the project root regardless of CWD."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
PACKAGE_DIR = Path(__file__).resolve().parent

ENV_FILE = ROOT_DIR / ".env"
WEB_DIR = ROOT_DIR / "web"
PROFILE_DIR = PACKAGE_DIR / "contacts" / "profiles"

# Everything the console remembers lives under data/: the database (see
# db_file) and the audio caches. Everything in here is personal and gitignored.
# WAYNE_DATA_DIR points it elsewhere — a sandbox to try things in without
# writing into anyone's real memories. .env is read here, not only in config:
# this module is imported first, and a sandbox set in .env that was decided
# before .env was loaded would quietly have written into the real thing.
load_dotenv(ENV_FILE)
DATA_DIR = Path(os.getenv("WAYNE_DATA_DIR") or ROOT_DIR / "data").expanduser()

# Where single-contact memory lived before the console became a phone book.
# Kept only so it can be migrated into Alfred's namespace on first run.
LEGACY_HISTORY_FILE = ROOT_DIR / "batcomputer_history.json"
LEGACY_VAULT_FILE = ROOT_DIR / "batcomputer_vault.txt"


def db_file():
    """
    The console's database (see wayne.store). A function, not a constant: tests
    and the evaluator point DATA_DIR somewhere else after this module loads.
    """
    return DATA_DIR / "wayne.db"


def contact_dir(contact_id):
    """
    Where an older console kept a contact's files (history.json, texts.json,
    vault.txt, …) before the database — the layout the importer reads once and
    an export for going back writes (see wayne.store.legacy).
    """
    return DATA_DIR / contact_id

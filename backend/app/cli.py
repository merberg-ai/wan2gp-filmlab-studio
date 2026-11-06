"""Admin CLI, run inside the FilmLab distro.

    python -m app.cli set-owner --username you            # prompts for a password
    python -m app.cli set-owner --username you --generate # writes a random one to a 600 file
    python -m app.cli warmup [--yes] [--all]             # list (or download) the WanGP models FilmLab uses
    python -m app.cli has-owner                           # exit 0 if an owner login exists (for the installer)
"""

import argparse
import asyncio
import getpass
import os
import secrets
import sys
from pathlib import Path

from .auth import set_owner
from .config import Settings
from .db import Database

GENERATED = Path.home() / ".config" / "filmlab" / "owner-initial-password"


async def _set_owner(args):
    db = Database(Settings().db_path)
    try:
        if args.generate:
            password = secrets.token_urlsafe(18)
            GENERATED.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(GENERATED, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(password + "\n")
        else:
            password = getpass.getpass("New owner password (12+ characters): ")
            if password != getpass.getpass("Repeat: "):
                sys.exit("passwords differ")
        try:
            await set_owner(db, args.username, password)
        except ValueError as exc:
            sys.exit(str(exc))
        print(f"owner '{args.username}' set" + (f"; password written to {GENERATED} (mode 600)" if args.generate else ""))
        print("all existing sessions were signed out")
    finally:
        db.close()


async def _has_owner() -> bool:
    db = Database(Settings().db_path)
    try:
        return bool(await db.fetchone("SELECT 1 FROM owner LIMIT 1"))
    finally:
        db.close()


def main():
    ap = argparse.ArgumentParser(prog="filmlab-admin")
    sub = ap.add_subparsers(dest="cmd", required=True)
    so = sub.add_parser("set-owner", help="create or replace the owner login")
    so.add_argument("--username", required=True)
    so.add_argument("--generate", action="store_true", help="generate a random password into a 600 file")
    wu = sub.add_parser("warmup", help="list the WanGP models FilmLab uses; --yes downloads the missing ones")
    wu.add_argument("--yes", action="store_true", help="run a tiny generation per missing model (downloads it)")
    wu.add_argument("--all", action="store_true", help="run every default model once, even installed ones")
    sub.add_parser("has-owner", help="exit 0 if an owner login exists")
    args = ap.parse_args()
    if args.cmd == "set-owner":
        asyncio.run(_set_owner(args))
    elif args.cmd == "warmup":
        from .warmup import warmup
        sys.exit(asyncio.run(warmup(args.yes, args.all, say=lambda m: print(m, flush=True))))
    elif args.cmd == "has-owner":
        sys.exit(0 if asyncio.run(_has_owner()) else 1)


if __name__ == "__main__":
    main()

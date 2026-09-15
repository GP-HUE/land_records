#!/usr/bin/env python3
"""Demo-data seeder for HOSTED deployments (Render free tier etc.).

The free-tier disk is EPHEMERAL — the database is wiped on every
deploy/restart. This script recreates a rich demo state on every boot
so the hosted site always works: accounts, land records (verified /
pending / AI-routed / draft), a mutation application, and sample scans
in the uploads folder.

The actual seeding logic now lives in ``landrec/seed_demo.py`` (the app
also auto-seeds on first boot for LOCAL installs — see that module).
This script NEVER touches an existing database that already has documents,
so it is safe to run on every container start.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from landrec import seed_demo  # noqa: E402

if __name__ == "__main__":
    seed_demo.seed_demo_data(force="--force" in sys.argv)

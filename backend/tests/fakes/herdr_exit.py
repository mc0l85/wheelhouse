#!/usr/bin/env python3
"""Fake herdr client that exits immediately (status FAKE_HERDR_EXIT, default 0)."""
import os, sys
sys.exit(int(os.environ.get("FAKE_HERDR_EXIT", "0")))

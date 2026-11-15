#!/usr/bin/env python3
"""Compare two run_e2e.py result files byte-for-byte: compare.py A.json B.json (exit 1 on any difference)."""
import json, sys
a, b = (json.load(open(p))["scenarios"] for p in sys.argv[1:3])
bad = 0
for k in sorted(set(a) | set(b)):
    same = k in a and k in b and a[k]["raw_hex"] == b[k]["raw_hex"]
    print(("IDENTICAL " if same else "DIFFERENT ") + k)
    bad += not same
sys.exit(1 if bad else 0)

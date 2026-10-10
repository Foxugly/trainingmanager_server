"""Systemd hardening of the long-running units (OPERATIONS.md §3.22), checked on the files.

A unit file is only exercised in production: these guards keep the hardening block, and the
Celery pool choice, from being lost in a later edit.
"""

import unittest
from pathlib import Path


def _repo_root():
    for parent in Path(__file__).resolve().parents:
        if (parent / ".git").exists():
            return parent
    raise RuntimeError("repository root not found")


UNITS_DIR = _repo_root() / "deploy/systemd"
UNITS = ['tm-gunicorn']
SOLO_CELERY = []
DIRECTIVES = ['NoNewPrivileges=yes', 'PrivateTmp=yes', 'ProtectSystem=full', 'ProtectKernelTunables=yes', 'ProtectControlGroups=yes', 'RestrictSUIDSGID=yes']


def read(unit):
    return (UNITS_DIR / f"{unit}.service").read_text(encoding="utf-8")


class SystemdHardeningTests(unittest.TestCase):
    def test_every_long_running_unit_is_hardened(self):
        for unit in UNITS:
            text = read(unit)
            for directive in DIRECTIVES:
                self.assertIn(directive, text, (unit, directive))
            # strict would make /var/www read-only: media, beat schedule, collectstatic break.
            self.assertNotIn("ProtectSystem=strict", text.splitlines(), unit)

    def test_no_unit_calls_sudo(self):
        """NoNewPrivileges=yes kills sudo inside the unit."""
        for unit in UNITS:
            for line in read(unit).splitlines():
                if line.startswith("Exec"):
                    self.assertNotIn("sudo", line, unit)

    def test_celery_pool_matches_the_tasks(self):
        for unit in SOLO_CELERY:
            text = read(unit)
            self.assertIn("--pool=solo", text, unit)
            self.assertNotIn("--concurrency", text, unit)

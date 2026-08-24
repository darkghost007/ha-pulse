"""Gemeinsame Test-Hilfen."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def fixture_state() -> dict:
    return json.loads((Path(__file__).parent / "fixtures" / "state.json").read_text())

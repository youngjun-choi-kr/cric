"""Paths, environment and model settings shared by every module."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.getenv("CRIC_DATA_DIR", ROOT / "data"))
DATA.mkdir(parents=True, exist_ok=True)

load_dotenv(ROOT / ".env")

TOKEN_FACTORY_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"

# Token Factory catalog IDs; override in .env if an account lists them differently.
MODEL_NANO = os.getenv("MODEL_NANO", "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B")
MODEL_SUPER = os.getenv("MODEL_SUPER", "nvidia/nemotron-3-super-120b-a12b")
MODEL_VL = os.getenv("MODEL_VL", "nvidia/Nemotron-3.5-Super-VL")
MODEL_ULTRA = os.getenv("MODEL_ULTRA", "nvidia/Nemotron-3-Ultra-550b-a55b")

# USD per 1M tokens (input, output), Token Factory public catalog.
PRICES = {
    MODEL_NANO: (0.06, 0.24),
    MODEL_SUPER: (0.30, 0.90),
    MODEL_VL: (0.30, 0.90),
    MODEL_ULTRA: (1.00, 3.00),
}


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} is not set. Copy .env.example to .env and fill it in (see SETUP.md).")
    return value

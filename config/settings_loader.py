# ════════════════════════════════════════════════════════════════════
#  config/settings_loader.py – centralised settings utility
# ════════════════════════════════════════════════════════════════════
"""
• loads config/settings.json
• falls back to hard-coded defaults when the file is missing/invalid
• applies optional overrides from a .env file (python-dotenv)

The resulting dict can be imported anywhere in the project:

    from config.settings_loader import load_settings
    SETTINGS = load_settings()
"""

# ───────────────────────────────────────────────────────── Logging ──
import logging

LOGGER = logging.getLogger(__name__)  # inherit root config from main

# ───────────────────────────────────────────────────────── Imports ──
from dotenv import load_dotenv
import json
import os
from typing import Any, Dict

# ───────────────────────────────────────────── Paths & defaults ──
DEFAULT_SETTINGS_PATH: str = "config/settings.json"

FALLBACK_SETTINGS: Dict[str, Any] = {
    "safety": {
        "profanity_filter": True,
        "sensitivity_level": "moderate",
        "log_triggered_filters": True,
        "blocked_response_template": "I'm unable to respond to that request due to safety policies.",
    },
    "memory": {  # ← new default block
        "backend": "none",  # "in_memory", "redis", …
        "enabled": False,
    },
    "prompt_matching": {
        "fuzzy_matching_enabled": True,
        "fuzzy_cutoff": 0.7,
        "enable_alias_diagnostics": True,
    },
    "generation": {
        "max_new_tokens": 100,
        "temperature": 0.5,
        "top_p": 0.9,
        "do_sample": True,
    },
    "logging": {
        "debug_mode": True,
        "log_level": "DEBUG",
        "log_to_file": False,
        "log_file_path": "logs/chatbot_debug.log",
        "prompt_preview": False,
    },
    "ui": {
        "enable_playground_autorun": False,
        "show_advanced_settings": True,
    },
    "context": {
        "max_history_turns": 5,
        "max_prompt_tokens": 512,
    },
}


# ───────────────────────────────────────────────────────── Loader ──
def load_settings(filepath: str = DEFAULT_SETTINGS_PATH) -> Dict[str, Any]:
    """Load settings from JSON and apply .env overrides."""
    load_dotenv()  # make .env variables available via os.getenv

    # ── read file or fall back ───────────────────────────────────────
    try:
        if not os.path.exists(filepath):
            LOGGER.warning("[Settings] %s not found – using defaults", filepath)
            settings: Dict[str, Any] = dict(FALLBACK_SETTINGS)
        else:
            with open(filepath, "r", encoding="utf-8") as f:
                data: Any = json.load(f)

            if not isinstance(data, dict):
                raise ValueError("settings.json must contain a JSON object")

            # Shallow copy to ensure a real Dict[str, Any]
            settings = dict(data)
            LOGGER.info("[Settings] Loaded from %s", filepath)

    except Exception as exc:
        LOGGER.error("[Settings] Failed to load %s: %s – using defaults", filepath, exc)
        settings = dict(FALLBACK_SETTINGS)

    # ── .env overrides ───────────────────────────────────────────────
    def env_bool(name: str, default: bool) -> bool:
        val = os.getenv(name)
        return default if val is None else val.lower() == "true"

    # logging
    log_cfg = settings.setdefault("logging", {})
    log_cfg["debug_mode"] = env_bool(
        "DEBUG_MODE", bool(log_cfg.get("debug_mode", False))
    )

    # context
    if (v := os.getenv("MAX_HISTORY_TURNS")) is not None:
        settings.setdefault("context", {})["max_history_turns"] = int(v)

    # memory backend / enable
    mem_cfg = settings.setdefault("memory", {})
    if mem_backend := os.getenv("MEMORY_BACKEND"):
        mem_cfg["backend"] = mem_backend
    if (mem_enabled := os.getenv("MEMORY_ENABLED")) is not None:
        mem_cfg["enabled"] = mem_enabled.lower() == "true"

    # ── debug prints ─────────────────────────────────────────────────
    LOGGER.debug("[Settings] DEBUG_MODE = %s", settings["logging"]["debug_mode"])
    LOGGER.debug(
        "[Settings] MAX_HISTORY_TURNS = %s", settings["context"]["max_history_turns"]
    )
    LOGGER.debug(
        "[Settings] MEMORY backend = %s | enabled = %s",
        settings["memory"]["backend"],
        settings["memory"]["enabled"],
    )

    return settings

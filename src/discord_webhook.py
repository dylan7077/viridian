"""Optional bridge: mirror website grades into a Discord channel via a webhook.

This is INERT by default — if ``DISCORD_WEBHOOK_URL`` is not set in the environment
it does nothing, so the site never posts anywhere it shouldn't. Set the env var to a
channel webhook URL to switch it on; then every website grade is echoed into Discord,
giving one shared feed of cards checked on the site *and* via the bot.
"""
from __future__ import annotations

import json
import os

import requests

import config  # noqa: F401 — importing config runs load_dotenv() so the env var is populated

WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()


def enabled() -> bool:
    return bool(WEBHOOK_URL)


def _caption(result: dict, source: str) -> str:
    card = (result.get("match") or {}).get("card") or {}
    name = card.get("name") or "A card"
    slab = result.get("slab") or {}
    g = slab.get("grade") if slab.get("grade") is not None else (result.get("grade") or {}).get("overall")
    gtxt = f"PSA {g}" if g is not None else "graded"
    return f"**{name}** — {gtxt} · via {source}"


def post_result(result: dict, source: str = "the website") -> bool:
    """Mirror a grade into the webhook channel using the clean share-card image.

    For an identified card we render the same polished 1200x630 share card the
    website/bot show and upload it as the message image. Unshareable results (no
    card identified) fall back to the bot's text embed. Returns True if sent.
    Never raises.
    """
    if not WEBHOOK_URL or not result or not result.get("ok"):
        return False
    try:
        from src import sharecard   # lazy: only pay the Pillow import when used
        png = sharecard.render(result)
        if png:
            r = requests.post(
                WEBHOOK_URL,
                data={"payload_json": json.dumps({"content": _caption(result, source)})},
                files={"file": ("viridian-grade.png", png, "image/png")},
                timeout=10)
            return r.status_code in (200, 204)
        # Not shareable (couldn't identify) — keep the informative text embed.
        from src import bot   # lazy import: avoids paying bot's import cost unless used
        embed = bot.build_embed(result).to_dict()
        # The overlay is an attachment:// image we don't upload over a JSON webhook —
        # drop it so Discord doesn't render a broken image. The card thumbnail stays.
        embed.pop("image", None)
        existing = embed.get("footer", {}).get("text", "")
        embed["footer"] = {"text": f"via {source} · {existing}".strip(" ·")}
        r = requests.post(WEBHOOK_URL, json={"embeds": [embed]}, timeout=8)
        return r.status_code in (200, 204)
    except Exception:
        return False

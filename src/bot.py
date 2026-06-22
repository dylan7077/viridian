"""Discord front-end: send a card photo, get a grade + value back.

Thin client — it does NO grading itself. It POSTs the photo to the website's
`/api/grade` (config.WEB_BASE_URL) and renders the JSON result, so the bot and the
site share one engine + one database with no duplicated index. Runs anywhere with
just DISCORD_TOKEN + WEB_BASE_URL.

Run with:  python -m src.bot
"""
from __future__ import annotations

import base64
import io
import logging

import aiohttp
import discord

import config

log = logging.getLogger("viridian.bot")

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


async def _grade_via_api(data: bytes) -> dict:
    """Send the photo to the website to grade; return the result JSON."""
    form = aiohttp.FormData()
    form.add_field("file", data, filename="card.jpg", content_type="image/jpeg")
    form.add_field("source", "bot")        # recorded into the shared feed as a bot grade
    timeout = aiohttp.ClientTimeout(total=150)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.post(f"{config.WEB_BASE_URL}/api/grade", data=form) as r:
            if r.status == 503:           # grader busy with another card — not an error
                return await r.json()
            r.raise_for_status()
            return await r.json()


_GRADE_NAMES = {
    10: "GEM MINT", 9: "MINT", 8: "NM-MT", 7: "NEAR MINT",
    6: "EX-MT", 5: "EX", 4: "VG-EX", 3: "VG", 2: "GOOD", 1: "POOR",
}
_GRADE_EMOJI = {
    10: "\U0001f451", 9: "\U0001f31f", 8: "\U00002b50",
    7: "\U0001f4ab", 6: "\U0001f537", 5: "\u26a1",
    4: "\u2b06\ufe0f", 3: "\u2b06\ufe0f", 2: "\u2b07\ufe0f", 1: "\u2b07\ufe0f",
}
_FULL = "\u2588"
_EMPTY = "\u2591"


def _grade_label(g) -> str:
    if g is None:
        return "\u2014"
    return f"PSA {g} \u00b7 {_GRADE_NAMES.get(g, '')}".strip()


def _bar(g: int | None, n: int = 10) -> str:
    if g is None:
        return _EMPTY * n
    return _FULL * max(0, min(n, g)) + _EMPTY * max(0, n - g)


def _subgrade_block(grade: dict) -> str:
    """Monospace, code-block subgrade table so the bars line up perfectly in Discord."""
    def row(label: str, g, extra: str = "") -> str:
        gv = str(g) if g is not None else "?"
        line = f"{label:<10}{_bar(g)} {gv:>2}"
        return f"{line}   {extra}" if extra else line

    c = grade.get("centering", {})
    rows = []
    if c.get("ok"):
        rows.append(row("Centering", c.get("grade"),
                        f"{c['left_right']} L·R · {c['top_bottom']} T·B"))
    else:
        rows.append(f"{'Centering':<10}— couldn't measure")
    rows.append(row("Corners", grade.get("corners", {}).get("grade")))
    rows.append(row("Edges",   grade.get("edges", {}).get("grade")))
    rows.append(row("Surface", grade.get("surface", {}).get("grade")))
    return "```\n" + "\n".join(rows) + "\n```"


def _overlay_file(result: dict) -> discord.File | None:
    overlay = result.get("overlay")
    if not overlay or not overlay.startswith("data:image/png;base64,"):
        return None
    b64 = overlay.split(",", 1)[1]
    raw = base64.b64decode(b64)
    return discord.File(io.BytesIO(raw), filename="overlay.png")


def _sharecard_file(result: dict) -> discord.File | None:
    """The clean 1200x630 share-card image for an identified grade, or None."""
    try:
        from src import sharecard   # lazy: only pay the Pillow import when used
        png = sharecard.render(result)
        if not png:
            return None
        return discord.File(io.BytesIO(png), filename="viridian-grade.png")
    except Exception:
        return None


def _result_post_kwargs(result: dict) -> dict:
    """Fresh send-kwargs for a grade result (share card or text embed + link buttons).

    Built fresh on each call because a discord.File's stream is consumed once sent,
    so a retry/fallback needs a new File object.
    """
    kwargs: dict = {}
    card = _sharecard_file(result)
    if card is not None:                       # identified → clean share-card image
        kwargs["file"] = card
    else:                                      # couldn't identify → text embed + overlay
        kwargs["embed"] = build_embed(result)
        overlay = _overlay_file(result)
        if overlay:
            kwargs["file"] = overlay
    view = _result_view(result)
    if view:
        kwargs["view"] = view
    return kwargs


async def _results_channel(mentioned_in):
    """The configured results channel, unless we were mentioned in it already (then
    reply in place). Returns None if unset/unreachable → caller replies in place."""
    rid = config.DISCORD_RESULTS_CHANNEL_ID
    if not rid.isdigit() or str(getattr(mentioned_in, "id", "")) == rid:
        return None
    try:
        return client.get_channel(int(rid)) or await client.fetch_channel(int(rid))
    except Exception:
        return None


def _ack_text(result: dict, where: str) -> str:
    card = (result.get("match") or {}).get("card") or {}
    name = card.get("name") or "your card"
    slab = result.get("slab") or {}
    g = slab.get("grade") if slab.get("grade") is not None else (result.get("grade") or {}).get("overall")
    grade = f" — {_grade_label(g)}" if g is not None else ""
    return f"✅ Graded **{name}**{grade} → posted in {where}"


def build_embed(result: dict) -> discord.Embed:
    if not result.get("ok"):
        return discord.Embed(title="Couldn't grade that",
                             description=result.get("message", "Unknown error."),
                             color=0xE5736B)

    grade = result["grade"]
    overall = grade["overall"]
    slab = result.get("slab")

    grade_source = slab.get("grade") if slab and slab.get("grade") is not None else overall
    color = 0x2FE0B0 if (grade_source or 0) >= 9 else \
            0xE6C97A if (grade_source or 0) >= 7 else \
            0x8FA39B
    emoji = _GRADE_EMOJI.get(grade_source, "")

    match = result.get("match")
    card_name = (match and match["card"].get("name")) or "Card"
    title = f"{emoji} {card_name} \u2014 {_grade_label(grade_source)}"

    if slab:
        cert = slab.get("cert", "")
        title += " \U0001f3f7\ufe0f"  # label emoji for slab

    emb = discord.Embed(title=title, color=color)
    emb.set_author(name="Viridian Grading Lab")

    # ── hero grade ──
    desc_lines = []
    if slab:
        cert_str = f" \u00b7 cert {slab['cert']}" if slab.get("cert") else ""
        desc_lines.append(f"**Graded slab** \u2014 PSA {slab.get('grade', '?')} authenticated"
                          f"{cert_str}")
    else:
        desc_lines.append(f"**Overall** \u2014 {_grade_label(overall)}")
    # No coverage warning here: if the card identified, it worked (no need to nag);
    # if it didn't, the "Couldn't identify" field below gives tailored, correct advice.
    emb.description = "\n".join(desc_lines)

    # ── subgrades with visual bars (monospace table) ──
    emb.add_field(name="Subgrades", value=_subgrade_block(grade), inline=False)

    # ── card identification ──
    if match:
        card = match["card"]
        set_name = card.get("set", "?")
        num = card.get("number", "?")
        method = match.get("method", "phash")
        method_icon = "\U0001f3af" if method == "orb" else "\U0001f50d"
        dist = match.get("distance", "?")
        orb_s = match.get("orb_score")
        extra = f" (ORB: {orb_s} matches)" if orb_s is not None else ""
        emb.add_field(
            name="Card",
            value=f"**{card.get('name', '?')}** \u00b7 *{set_name}* #{num}\n"
                  f"{method_icon} {method.upper()} match \u2014 "
                  f"dist {dist}{extra}",
            inline=False,
        )
        if card.get("image"):
            emb.set_thumbnail(url=card["image"])
    elif result.get("uncertain"):
        # Be honest and helpful instead of guessing wrong. The warning is already
        # tailored to the cause; only the weak-match case needs an extra retake tip.
        parts = [result.get("match_warning", "Not confident enough to name it.")]
        guess = result.get("guess")
        if guess and guess.get("name"):
            gnum = guess.get("number")
            parts.append(f"Closest guess (not confident): *{guess['name']}*"
                         f"{' #' + gnum if gnum else ''}")
        if result.get("unsure_reason") == "weak_match":
            parts.append("\U0001f4a1 A flatter, glare-free shot — or the website's "
                         "corner-align tool — usually fixes it.")
        emb.add_field(name="Couldn’t identify the card",
                      value="\n".join(parts), inline=False)

    # ── pricing ──
    value = result.get("value")
    if value and value.get("ok") and value.get("values"):
        mult = value.get("graded_multiplier")
        lines = []
        for v in value["values"]:
            sym = v.get("symbol", "$")
            raw = v.get("raw")
            graded = v.get("graded")
            if raw is not None and graded is not None:
                lines.append(
                    f"{sym}**{graded:.2f}** graded \u00b7 {sym}{raw:.2f} raw")
            elif raw is not None:
                lines.append(f"{sym}{raw:.2f}")
        val_title = "Value"
        if mult is not None:
            val_title += f" (\u00d7{mult}\u00d7 graded premium)"
        emb.add_field(name=val_title,
                      value="\n".join(lines), inline=False)

    # ── overlay ──
    if result.get("overlay", "").startswith("data:image/png;base64,"):
        emb.set_image(url="attachment://overlay.png")

    # ── capture quality ── a blurry/glare photo grades unreliably; tell them to retake.
    if result.get("capture_warning"):
        emb.add_field(name="\U0001f4f7 Photo quality",
                      value=result["capture_warning"] + " A clearer photo grades more accurately.",
                      inline=False)

    # ── footer ──
    footer = f"\U0001f4c7 {int(result.get('indexed') or 0):,} cards indexed"
    # The uncertain case already has its own field; only surface other warnings here.
    if result.get("match_warning") and not result.get("uncertain"):
        footer += "  \u00b7 \u26a0 " + result["match_warning"]
    emb.set_footer(text=footer)
    return emb


def _result_view(result: dict) -> "discord.ui.View | None":
    """Link buttons under a grade reply: a shareable result page (for identified
    cards) and the website's corner-align tool (pre-loaded with this photo)."""
    token = result.get("align_token")
    share_url = result.get("share_url")
    if not token and not share_url:
        return None
    try:
        view = discord.ui.View()
        if share_url:
            view.add_item(discord.ui.Button(
                label="View & share", url=share_url,
                style=discord.ButtonStyle.link, emoji="\U0001f517"))
        if token:
            view.add_item(discord.ui.Button(
                label="Adjust corners on the web",
                url=f"{config.WEB_BASE_URL}/?align={token}",
                style=discord.ButtonStyle.link, emoji="\U0001f527"))
        return view
    except Exception:
        return None


@client.event
async def on_ready():
    print(f"Logged in as {client.user}. Grading via {config.WEB_BASE_URL}")


@client.event
async def on_message(message: discord.Message):
    if message.author == client.user:
        return
    images = [a for a in message.attachments
              if (a.content_type or "").startswith("image")]
    if not images:
        return
    if client.user not in message.mentions:
        return
    async with message.channel.typing():
        try:
            data = await images[0].read()
            result = await _grade_via_api(data)     # the website grades + records it
            if result.get("busy"):
                await message.reply(embed=discord.Embed(
                    title="⏳ One moment",
                    description=result.get("message", "Grading another card — try again shortly."),
                    color=0xE6C97A), mention_author=False)
                return
            # Post the result to the dedicated results channel (keeps the submission
            # channel clean), leaving a short ack where it was requested.
            results_ch = await _results_channel(message.channel)
            if results_ch is None:
                # No results channel configured → reply in place (original behaviour).
                await message.reply(mention_author=False, **_result_post_kwargs(result))
            else:
                posted_to = None
                try:
                    await results_ch.send(**_result_post_kwargs(result))   # full card + buttons
                    posted_to = results_ch.mention
                except discord.HTTPException:
                    # Bot lacks send perms there → fall back to the webhook (image only,
                    # no buttons). Grant the bot Send Messages in that channel to upgrade.
                    from src import discord_webhook
                    if discord_webhook.enabled() and await client.loop.run_in_executor(
                            None, discord_webhook.post_result, result, "the bot"):
                        posted_to = results_ch.mention
                if posted_to:
                    await message.reply(_ack_text(result, posted_to), mention_author=False)
                else:
                    await message.reply(mention_author=False, **_result_post_kwargs(result))
            # Award the "PSA 10 Club" role on a perfect 10 (never blocks grading).
            try:
                _g = result.get("grade") or {}
                _slab = result.get("slab") or {}
                _final = _slab.get("grade") if _slab.get("grade") is not None else _g.get("overall")
                if _final == 10 and message.guild and isinstance(message.author, discord.Member):
                    _role = discord.utils.get(message.guild.roles, name="🔟 PSA 10 Club")
                    if _role and _role not in message.author.roles:
                        await message.author.add_roles(_role, reason="Graded a PSA 10 on Viridian")
                        await message.channel.send(
                            f"🔟 {message.author.mention} just pulled a **PSA 10** — "
                            f"welcome to the **PSA 10 Club**! 🎉")
            except Exception:
                pass
        except Exception:
            # Log the real error (with traceback) server-side; show users a clean message
            # rather than leaking raw exception text / internal paths into a public channel.
            log.exception("grade request failed for message %s", getattr(message, "id", "?"))
            await message.reply(
                embed=discord.Embed(
                    title="Something went wrong",
                    description="Couldn't grade that one — try a clearer, flatter photo "
                                "of the whole card, or check back in a moment.",
                    color=0xE5736B),
                mention_author=False)


def main():
    if not config.DISCORD_TOKEN:
        raise SystemExit("Set DISCORD_TOKEN in your .env first.")
    client.run(config.DISCORD_TOKEN)


if __name__ == "__main__":
    main()

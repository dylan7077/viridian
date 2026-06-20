"""Post a standing "visit the website" embed into a Discord channel and pin it.

Drives Discord -> website traffic (the site's navbar already drives the reverse).
Uses the bot token from .env. Run once; the message stays pinned.

  # 1. find the channel id you want:
  python -m scripts.post_site_link --list

  # 2. post + pin into that channel:
  python -m scripts.post_site_link --channel 123456789012345678

Flags:
  --list            list every text channel the bot can see (id · #name · guild)
  --channel <id>    channel to post into
  --url <url>       site url (default: https://94.72.104.185.sslip.io)
  --no-pin          post without pinning
"""
from __future__ import annotations

import argparse
import sys

import discord

import config

DEFAULT_URL = "https://94.72.104.185.sslip.io"


def build_link_embed(url: str) -> discord.Embed:
    emb = discord.Embed(
        title="Viridian Grading Lab",
        description=(
            "**Grade any Pokémon card for free.**\n"
            "Snap a photo and get the exact card, an estimated PSA grade, "
            "and its live market value in about 7 seconds — right in your browser.\n\n"
            f"\U0001f449 **{url.replace('https://', '')}**"
        ),
        color=0x2FE0B0,
        url=url,
    )
    emb.set_author(name="Viridian Grading Lab")
    emb.add_field(name="What you get",
                  value="✅ Free forever\n\U0001f50d Exact card ID\n"
                        "\U0001f3f7️ Estimated PSA grade\n\U0001f4b0 Live market value",
                  inline=False)
    emb.set_footer(text="Grade without guesswork · estimate only, not affiliated with PSA")
    return emb


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="list visible text channels and exit")
    ap.add_argument("--channel", type=int, help="channel id to post into")
    ap.add_argument("--url", default=DEFAULT_URL, help="website url")
    ap.add_argument("--no-pin", action="store_true", help="don't pin the message")
    args = ap.parse_args()

    if not config.DISCORD_TOKEN:
        print("DISCORD_TOKEN is not set in the environment / .env", file=sys.stderr)
        return 2
    if not args.list and not args.channel:
        print("Pass --list to find a channel, or --channel <id> to post.", file=sys.stderr)
        return 2

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        try:
            if args.list:
                for guild in client.guilds:
                    print(f"\n# {guild.name}  (guild {guild.id})")
                    for ch in guild.text_channels:
                        print(f"  {ch.id}  #{ch.name}")
                return

            channel = client.get_channel(args.channel) or await client.fetch_channel(args.channel)
            if channel is None:
                print(f"Channel {args.channel} not found / bot can't see it.", file=sys.stderr)
                return

            view = discord.ui.View()
            view.add_item(discord.ui.Button(
                label="Open Viridian", url=args.url,
                style=discord.ButtonStyle.link, emoji="\U0001f517"))

            msg = await channel.send(embed=build_link_embed(args.url), view=view)
            print(f"Posted to #{getattr(channel, 'name', args.channel)} (message {msg.id}).")
            if not args.no_pin:
                try:
                    await msg.pin()
                    print("Pinned.")
                except discord.Forbidden:
                    print("Posted, but couldn't pin (bot lacks Manage Messages here).",
                          file=sys.stderr)
        finally:
            await client.close()

    client.run(config.DISCORD_TOKEN)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

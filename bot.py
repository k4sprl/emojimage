import discord
from discord import app_commands, ui
from discord.ext import commands
import aiohttp
import re
import emoji
from emoji import demojize
import asyncio
from typing import Dict, List, Optional
import urllib.parse

EMOJI_REGEX = r"<(a?):([^:]+):(\d+)>"
MAX_ITEMS = 5
INVITE_URL = "https://discord.com/oauth2/authorize?client_id=1456291804994470064"
GITHUB_URL = "https://github.com/k4sprl/emojimage"
TOS_URL = "https://k4sprl.github.io/emojimage/#terms"
PRIVACY_URL = "https://k4sprl.github.io/emojimage/#privacy"
WEBSITE_URL = "https://k4sprl.github.io/emojimage/"

# custom UI emojis you added
OK_EMOJI = "<:ok:1458119160612520162>"
ERROR_EMOJI = "<:error:1458106447652192363>"
WARNING_EMOJI = "<:warning:1458106521497108594>"
INFO_EMOJI = "<:info:1458107083789697256>"
COOLDOWN_EMOJI = "<:cooldown:1458106692997746917>"
SOURCE_EMOJI = "<:source:1458116707385085952>"
TIMER_EMOJI = "<:timer:1456691289008509011>"
LINK_EMOJI = "<:link:1456686996381499433>"
EMOJI_ICON = "<:emoji:1458118535355175043>"
STICKER_ICON = "<:sticker:1456762041422581812>"
GITHUB_EMOJI = "<:github:1456700755313561793>"
TOP_EMOJI = "<:top:1458121333878816851>"
SETTINGS_EMOJI = "<:settings:1458121436177760390>"

# --- Bot ---------------------------------------------------------------------------------------
class EmojiBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.session: Optional[aiohttp.ClientSession] = None
        self.waiting_for_sticker: set[int] = set()

    async def setup_hook(self) -> None:
        self.session = aiohttp.ClientSession()
        await self.tree.sync(guild=None)
        print(f"Logged in as {self.user} — slash commands synced globally")

    async def close(self) -> None:
        if self.session:
            await self.session.close()
        await super().close()

bot = EmojiBot()

# --- Utils -------------------------------------------------------------------------------------
def extract_unicode_emojis(text: str) -> List[str]:
    return [c for c in text if c in emoji.EMOJI_DATA]

def twemoji_svg_url_for(char: str) -> str:
    codepoints = '-'.join(f"{ord(c):x}" for c in char)
    return f"https://twemoji.maxcdn.com/v/latest/72x72/{codepoints}.png?quality=lossless"

def format_custom_emoji_url(emoji_id: str, animated: bool) -> str:
    base = f"https://cdn.discordapp.com/emojis/{emoji_id}.webp"
    if animated:
        return f"{base}?quality=lossless&animated=true"
    return f"{base}?quality=lossless"

def sticker_media_url_from_parsed(parsed: urllib.parse.ParseResult) -> str:
    qs = urllib.parse.parse_qs(parsed.query or "")
    animated_flag = any(k.lower() == 'animated' and 'true' in [x.lower() for x in v] for k, v in qs.items()) or ('animated=true' in (parsed.query or "").lower())
    base = f"https://media.discordapp.net{parsed.path}"
    if animated_flag:
        return f"{base}?quality=lossless&animated=true"
    return f"{base}?quality=lossless"

def preserve_query_with_quality(parsed: urllib.parse.ParseResult) -> str:
    qs = urllib.parse.parse_qs(parsed.query or "")
    animated_flag = any(k.lower() == 'animated' and 'true' in [x.lower() for x in v] for k, v in qs.items()) or ('animated=true' in (parsed.query or "").lower())
    base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    if animated_flag:
        return f"{base}?quality=lossless&animated=true"
    return f"{base}?quality=lossless"

def is_url_animated(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed.query or "")
    if any(k.lower() == 'animated' and any(v.lower() == 'true' for v in vals) for k, vals in qs.items()):
        return True
    if 'animated=true' in (parsed.query or "").lower():
        return True
    if parsed.path.lower().endswith(('.gif', '.webm', '.apng')):
        return True
    return False

def sticker_note_from_url(url: str) -> Optional[str]:
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.lower()
    if path.endswith('.json'):
        return f"{WARNING_EMOJI} (.json Sticker) — Discord-only."
    if path.endswith('.png'):
        return f"{WARNING_EMOJI} (.png Sticker) — may require modern software; may appear static when sent by bots."
    return f"{WARNING_EMOJI} Sticker — may require modern software."

# --- UI: More Info button for default stickers ---------------------------------------------------
class StickerInfoButton(ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="More Info", style=discord.ButtonStyle.secondary, emoji=discord.PartialEmoji(name="info", id=1456687175679742106))
    async def more_info(self, interaction: discord.Interaction, _button: ui.Button) -> None:
        msg = (
            "Discord's default stickers are Canvas-based and not downloadable as files.\n\n"
            "They render inside Discord but aren't exposed as downloadable assets."
        )
        await interaction.response.send_message(msg, ephemeral=True)

# --- Core processing ---------------------------------------------------------------------------
async def process_input(source, text: str, stickers: List[discord.StickerItem]) -> None:
    is_interaction = isinstance(source, discord.Interaction)
    clean_input = (text or "").strip().replace('\u200b', '')

    custom_matches = list(re.finditer(EMOJI_REGEX, clean_input))
    unicode_emojis = extract_unicode_emojis(clean_input)

    items: List[Dict[str, Optional[str]]] = []

    # dedupe by full URL (including query) to avoid collapsing distinct variants
    def add_item(name: str, url: Optional[str], src: str, animated: bool = False) -> None:
        if not url:
            items.append({"name": name, "url": None, "src": src, "animated": animated})
            return
        norm = url.rstrip('/')
        for it in items:
            existing = it.get("url")
            if not existing:
                continue
            if existing.rstrip('/') == norm:
                return
        items.append({"name": name, "url": url, "src": src, "animated": animated})

    # Custom emojis
    for match in custom_matches:
        is_anim_flag, name, emoji_id = match.groups()
        animated = bool(is_anim_flag)
        final = format_custom_emoji_url(emoji_id, animated)
        add_item(name, final, "custom", animated=animated)

    # Unicode -> Twemoji PNG
    for uni in unicode_emojis:
        name = demojize(uni).replace(":", "")
        final = twemoji_svg_url_for(uni)
        add_item(name, final, "unicode", animated=False)

    # Stickers from message.stickers
    for s in stickers:
        try:
            full_sticker = await bot.fetch_sticker(s.id)
        except discord.NotFound:
            add_item(f"{s.name} (error)", None, "sticker_notfound")
            continue
        except discord.HTTPException as http_e:
            add_item(f"{s.name} (error)", None, f"sticker_http_{getattr(http_e, 'status', 'err')}")
            continue

        url = getattr(full_sticker, "url", None) or getattr(full_sticker, "asset_url", None)
        if not url:
            view = StickerInfoButton()
            msg_text = f"{LINK_EMOJI} Discord's default stickers are not compatible with Emojimage"
            if is_interaction:
                try:
                    await source.response.send_message(msg_text, view=view, ephemeral=True)
                except Exception:
                    await source.followup.send(msg_text, view=view, ephemeral=True)
            else:
                await source.channel.send(msg_text, view=view)
            return

        parsed = urllib.parse.urlparse(url)
        final = sticker_media_url_from_parsed(parsed)
        animated_flag = is_url_animated(final)
        add_item(full_sticker.name or "sticker", final, "sticker", animated=animated_flag)

    # Parse markdown links and bare CDN/sticker URLs
    MD_LINK_REGEX = r'\[([^\]]+)\]\((https?://[^\s)]+)\)'
    URL_EMOJI_REGEX = r'https?://(cdn\.discordapp\.com/emojis|media\.discordapp\.net/stickers|cdn\.discordapp\.com/stickers)/[^\s)]+'

    for m in re.finditer(MD_LINK_REGEX, clean_input):
        display_name, url = m.groups()
        if not re.search(URL_EMOJI_REGEX, url):
            continue
        parsed = urllib.parse.urlparse(url)
        is_sticker = '/stickers/' in parsed.path.lower()
        if is_sticker:
            final = sticker_media_url_from_parsed(parsed)
            animated_flag = is_url_animated(final)
            add_item(display_name, final, "markdown_sticker", animated=animated_flag)
            continue
        final = preserve_query_with_quality(parsed)
        animated_flag = is_url_animated(final)
        add_item(display_name, final, "markdown", animated=animated_flag)

    for m in re.finditer(URL_EMOJI_REGEX, clean_input):
        url = m.group(0)
        parsed = urllib.parse.urlparse(url)
        is_sticker = '/stickers/' in parsed.path.lower()
        if is_sticker:
            final = sticker_media_url_from_parsed(parsed)
            display = urllib.parse.unquote(parsed.path.split('/')[-1]) or final
            animated_flag = is_url_animated(final)
            add_item(display, final, "bare_sticker", animated=animated_flag)
            continue
        final = preserve_query_with_quality(parsed)
        display = urllib.parse.unquote(parsed.path.split('/')[-1]) or final
        animated_flag = is_url_animated(final)
        add_item(display, final, "bare", animated=animated_flag)

    if not items:
        msg = f"{ERROR_EMOJI} No valid emojis, stickers or links found."
        if is_interaction:
            await source.response.send_message(msg, ephemeral=True)
        else:
            await source.channel.send(msg)
        return

    # Defer if interaction so followups are allowed
    if is_interaction:
        try:
            await source.response.defer()
        except Exception:
            pass

    # Filter valid URLs
    valid_items = [i for i in items if i.get("url")]

    # Enforce max items after parsing/deduplication
    if len(valid_items) > MAX_ITEMS:
        msg = f"{ERROR_EMOJI} You can't request more than {MAX_ITEMS} items at once."
        if is_interaction:
            await source.followup.send(msg, ephemeral=True)
        else:
            await source.channel.send(msg)
        return

    # Sort: twemoji (unicode) first, then static, then animated
    def sort_key(it: Dict[str, Optional[str]]):
        if it.get("src") == "unicode":
            return (0, 0)
        if not it.get("animated", False):
            return (1, 0)
        return (2, 0)

    valid_items.sort(key=sort_key)

    # --------------------------
    # Helper for sending replies
    # --------------------------
    async def _send_response_blocking(content: str) -> None:
        if is_interaction:
            await source.followup.send(content)
            return
        try:
            if isinstance(source.channel, discord.DMChannel):
                await source.reply(content, mention_author=False)
            else:
                await source.channel.send(content)
        except Exception:
            await source.channel.send(content)

    async def _send_note_blocking(content: str) -> None:
        # sticker notes must be sent as normal messages (not replies)
        if is_interaction:
            await source.followup.send(content)
            return
        try:
            await source.channel.send(content)
        except Exception:
            try:
                await source.reply(content, mention_author=False)
            except Exception:
                pass

    # --------------------------
    # Output: single / multiple
    # --------------------------
    invisible_sep = "\u2063"  # invisible separator U+2063

    # Single item
    if len(valid_items) == 1:
        itm = valid_items[0]
        name = itm.get("name", "emoji")
        url = itm["url"]

        # Detect discord emoji (cdn emojis) but exclude stickers and unicode (twemoji)
        is_discord_emoji = url and ("/emojis/" in url) and ("/stickers/" not in url) and itm.get("src") != "unicode"
        zw_suffix = f" {invisible_sep}" if is_discord_emoji else ""
        msg = f"{LINK_EMOJI}[**{name}**]({url}){zw_suffix}"

        await _send_response_blocking(msg)

        # Sticker warning MUST be a separate normal message (not a reply)
        if "sticker" in itm.get("src", ""):
            note = sticker_note_from_url(url)
            if note:
                await _send_note_blocking(note)
        return

    # Multiple items
    lines: List[str] = []
    sticker_notes: List[str] = []

    for it in valid_items:
        name = it.get("name", "emoji")
        url = it["url"]

        is_discord_emoji = url and ("/emojis/" in url) and ("/stickers/" not in url) and it.get("src") != "unicode"
        zw_suffix = f" {invisible_sep}" if is_discord_emoji else ""
        lines.append(f"{LINK_EMOJI}[**{name}**]({url}){zw_suffix}")

        if "sticker" in it.get("src", ""):
            note = sticker_note_from_url(url)
            if note:
                sticker_notes.append(note)

    out_text = "\n".join(lines)
    await _send_response_blocking(out_text)

    for note in sticker_notes:
        await _send_note_blocking(note)

# --- Slash commands --------------------------------------------------------------------------------

@bot.tree.command(name="help", description="Show help message")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def help_cmd(interaction: discord.Interaction) -> None:
    help_text = (
        "## ❓Emojimage - Help\n"
        "**`/e2img`** - Extract images out of emojis.\n"
        "**`/s2img`** - Extract images out of stickers.\n"
        "**`/ping`** - Check bot latency.\n"
        "**`/invite`** - Add bot to your server or apps.\n"
        "**`/share`** - Share Emojimage with others.\n"
        "**`/about`** - About Emojimage.\n"
        "**`/tos`** - Link to Terms of Service.\n"
        "**`/privacy`** - Link to Privacy Policy.\n"
        "**`/website`** - Link to Website.\n"
        "**`/source`** - Link to GitHub.\n\n"
        "### I also support messages sent by others!\n"
        "If you add me to your apps via `/invite`, you can right-click (or hold on mobile) a message, click on **Apps** & select Emojimage.\n"
        "And of course, I work in DMs as well.\n"
    )
    await interaction.response.send_message(help_text, ephemeral=True)

@bot.tree.command(name="e2img", description="Extract images out of emojis")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
@app_commands.describe(emoji_input="Paste the custom emojis here")
async def e2img(interaction: discord.Interaction, emoji_input: str) -> None:
    await process_input(interaction, emoji_input, stickers=[])

@bot.tree.command(name="s2img", description="Extract images out of stickers")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def s2img(interaction: discord.Interaction) -> None:
    user_id = interaction.user.id
    bot.waiting_for_sticker.add(user_id)
    try:
        await interaction.response.send_message(f"{TIMER_EMOJI} Please send your sticker within 15 seconds.", ephemeral=True)

        def check(m: discord.Message) -> bool:
            return (
                m.author.id == user_id
                and m.channel.id == interaction.channel.id
                and getattr(m, "stickers", None)
                and len(m.stickers) > 0
            )

        try:
            msg: discord.Message = await bot.wait_for("message", timeout=15.0, check=check)
            await process_input(msg, msg.content, stickers=msg.stickers)
        except asyncio.TimeoutError:
            await interaction.followup.send(f"{ERROR_EMOJI} 15 seconds have passed, no sticker received. Try again.", ephemeral=True)
    finally:
        bot.waiting_for_sticker.discard(user_id)

@bot.tree.command(name="ping", description="Check bot latency")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def ping(interaction: discord.Interaction) -> None:
    latency_ms = round(bot.latency * 1000)
    await interaction.response.send_message(f"🏓Pong! **{latency_ms}ms.**", ephemeral=True)

@bot.tree.command(name="invite", description="Add bot to your server or apps")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def invite_cmd(interaction: discord.Interaction) -> None:
    view = ui.View()
    view.add_item(ui.Button(label="Invite Emojimage", url=INVITE_URL))
    await interaction.response.send_message("Click the button to invite the bot:", view=view, ephemeral=True)

@bot.tree.command(name="share", description="Share Emojimage with others (Sends a message with info and invite button)")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def share_cmd(interaction: discord.Interaction) -> None:
    promo = (
        "Hey! — I use **Emojimage** to extract original high-quality images out of emojis & stickers.\n"
        "Quickly turn an emoji into an image (with transparent background) for example.\n\n"
        "Tap the button below to add Emojimage to your apps and try it out!"
    )
    view = ui.View()
    view.add_item(ui.Button(label="Invite Emojimage", url=INVITE_URL))
    await interaction.response.send_message(promo, view=view, ephemeral=False)

@bot.tree.command(name="about", description="About Emojimage")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def about_cmd(interaction: discord.Interaction) -> None:
    about_text = (
        "## Emojimage — About\n"
        "• Extract original high quality images out of emojis & stickers.\n\n"
        "• Convert Discord emojis to direct CDN links\n"
        "• Provide high-quality Twemoji PNGs for Unicode emojis\n"
        "• Export server stickers (where possible). Default Canvas stickers are unsupported\n\n"
        "Privacy: Emojimage **does not store** images or message content persistently.\n"
        "**Please read our TOS** *`/tos`* **and Privacy Policy** *`/privacy`* **before using the bot.**"
    )
    await interaction.response.send_message(about_text, ephemeral=True)

@bot.tree.command(name="tos", description="Link to Terms of Service")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def tos_cmd(interaction: discord.Interaction) -> None:
    view = ui.View()
    view.add_item(ui.Button(label="View Terms of Service", url=TOS_URL))
    await interaction.response.send_message("Terms of Service:", view=view, ephemeral=True)

@bot.tree.command(name="privacy", description="Link to Privacy Policy")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def privacy_cmd(interaction: discord.Interaction) -> None:
    view = ui.View()
    view.add_item(ui.Button(label="View Privacy Policy", url=PRIVACY_URL))
    await interaction.response.send_message("Privacy Policy:", view=view, ephemeral=True)

@bot.tree.command(name="source", description="Link to source code / GitHub")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def source_cmd(interaction: discord.Interaction) -> None:
    view = ui.View()
    view.add_item(ui.Button(label="View Source Code", emoji=SOURCE_EMOJI, url=GITHUB_URL))
    await interaction.response.send_message("View the source code here:", view=view, ephemeral=True)

@bot.tree.command(name="website", description="Link to website")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def website_cmd(interaction: discord.Interaction) -> None:
    view = ui.View()
    view.add_item(ui.Button(label="View Website", url=WEBSITE_URL))
    await interaction.response.send_message("Website:", view=view, ephemeral=True)

# --- Context Menu (Right-click Message -> Apps) ---------------------------------------
@bot.tree.context_menu(name="Emojimage")
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
async def emojimage_context(interaction: discord.Interaction, message: discord.Message) -> None:
    stickers = message.stickers if getattr(message, "stickers", None) else []
    content = message.content or ""
    await process_input(interaction, content, stickers)

# --- Message listener (DMs only) -----------------------------------------------------
@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return

    user_id = message.author.id
    if user_id in bot.waiting_for_sticker:
        return

    if not isinstance(message.channel, discord.DMChannel):
        return

    content = message.content or ""
    has_custom = bool(re.search(EMOJI_REGEX, content))
    has_unicode = bool(extract_unicode_emojis(content))
    has_sticker = bool(getattr(message, "stickers", None) and len(message.stickers) > 0)
    has_links = bool(re.search(r'https?://(cdn\.discordapp\.com/emojis|media\.discordapp\.net/stickers|cdn\.discordapp\.com/stickers)/[^\s)]+', content) or re.search(r'\[([^\]]+)\]\((https?://[^\s)]+)\)', content))

    if not (has_custom or has_unicode or has_sticker or has_links):
        return

    stickers = message.stickers if message.stickers else []
    await process_input(message, content, stickers)

# --- Run -----------------------------------------------------------------------------------------
if __name__ == "__main__":
    bot.run("PUT_BOT_TOKEN_HERE")

# main.py – Red Herring bot complet et fonctionnel (Pagination persistante)

import os
import threading
import discord
from discord import app_commands
from discord.ext import commands
import asyncpg
import aiohttp
from aiohttp import ClientTimeout
from flask import Flask
from typing import Optional, List, Dict
from urllib.parse import quote_plus
from datetime import datetime, timedelta

# ————— Configuration —————
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL  = os.getenv("DATABASE_URL")
TMDB_API_KEY  = os.getenv("TMDB_API_KEY")
GUILD_ID      = os.getenv("GUILD_ID")  # facultatif pour dev

# ————— Visuels —————
COLOR_MAP = {"Série":0x1abc9c, "Animé":0xe74c3c, "Webtoon":0x9b59b6, "Manga":0xf1c40f}
TYPE_EMOJIS = {"Série":"📺","Animé":"🎥","Webtoon":"📱","Manga":"📚"}
STATUS_EMOJIS = {"À voir":"🔴","En cours":"🟠","Terminé":"🟢"}

# ————— Flask healthcheck —————
app = Flask(__name__)
@app.route("/")
def home():
    return "Red Herring Bot en ligne"

def run_web():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT",8000)))

# ————— Helpers TMDB —————
_thumbnail_cache: Dict[str,str] = {}
async def fetch_thumbnail(title: str, content_type: str) -> Optional[str]:
    key = f"{title}|{content_type}"
    if key in _thumbnail_cache:
        return _thumbnail_cache[key]
    if not TMDB_API_KEY:
        return None
    kind = "tv" if content_type in ("Série","Animé") else "movie"
    url = f"https://api.themoviedb.org/3/search/{kind}?api_key={TMDB_API_KEY}&query={quote_plus(title)}"
    try:
        timeout = ClientTimeout(total=3)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(url) as resp:
                data = await resp.json()
    except:
        return None
    for res in data.get("results", []):
        if res.get("poster_path"):
            thumb = f"https://image.tmdb.org/t/p/w300{res['poster_path']}"
            _thumbnail_cache[key] = thumb
            return thumb
    return None

# ————— Normalisation —————
def normalize_type(v:str)->str:
    m={"série":"Série","serie":"Série","animé":"Animé","anime":"Animé","webtoon":"Webtoon","manga":"Manga"}
    return m.get(v.lower().strip(), v.capitalize())

def normalize_status(v:str)->str:
    m={"à voir":"À voir","a voir":"À voir","en cours":"En cours","terminé":"Terminé","termine":"Terminé"}
    return m.get(v.lower().strip(), v.capitalize())

# ————— Pagination View (persistent) —————
class PaginationView(discord.ui.View):
    def __init__(self, embeds: List[discord.Embed], *, timeout: Optional[float] = None):
        super().__init__(timeout=timeout)
        self.embeds = embeds
        self.index = 0

    @discord.ui.button(custom_id="pagination_prev", emoji="⬅️", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = (self.index - 1) % len(self.embeds)
        await interaction.response.edit_message(embed=self.embeds[self.index], view=self)

    @discord.ui.button(custom_id="pagination_next", emoji="➡️", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = (self.index + 1) % len(self.embeds)
        await interaction.response.edit_message(embed=self.embeds[self.index], view=self)

# ————— Bot Definition —————
class RedHerringBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.pool: Optional[asyncpg.Pool] = None

    async def setup_hook(self):
        self.pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS contents (
                    id SERIAL PRIMARY KEY,
                    user_id TEXT,
                    title TEXT,
                    content_type TEXT,
                    status TEXT,
                    rating INTEGER,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
        # force sync guild or global
        if GUILD_ID:
            await self.tree.sync(guild=discord.Object(id=int(GUILD_ID)))
        else:
            await self.tree.sync()
        # register persistent view
        self.add_view(PaginationView([], timeout=None))
        # start healthcheck
        threading.Thread(target=run_web, daemon=True).start()

# instantiate bot and register view
bot = RedHerringBot()
# persistent view registration in case setup_hook isn't called
bot.add_view(PaginationView([], timeout=None))

# ————— Group /contenu —————
contenu = app_commands.Group(name="contenu", description="Gérer tes contenus")
bot.tree.add_command(contenu)

# ————— /contenu ajouter —————
@contenu.command(name="ajouter", description="Ajouter un contenu")
@app_commands.describe(titre="Titre", type="Type", statut="Statut")
@app_commands.choices(
    type=[app_commands.Choice(name=t, value=t) for t in COLOR_MAP],
    statut=[app_commands.Choice(name=s, value=s) for s in STATUS_EMOJIS]
)
async def cmd_ajouter(inter: discord.Interaction,
                      titre: str,
                      type: app_commands.Choice[str],
                      statut: app_commands.Choice[str]):
    t_norm = normalize_type(type.value)
    s_norm = normalize_status(statut.value)
    await bot.pool.execute(
        "INSERT INTO contents(user_id,title,content_type,status) VALUES($1,$2,$3,$4)",
        str(inter.user.id), titre, t_norm, s_norm
    )
    thumb = await fetch_thumbnail(titre, t_norm)
    emb = discord.Embed(
        title="Contenu ajouté ✅",
        description=f"**{titre}**",
        color=COLOR_MAP.get(t_norm, 0x95a5a6),
        timestamp=datetime.utcnow()
    )
    if thumb:
        emb.set_thumbnail(url=thumb)
    emb.add_field(name="Type", value=f"{t_norm} {TYPE_EMOJIS[t_norm]}", inline=True)
    emb.add_field(name="Statut", value=f"{s_norm} {STATUS_EMOJIS[s_norm]}", inline=True)
    await inter.response.send_message(embed=emb)

# … (autres commandes inchangées : ajoutermulti, liste, noter, modifier, supprimer)

# ————— Lancement —————
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)

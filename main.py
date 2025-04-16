import os
import threading
import io
import json
import csv
import discord
from discord import app_commands, File
from discord.ext import commands
import asyncpg
import aiohttp
from aiohttp import ClientTimeout
from flask import Flask
from typing import Optional, List, Dict
from urllib.parse import quote_plus
from datetime import datetime, timedelta

# Configuration
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")

# Mappages
COLOR_MAP: Dict[str, int] = {
    "Série": 0x1abc9c,
    "Animé": 0xe74c3c,
    "Webtoon": 0x9b59b6,
    "Manga": 0xf1c40f
}
TYPE_EMOJIS: Dict[str, str] = {
    "Série": "📺",
    "Animé": "🎥",
    "Webtoon": "📱",
    "Manga": "📚"
}
STATUS_EMOJIS: Dict[str, str] = {
    "En cours": "⏳",
    "À voir": "👀",
    "Terminé": "✅"
}

# Cache des miniatures
_thumbnail_cache: Dict[str, str] = {}

# Fonctions de normalisation

def normalize_type(value: str) -> str:
    m = {
        "série": "Série", "serie": "Série",
        "animé": "Animé", "anime": "Animé",
        "webtoon": "Webtoon", "manga": "Manga"
    }
    return m.get(value.lower().strip(), value.capitalize())


def normalize_status(value: str) -> str:
    m = {
        "en cours": "En cours", "à voir": "À voir", "a voir": "À voir",
        "terminé": "Terminé", "termine": "Terminé"
    }
    return m.get(value.lower().strip(), value.capitalize())

# Récupération de la vignette TMDB
async def fetch_thumbnail(title: str, content_type: str) -> Optional[str]:
    key = f"{title}|{content_type}"
    if key in _thumbnail_cache:
        return _thumbnail_cache[key]
    if not TMDB_API_KEY:
        return None
    kind = "tv" if content_type in ("Série", "Animé") else "movie"
    query = quote_plus(title)
    url = (f"https://api.themoviedb.org/3/search/{kind}?"
           f"api_key={TMDB_API_KEY}&query={query}")
    try:
        timeout = ClientTimeout(total=3)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                data = await resp.json()
    except Exception:
        return None
    results = data.get("results") or []
    if not results:
        return None
    path = results[0].get("poster_path")
    if not path:
        return None
    thumb = f"https://image.tmdb.org/t/p/w200{path}"
    _thumbnail_cache[key] = thumb
    return thumb

# Serveur Flask pour healthcheck
app = Flask(__name__)
@app.route("/")
def home():
    return "RedHerring Bot en ligne"

def run_web():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8000)))

# Classe du Bot
class RedHerringBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.pool: Optional[asyncpg.Pool] = None
        self._stats_cache: Dict[str, Dict] = {}

    async def setup_hook(self):
        # Création du pool asyncpg
        self.pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
        # Création de la table si nécessaire
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS contents (
                    id SERIAL PRIMARY KEY,
                    user_id TEXT,
                    title TEXT,
                    content_type TEXT,
                    status TEXT,
                    rating INTEGER
                )
                """
            )
        # Synchronisation des commandes slash
        await self.tree.sync()
        # Démarrage du healthcheck
        threading.Thread(target=run_web, daemon=True).start()

bot = RedHerringBot()

@bot.event
async def on_ready():
    print(f"{bot.user} connecté !")

# Vue de pagination
class PaginationView(discord.ui.View):
    def __init__(self, embeds: List[discord.Embed]):
        super().__init__(timeout=120)
        self.embeds = embeds
        self.index = 0

    @discord.ui.button(emoji="⬅️", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = (self.index - 1) % len(self.embeds)
        await interaction.response.edit_message(embed=self.embeds[self.index], view=self)

    @discord.ui.button(emoji="➡️", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = (self.index + 1) % len(self.embeds)
        await interaction.response.edit_message(embed=self.embeds[self.index], view=self)

# Autocomplétion
async def type_autocomplete(interaction: discord.Interaction, current: str):
    opts = [t for t in COLOR_MAP if current.lower() in t.lower()]
    return [app_commands.Choice(name=o, value=o) for o in opts[:5]]

async def status_autocomplete(interaction: discord.Interaction, current: str):
    opts = [s for s in STATUS_EMOJIS if current.lower() in s.lower()]
    return [app_commands.Choice(name=o, value=o) for o in opts[:5]]

# Commandes Slash: /ajouter
@bot.tree.command(name="ajouter", description="Ajouter un contenu")
@app_commands.describe(
    titre="Titre du contenu",
    type="Type (choix)",
    statut="Statut (choix)"
)
@app_commands.choices(
    type=[app_commands.Choice(name=t, value=t) for t in COLOR_MAP],
    statut=[app_commands.Choice(name=s, value=s) for s in STATUS_EMOJIS]
)
async def ajouter(interaction: discord.Interaction, titre: str, type: app_commands.Choice[str], statut: app_commands.Choice[str]):
    async with bot.pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO contents(user_id, title, content_type, status) VALUES($1,$2,$3,$4)",
            str(interaction.user.id), titre, type.value, statut.value
        )
    thumb = await fetch_thumbnail(titre, type.value)
    embed = discord.Embed(
        title="Contenu ajouté",
        description=f"**{titre}**",
        color=COLOR_MAP.get(type.value, 0x3498db),
        timestamp=datetime.utcnow()
    )
    if thumb:
        embed.set_thumbnail(url=thumb)
    embed.add_field(name="Type", value=f"{type.value} {TYPE_EMOJIS[type.value]}", inline=True)
    embed.add_field(name="Statut", value=f"{statut.value} {STATUS_EMOJIS[statut.value]}", inline=True)
    await interaction.response.send_message(embed=embed)

# Commandes Slash: /liste
@bot.tree.command(name="liste", description="Afficher ta liste de contenus")
@app_commands.describe(categorie="Filtrer par type", statut="Filtrer par statut")
@app_commands.autocomplete(categorie=type_autocomplete, statut=status_autocomplete)
async def liste(interaction: discord.Interaction, categorie: Optional[str] = None, statut: Optional[str] = None):
    args = [str(interaction.user.id)]
    q = "SELECT id, title, content_type, status, rating FROM contents WHERE user_id=$1"
    idx = 2
    if categorie:
        q += f" AND content_type=${idx}"; args.append(categorie); idx += 1
    if statut:
        q += f" AND status=${idx}"; args.append(statut); idx += 1
    q += " ORDER BY content_type, title"
    rows = await bot.pool.fetch(q, *args)
    if not rows:
        return await interaction.response.send_message("Aucun contenu.", ephemeral=True)
    embeds: List[discord.Embed] = []
    for i in range(0, len(rows), 8):
        emb = discord.Embed(title="Ta liste RedHerring", color=0x3498db, timestamp=datetime.utcnow())
        for rec in rows[i:i+8]:
            line = f"**{rec['title']}** {STATUS_EMOJIS[rec['status']]} (#{rec['id']})"
            if rec['rating'] is not None:
                line += f" | Note: {rec['rating']}/10"
            emb.add_field(
                name=f"{rec['content_type']} {TYPE_EMOJIS[rec['content_type']]}",
                value=line,
                inline=False
            )
        embeds.append(emb)
    view = PaginationView(embeds)
    await interaction.response.send_message(embed=embeds[0], view=view)

# Commandes restantes (/modifier, /modifiermulti, /noter, /supprimer, /recherche, /random, /stats, /export, /import, /help)
# ... implémentation identique en asyncpg avec vérification et embeds ...

if __name__ == '__main__':
    bot.run(DISCORD_TOKEN)
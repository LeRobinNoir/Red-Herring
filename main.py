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

# Emojis et couleurs
COLOR_MAP = {
    "Série": 0x1abc9c,
    "Animé": 0xe74c3c,
    "Webtoon": 0x9b59b6,
    "Manga": 0xf1c40f
}
TYPE_EMOJIS = {
    "Série": "📺",
    "Animé": "🎥",
    "Webtoon": "📱",
    "Manga": "📚"
}
STATUS_EMOJIS = {
    "À voir": "🔴",
    "En cours": "🟠",
    "Terminé": "🟢"
}

# Flask pour healthcheck
app = Flask(__name__)
@app.route("/")
def home():
    return "RedHerring est en ligne"

def run_web():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8000)))

# TMDB thumbnails
_thumbnail_cache = {}
async def fetch_thumbnail(title: str, content_type: str) -> Optional[str]:
    key = f"{title}|{content_type}"
    if key in _thumbnail_cache:
        return _thumbnail_cache[key]
    if not TMDB_API_KEY:
        return None
    kind = "tv" if content_type in ("Série", "Animé") else "movie"
    url = f"https://api.themoviedb.org/3/search/{kind}?api_key={TMDB_API_KEY}&query={quote_plus(title)}"
    try:
        timeout = ClientTimeout(total=3)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                data = await resp.json()
    except:
        return None
    results = data.get("results") or []
    if not results or not results[0].get("poster_path"):
        return None
    thumb = f"https://image.tmdb.org/t/p/w200{results[0]['poster_path']}"
    _thumbnail_cache[key] = thumb
    return thumb

# Normalisation

def normalize_type(value: str) -> str:
    m = {"série": "Série", "serie": "Série", "animé": "Animé", "anime": "Animé", "webtoon": "Webtoon", "manga": "Manga"}
    return m.get(value.lower().strip(), value.capitalize())

def normalize_status(value: str) -> str:
    m = {"à voir": "À voir", "a voir": "À voir", "en cours": "En cours", "terminé": "Terminé", "termine": "Terminé"}
    return m.get(value.lower().strip(), value.capitalize())

# Bot setup
class RedHerringBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.pool: Optional[asyncpg.Pool] = None
        self._stats_cache: Dict[str, Dict] = {}

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
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
        await self.tree.sync()

bot = RedHerringBot()

@bot.event
async def on_ready():
    print(f"✅ {bot.user} est connecté et prêt.")

# Regroupe toutes les commandes dans un groupe
contenu = app_commands.Group(name="contenu", description="Gérer tes contenus")
bot.tree.add_command(contenu)

# Les autres commandes (ajouter, modifier, liste, etc.) seront ensuite ajoutées dans ce groupe
# Ex : @contenu.command(name="ajouter") ... etc.

# Lancement
if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    bot.run(DISCORD_TOKEN)
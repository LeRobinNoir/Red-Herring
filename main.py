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
    "À voir": "👀",
    "En cours": "⏳",
    "Terminé": "✅"
}

# Cache des miniatures
_thumbnail_cache: Dict[str, str] = {}

# Helpers

def normalize_type(value: str) -> str:
    m = {"série":"Série","serie":"Série","animé":"Animé","anime":"Animé","webtoon":"Webtoon","manga":"Manga"}
    return m.get(value.lower().strip(), value.capitalize())

def normalize_status(value: str) -> str:
    m = {"à voir":"À voir","a voir":"À voir","en cours":"En cours","terminé":"Terminé","termine":"Terminé"}
    return m.get(value.lower().strip(), value.capitalize())

async def fetch_thumbnail(title: str, content_type: str) -> Optional[str]:
    key = f"{title}|{content_type}"
    if key in _thumbnail_cache:
        return _thumbnail_cache[key]
    if not TMDB_API_KEY:
        return None
    kind = "tv" if content_type in ("Série","Animé") else "movie"
    query = quote_plus(title)
    url = f"https://api.themoviedb.org/3/search/{kind}?api_key={TMDB_API_KEY}&query={query}"
    try:
        timeout = ClientTimeout(total=3)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                data = await resp.json()
    except Exception:
        return None
    results = data.get("results") or []
    if not results or not results[0].get("poster_path"):
        return None
    thumb = f"https://image.tmdb.org/t/p/w200{results[0]['poster_path']}"
    _thumbnail_cache[key] = thumb
    return thumb

# Healthcheck
app = Flask(__name__)
@app.route("/")
def home():
    return "RedHerring Bot en ligne"

def run_web():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT",8000)))

# Bot class
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
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS contents (
                    id SERIAL PRIMARY KEY,
                    user_id TEXT,
                    title TEXT,
                    content_type TEXT,
                    status TEXT,
                    rating INTEGER
                )"""
            )
        await self.tree.sync()
        threading.Thread(target=run_web, daemon=True).start()

bot = RedHerringBot()

@bot.event
async def on_ready():
    print(f"{bot.user} connecté !")

# Pagination view
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

# Autocomplete
async def type_autocomplete(interaction: discord.Interaction, current: str):
    return [app_commands.Choice(name=t, value=t) for t in COLOR_MAP if current.lower() in t.lower()][:5]
async def status_autocomplete(interaction: discord.Interaction, current: str):
    return [app_commands.Choice(name=s, value=s) for s in STATUS_EMOJIS if current.lower() in s.lower()][:5]

# /ajouter
@bot.tree.command(name="ajouter", description="Ajouter un contenu")
@app_commands.describe(titre="Titre", type="Type", statut="Statut")
@app_commands.choices(type=[app_commands.Choice(name=t,value=t) for t in COLOR_MAP],statut=[app_commands.Choice(name=s,value=s) for s in STATUS_EMOJIS])
async def ajouter(interaction: discord.Interaction, titre: str, type: app_commands.Choice[str], statut: app_commands.Choice[str]):
    async with bot.pool.acquire() as conn:
        await conn.execute("INSERT INTO contents(user_id,title,content_type,status) VALUES($1,$2,$3,$4)",str(interaction.user.id),titre,type.value,statut.value)
    thumb = await fetch_thumbnail(titre,type.value)
    embed = discord.Embed(title="Contenu ajouté",description=f"**{titre}**",color=COLOR_MAP.get(type.value),timestamp=datetime.utcnow())
    if thumb:embed.set_thumbnail(url=thumb)
    embed.add_field(name="Type",value=f"{type.value} {TYPE_EMOJIS[type.value]}",inline=True)
    embed.add_field(name="Statut",value=f"{statut.value} {STATUS_EMOJIS[statut.value]}",inline=True)
    await interaction.response.send_message(embed=embed)

# /liste
@bot.tree.command(name="liste", description="Afficher ta liste")
@app_commands.describe(categorie="Type", statut="Statut")
@app_commands.autocomplete(categorie=type_autocomplete,statut=status_autocomplete)
async def liste(interaction: discord.Interaction, categorie: Optional[str]=None, statut: Optional[str]=None):
    args=[str(interaction.user.id)];q="SELECT id,title,content_type,status,rating FROM contents WHERE user_id=$1"
    idx=2
    if categorie: q+=f" AND content_type=${idx}";args.append(categorie);idx+=1
    if statut: q+=f" AND status=${idx}";args.append(statut);idx+=1
    q+=" ORDER BY content_type,title"
    rows=await bot.pool.fetch(q,*args)
    if not rows:return await interaction.response.send_message("Aucun contenu.",ephemeral=True)
    embeds=[]
    for i in range(0,len(rows),8):
        emb=discord.Embed(title="Ta liste RedHerring",color=0x3498db,timestamp=datetime.utcnow())
        for r in rows[i:i+8]:
            line=f"**{r['title']}** {STATUS_EMOJIS[r['status']]} (#{r['id']})"
            if r['rating']!=None:line+=f" | Note: {r['rating']}/10"
            emb.add_field(name=f"{r['content_type']} {TYPE_EMOJIS[r['content_type']]}",value=line,inline=False)
        embeds.append(emb)
    await interaction.response.send_message(embed=embeds[0],view=PaginationView(embeds))

# /modifier
@bot.tree.command(name="modifier",description="Modifier statut")
@app_commands.describe(id="ID",statut="Statut")
@app_commands.choices(statut=[app_commands.Choice(name=s,value=s) for s in STATUS_EMOJIS])
async def modifier(interaction: discord.Interaction,id:int,statut:app_commands.Choice[str]):
    res=await bot.pool.execute("UPDATE contents SET status=$1 WHERE id=$2 AND user_id=$3",statut.value,id,str(interaction.user.id))
    if not res.endswith("UPDATE 1"):return await interaction.response.send_message("Échec.",ephemeral=True)
    embed=discord.Embed(title="Statut modifié",description=f"#{id} -> {statut.value}",color=0x2ecc71,timestamp=datetime.utcnow())
    await interaction.response.send_message(embed=embed)

# /modifiermulti
class MultiModifyView(discord.ui.View):
    def __init__(self, entries: List[Dict], user_id: str):
        super().__init__(timeout=120)
        self.entries = entries
        self.user_id = user_id
        self.selected: List[int] = []
        self.new_status: Optional[str] = None

        # Select for entries
        entry_options = [discord.SelectOption(label=f"{e['title']} (#{e['id']})", value=str(e['id'])) for e in entries]
        select_entries = discord.ui.Select(
            placeholder="Sélectionne contenus", min_values=1, max_values=len(entry_options), options=entry_options
        )
        select_entries.callback = self.select_items
        self.add_item(select_entries)

        # Select for status
        status_options = [discord.SelectOption(label=s, value=s) for s in STATUS_EMOJIS]
        select_status = discord.ui.Select(
            placeholder="Nouveau statut", min_values=1, max_values=1, options=status_options
        )
        select_status.callback = self.select_status
        self.add_item(select_status)

        # Confirm button
        confirm_button = discord.ui.Button(label="Confirmer", style=discord.ButtonStyle.green)
        confirm_button.callback = self.confirm
        self.add_item(confirm_button)

    async def select_items(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.selected = [int(v) for v in select.values]
        await interaction.response.send_message(f"{len(self.selected)} sélectionnés", ephemeral=True)

    async def select_status(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.new_status = select.values[0]
        await interaction.response.send_message(f"Statut = {self.new_status}", ephemeral=True)

    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.selected or not self.new_status:
            return await interaction.response.send_message("Requis.", ephemeral=True)
        for cid in self.selected:
            await bot.pool.execute(
                "UPDATE contents SET status=$1 WHERE id=$2 AND user_id=$3",
                self.new_status, cid, self.user_id
            )
        await interaction.response.send_message(
            f"{len(self.selected)} mis à jour en {self.new_status}",
            ephemeral=True
        )
        self.stop()

@bot.tree.command(name="modifiermulti", description="Modifier plusieurs statuts")
async def modifiermulti(interaction: discord.Interaction):
    # Fetch user entries
    rows = await bot.pool.fetch(
        "SELECT id, title FROM contents WHERE user_id=$1 ORDER BY id",
        str(interaction.user.id)
    )
    if not rows:
        return await interaction.response.send_message("Rien à modifier.", ephemeral=True)
    view = MultiModifyView(entries=[dict(r) for r in rows], user_id=str(interaction.user.id))
    await interaction.response.send_message("Choisis et confirme :", view=view)

# /noter
@bot.tree.command(name="noter",description="Noter 0-10")
@app_commands.describe(id="ID",note="Note")
async def noter(i,id:int,note:int):
    if not 0<=note<=10:return await i.response.send_message("0-10.",ephemeral=True)
    res=await bot.pool.execute("UPDATE contents SET rating=$1 WHERE id=$2 AND user_id=$3",note,id,str(i.user.id))
    if not res.endswith("UPDATE 1"):return await i.response

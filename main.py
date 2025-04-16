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
    url = f"https://api.themoviedb.org/3/search/{kind}?api_key={TMDB_API_KEY}&query={quote_plus(title)}"
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
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8000)))

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
                )
                """
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

# Commands: ajouter
@bot.tree.command(name="ajouter", description="Ajouter un contenu")
@app_commands.describe(titre="Titre", type="Type", statut="Statut")
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

# Commands: liste
@bot.tree.command(name="liste", description="Afficher la liste de contenus")
@app_commands.describe(categorie="Type", statut="Statut")
@app_commands.autocomplete(categorie=type_autocomplete, statut=status_autocomplete)
async def liste(interaction: discord.Interaction, categorie: Optional[str] = None, statut: Optional[str] = None):
    args = [str(interaction.user.id)]
    q = "SELECT id,title,content_type,status,rating FROM contents WHERE user_id=$1"
    idx = 2
    if categorie:
        q += f" AND content_type=${idx}"; args.append(categorie); idx += 1
    if statut:
        q += f" AND status=${idx}"; args.append(statut); idx += 1
    q += " ORDER BY content_type,title"
    rows = await bot.pool.fetch(q, *args)
    if not rows:
        return await interaction.response.send_message("Aucun contenu.", ephemeral=True)
    embeds: List[discord.Embed] = []
    for i in range(0, len(rows), 8):
        emb = discord.Embed(
            title="Ta liste RedHerring",
            color=0x3498db,
            timestamp=datetime.utcnow()
        )
        for r in rows[i:i+8]:
            line = f"**{r['title']}** {STATUS_EMOJIS[r['status']]} (#{r['id']})"
            if r['rating'] is not None:
                line += f" | Note: {r['rating']}/10"
            emb.add_field(name=f"{r['content_type']} {TYPE_EMOJIS[r['content_type']]}", value=line, inline=False)
        embeds.append(emb)
    await interaction.response.send_message(embed=embeds[0], view=PaginationView(embeds))

# Commands: modifier
@bot.tree.command(name="modifier", description="Modifier le statut")
@app_commands.describe(id="ID du contenu", statut="Nouveau statut")
@app_commands.choices(statut=[app_commands.Choice(name=s, value=s) for s in STATUS_EMOJIS])
async def modifier(interaction: discord.Interaction, id: int, statut: app_commands.Choice[str]):
    res = await bot.pool.execute("UPDATE contents SET status=$1 WHERE id=$2 AND user_id=$3", statut.value, id, str(interaction.user.id))
    if not res.endswith("UPDATE 1"):
        return await interaction.response.send_message("Échec.", ephemeral=True)
    embed = discord.Embed(
        title="Statut modifié",
        description=f"Contenu #{id} -> {statut.value}",
        color=0x2ecc71,
        timestamp=datetime.utcnow()
    )
    await interaction.response.send_message(embed=embed)

# Commands: modifiermulti
class MultiModifyView(discord.ui.View):
    def __init__(self, entries: List[Dict], user_id: str):
        super().__init__(timeout=120)
        self.entries = entries
        self.user_id = user_id
        self.selected: List[int] = []
        self.new_status: Optional[str] = None

        entry_opts = [discord.SelectOption(label=f"{e['title']} (#{e['id']})", value=str(e['id'])) for e in entries]
        sel_entries = discord.ui.Select(placeholder="Sélectionne contenus", min_values=1, max_values=len(entry_opts), options=entry_opts)
        sel_entries.callback = self._select_items
        self.add_item(sel_entries)

        status_opts = [discord.SelectOption(label=s, value=s) for s in STATUS_EMOJIS]
        sel_status = discord.ui.Select(placeholder="Nouveau statut", min_values=1, max_values=1, options=status_opts)
        sel_status.callback = self._select_status
        self.add_item(sel_status)

        btn = discord.ui.Button(label="Confirmer", style=discord.ButtonStyle.green)
        btn.callback = self._confirm
        self.add_item(btn)

    async def _select_items(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.selected = [int(v) for v in select.values]
        await interaction.response.send_message(f"{len(self.selected)} sélectionnés", ephemeral=True)

    async def _select_status(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.new_status = select.values[0]
        await interaction.response.send_message(f"Statut = {self.new_status}", ephemeral=True)

    async def _confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.selected or not self.new_status:
            return await interaction.response.send_message("Requis.", ephemeral=True)
        for cid in self.selected:
            await bot.pool.execute("UPDATE contents SET status=$1 WHERE id=$2 AND user_id=$3", self.new_status, cid, self.user_id)
        await interaction.response.send_message(f"{len(self.selected)} mis à jour en {self.new_status}", ephemeral=True)
        self.stop()

@bot.tree.command(name="modifiermulti", description="Modifier plusieurs statuts")
async def modifiermulti(interaction: discord.Interaction):
    rows = await bot.pool.fetch("SELECT id,title FROM contents WHERE user_id=$1 ORDER BY id", str(interaction.user.id))
    if not rows:
        return await interaction.response.send_message("Rien à modifier.", ephemeral=True)
    view = MultiModifyView(entries=[dict(r) for r in rows], user_id=str(interaction.user.id))
    await interaction.response.send_message("Choisis et confirme :", view=view)

# Commands: noter
@bot.tree.command(name="noter", description="Noter un contenu (0-10)")
@app_commands.describe(id="ID du contenu", note="Note (0-10)")
async def noter(interaction: discord.Interaction, id: int, note: int):
    if not 0 <= note <= 10:
        return await interaction.response.send_message("Note entre 0 et 10.", ephemeral=True)
    res = await bot.pool.execute("UPDATE contents SET rating=$1 WHERE id=$2 AND user_id=$3", note, id, str(interaction.user.id))
    if not res.endswith("UPDATE 1"):
        return await interaction.response.send_message("Échec.", ephemeral=True)
    await interaction.response.send_message(f"Contenu #{id} noté {note}/10")

# Commands: supprimer
@bot.tree.command(name="supprimer", description="Supprimer contenus par IDs")
@app_commands.describe(ids="IDs séparés par virgules")
async def supprimer(interaction: discord.Interaction, ids: str):
    valid = [int(p) for p in ids.split(',') if p.strip().isdigit()]
    if not valid:
        return await interaction.response.send_message("IDs invalides.", ephemeral=True)
    deleted: List[str] = []
    for cid in valid:
        row = await bot.pool.fetchrow("DELETE FROM contents WHERE id=$1 AND user_id=$2 RETURNING title", cid, str(interaction.user.id))
        if row:
            deleted.append(row['title'])
    if not deleted:
        return await interaction.response.send_message("Rien supprimé.", ephemeral=True)
    embed = discord.Embed(title="Supprimé", description=", ".join(deleted), color=0xe74c3c)
    await interaction.response.send_message(embed=embed)

# Commands: recherche
@bot.tree.command(name="recherche", description="Chercher par mot-clé")
@app_commands.describe(query="Mot-clé", categorie="Type", statut="Statut")
@app_commands.autocomplete(categorie=type_autocomplete, statut=status_autocomplete)
async def recherche(interaction: discord.Interaction, query: str, categorie: Optional[str] = None, statut: Optional[str] = None):
    args = [str(interaction.user.id), f"%{query}%"]
    q = "SELECT id,title,content_type,status,rating FROM contents WHERE user_id=$1 AND title ILIKE $2"
    idx = 3
    if categorie:
        q += f" AND content_type=${idx}"; args.append(categorie); idx += 1
    if statut:
        q += f" AND status=${idx}"; args.append(statut); idx += 1
    q += " ORDER BY content_type,title"
    rows = await bot.pool.fetch(q, *args)
    if not rows:
        return await interaction.response.send_message("Aucun résultat.", ephemeral=True)
    embed = discord.Embed(title=f"Résultats pour '{query}'", color=0x3498db)
    for r in rows:
        embed.add_field(name=f"#{r['id']} - {r['title']}", value=f"{r['content_type']} {TYPE_EMOJIS[r['content_type']]} | {r['status']} {STATUS_EMOJIS[r['status']]}")
    await interaction.response.send_message(embed=embed)

# Commands: random
@bot.tree.command(name="random", description="Suggestion aléatoire")
@app_commands.describe(categorie="Type", statut="Statut")
@app_commands.autocomplete(categorie=type_autocomplete, statut=status_autocomplete)
async def random_suggestion(interaction: discord.Interaction, categorie: Optional[str] = None, statut: Optional[str] = None):
    args = [str(interaction.user.id)]
    q = "SELECT id,title,content_type,status FROM contents WHERE user_id=$1"
    idx = 2
    if categorie:
        q += f" AND content_type=${idx}"; args.append(categorie); idx += 1
    if statut:
        q += f" AND status=${idx}"; args.append(statut); idx += 1
    q += " ORDER BY RANDOM() LIMIT 1"
    row = await bot.pool.fetchrow(q, *args)
    if not row:
        return await interaction.response.send_message("Rien trouvé.", ephemeral=True)
    embed = discord.Embed(title="Suggestion aléatoire", color=0x9b59b6)
    embed.add_field(name=row['title'], value=f"{row['content_type']} {TYPE_EMOJIS[row['content_type']]} | {row['status']} (#"+str(row['id'])+")")
    await interaction.response.send_message(embed=embed)

# Commands: stats
@bot.tree.command(name="stats", description="Afficher stats (cache 60s)")
async def stats(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    now = datetime.utcnow()
    cache = bot._stats_cache.get(uid)
    if cache and now - cache['time'] < timedelta(seconds=60):
        return await interaction.response.send_message(embed=cache['embed'])
    async with bot.pool.acquire() as conn:
        total = await conn.fetchval("SELECT COUNT(*) FROM contents WHERE user_id=$1", uid)
        by_type = await conn.fetch("SELECT content_type, COUNT(*) AS cnt FROM contents WHERE user_id=$1 GROUP BY content_type", uid)
        by_status = await conn.fetch("SELECT status, COUNT(*) AS cnt FROM contents WHERE user_id=$1 GROUP BY status", uid)
        avg_rating = await conn.fetchval("SELECT AVG(rating) FROM contents WHERE user_id=$1 AND rating IS NOT NULL", uid)
    emb = discord.Embed(title="Stats RedHerring", color=0x3498db, timestamp=now)
    emb.add_field(name="Total contenus", value=str(total), inline=False)
    emb.add_field(name="Par type", value="\n".join(f"{r['content_type']}: {r['cnt']}" for r in by_type) or "—", inline=False)
    emb.add_field(name="Par statut", value="\n".join(f"{r['status']}: {r['cnt']}" for r in by_status) or "—", inline=False)
    if avg_rating is not None:
        emb.add_field(name="Note moyenne", value=f"{avg_rating:.2f}/10", inline=False)
    bot._stats_cache[uid] = {'time': now, 'embed': emb}
    await interaction.response.send_message(embed=emb)

# Commands: export
@bot.tree.command(name="export", description="Exporter JSON/CSV")
@app_commands.describe(format="json ou csv")
async def export_cmd(interaction: discord.Interaction, format: str):
    uid = str(interaction.user.id)
    rows = await bot.pool.fetch("SELECT * FROM contents WHERE user_id=$1", uid)
    if not rows:
        return await interaction.response.send_message("Aucun contenu à exporter.", ephemeral=True)
    if format.lower() == 'json':
        buf = io.BytesIO(json.dumps([dict(r) for r in rows], ensure_ascii=False).encode())
        await interaction.response.send_message(file=File(buf, 'export.json'))
    elif format.lower() == 'csv':
        txt = io.StringIO()
        writer = csv.writer(txt)
        writer.writerow(['id','user_id','title','content_type','status','rating'])
        for r in rows:
            writer.writerow([r['id'], r['user_id'], r['title'], r['content_type'], r['status'], r['rating']])
        await interaction.response.send_message(file=File(io.BytesIO(txt.getvalue().encode()), 'export.csv'))
    else:
        await interaction.response.send_message("Format 'json' ou 'csv' uniquement.", ephemeral=True)

# Commands: import
@bot.tree.command(name="import", description="Importer JSON/CSV")
@app_commands.describe(fichier="Fichier à importer")
async def import_cmd(interaction: discord.Interaction, fichier: discord.Attachment):
    data = await fichier.read()
    uid = str(interaction.user.id)
    count = 0
    if fichier.filename.lower().endswith('.json'):
        try:
            arr = json.loads(data)
        except:
            return await interaction.response.send_message("JSON invalide.", ephemeral=True)
        async with bot.pool.acquire() as conn:
            for e in arr:
                await conn.execute(
                    "INSERT INTO contents(user_id,title,content_type,status,rating) VALUES($1,$2,$3,$4,$5)",
                    uid, e.get('title'), normalize_type(e.get('content_type','')), normalize_status(e.get('status','')), e.get('rating')
                )
                count += 1
    elif fichier.filename.lower().endswith('.csv'):
        lines = data.decode().splitlines()
        reader = csv.DictReader(lines)
        async with bot.pool.acquire() as conn:
            for row in reader:
                await conn.execute(
                    "INSERT INTO contents(user_id,title,content_type,status,rating) VALUES($1,$2,$3,$4,$5)",
                    uid, row.get('title'), normalize_type(row.get('content_type','')), normalize_status(row.get('status','')), row.get('rating') or None
                )
                count += 1
    else:
        return await interaction.response.send_message("Fichier .json ou .csv requis.", ephemeral=True)
    await interaction.response.send_message(f"Importé {count} éléments.")

# Commands: help
@bot.tree.command(name="help", description="Afficher l'aide de RedHerring")
async def help_cmd(interaction: discord.Interaction):
    cmds = [
        ("/ajouter", "Ajouter un contenu"),
        ("/liste", "Afficher ta liste"),
        ("/modifier", "Modifier un statut"),
        ("/modifiermulti", "Modifier plusieurs statuts"),
        ("/noter", "Noter un contenu"),
        ("/supprimer", "Supprimer des contenus"),
        ("/recherche", "Chercher par mot-clé"),
        ("/random", "Suggestion aléatoire"),
        ("/stats", "Afficher tes stats"),
        ("/export", "Exporter en JSON/CSV"),
        ("/import", "Importer depuis JSON/CSV")
    ]
    embed = discord.Embed(title="Aide RedHerring", color=0x95a5a6)
    for name, desc in cmds:
        embed.add_field(name=name, value=desc, inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# Entrypoint
if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    bot.run(DISCORD_TOKEN)

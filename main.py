# main.py – Red Herring bot complet et fonctionnel (pagination persistante corrigée)

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
from datetime import datetime

# ————— Configuration —————
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL  = os.getenv("DATABASE_URL")
TMDB_API_KEY  = os.getenv("TMDB_API_KEY")
GUILD_ID      = os.getenv("GUILD_ID")  # facultatif pour dev

# ————— Visuels —————
COLOR_MAP = {"Série":0x1abc9c, "Animé":0xe74c3c, "Webtoon":0x9b59b6, "Manga":0xf1c40f}
TYPE_EMOJIS = {"Série":"📺","Animé":"🎥","Webtoon":"📱","Manga":"📚"}
STATUS_EMOJIS = {"À voir":"🔴","En cours":"🟠","Terminé":"🟢","Abandonné":"⚫"}

# ————— Flask healthcheck —————
app = Flask(__name__)
@app.route("/")
def home():
    return "Red Herring Bot en ligne"

def run_web():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT",8000)))

# ————— Helpers TMDB —————
_thumbnail_cache: Dict[str, str] = {}
async def fetch_thumbnail(title: str, content_type: str) -> Optional[str]:
    key = f"{title}|{content_type}"
    if key in _thumbnail_cache:
        return _thumbnail_cache[key]
    if not TMDB_API_KEY:
        return None
    kind = "tv" if content_type in ("Série","Animé") else "movie"
    url = (
        f"https://api.themoviedb.org/3/search/{kind}?api_key={TMDB_API_KEY}&query="
        f"{quote_plus(title)}"
    )
    try:
        timeout = ClientTimeout(total=3)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(url) as resp:
                data = await resp.json()
    except:
        return None
    for res in data.get("results", []):
        poster = res.get("poster_path")
        if poster:
            thumb = f"https://image.tmdb.org/t/p/w300{poster}"
            _thumbnail_cache[key] = thumb
            return thumb
    return None

# ————— Normalisation —————
def normalize_type(v: str) -> str:
    m = {
        "série":"Série", "serie":"Série",
        "animé":"Animé", "anime":"Animé",
        "webtoon":"Webtoon", "manga":"Manga"
    }
    return m.get(v.lower().strip(), v.capitalize())

def normalize_status(v: str) -> str:
    m = {
        "à voir":"À voir", "a voir":"À voir",
        "en cours":"En cours",
        "terminé":"Terminé", "termine":"Terminé"
    }
    # inclut Abandonné
    if v.lower().strip() in ("abandonné", "abandonne", "abandonner"):
        return "Abandonné"
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
        # Création du pool et de la table
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
                    rating INTEGER,
                    created_at TIMESTAMP DEFAULT NOW()
                );
                """
            )
        # Synchronisation des slash commands
        if GUILD_ID:
            await self.tree.sync(guild=discord.Object(id=int(GUILD_ID)))
        else:
            await self.tree.sync()
        # Enregistrement de la vue persistante DANS la loop
        self.add_view(PaginationView([], timeout=None))
        # Lancement du serveur Flask en thread
        threading.Thread(target=run_web, daemon=True).start()

# Instantiate bot
bot = RedHerringBot()

# ————— Groupe /contenu —————
contenu = app_commands.Group(name="contenu", description="Gérer tes contenus")
bot.tree.add_command(contenu)

# ————— /contenu ajouter —————
@contenu.command(name="ajouter", description="Ajouter un contenu")
@app_commands.describe(
    titre="Titre du contenu",
    type="Type (Manga, Animé, Webtoon, Série)",
    statut="Statut (À voir, En cours, Terminé, Abandonné)"
)
@app_commands.choices(
    type=[app_commands.Choice(name=t, value=t) for t in COLOR_MAP],
    statut=[app_commands.Choice(name=s, value=s) for s in STATUS_EMOJIS]
)
async def cmd_ajouter(
    interaction: discord.Interaction,
    titre: str,
    type: app_commands.Choice[str],
    statut: app_commands.Choice[str]
):
    t_norm = normalize_type(type.value)
    s_norm = normalize_status(statut.value)
    await bot.pool.execute(
        "INSERT INTO contents(user_id,title,content_type,status) VALUES($1,$2,$3,$4)",
        str(interaction.user.id), titre, t_norm, s_norm
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
    await interaction.response.send_message(embed=emb)

# ————— /contenu ajoutermulti —————
class ContentModal(discord.ui.Modal, title="Ajouter un contenu"):
    titre = discord.ui.TextInput(label="Titre", placeholder="Ex: One Piece", max_length=100)
    type_ = discord.ui.TextInput(label="Type", placeholder="Manga, Animé, etc.", max_length=50)
    statut = discord.ui.TextInput(label="Statut", placeholder="À voir, En cours, Terminé, Abandonné", max_length=50)

    async def on_submit(self, interaction: discord.Interaction):
        entry = {"titre": self.titre.value, "type": normalize_type(self.type_.value), "statut": normalize_status(self.statut.value)}
        self.view.entries.append(entry)
        await interaction.response.send_message(f"Ajouté **{entry['titre']}**.")

class AjouterMultiView(discord.ui.View):
    def __init__(self, user_id: str):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.entries: List[Dict] = []

    @discord.ui.button(label="➕ Ajouter un contenu", style=discord.ButtonStyle.primary)
    async def add_fn(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = ContentModal()
        modal.view = self
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="✅ Confirmer tout", style=discord.ButtonStyle.success)
    async def confirm_fn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.entries:
            return await interaction.response.send_message("Aucun contenu à ajouter.")
        lines = []
        async with bot.pool.acquire() as conn:
            for e in self.entries:
                r = await conn.fetchrow("INSERT INTO contents(user_id,title,content_type,status) VALUES($1,$2,$3,$4) RETURNING id", self.user_id, e['titre'], e['type'], e['statut'])
                lines.append(f"{e['titre']} (ID: {r['id']})")
        emb = discord.Embed(title="Ajouts multiples ✅", description="\n".join(lines), color=0x2ecc71)
        await interaction.response.send_message(embed=emb)
        self.stop()

@contenu.command(name="ajoutermulti", description="Ajouter plusieurs contenus")
async def cmd_ajoutermulti(interaction: discord.Interaction):
    view = AjouterMultiView(user_id=str(interaction.user.id))
    await interaction.response.send_message("Clique sur ➕ pour ajouter, puis ✅ pour confirmer.", view=view)

# ————— /contenu liste —————
@contenu.command(name="liste", description="Afficher liste paginée par statut")
@app_commands.describe(member="Afficher la liste d'un autre utilisateur (optionnel)")
async def cmd_liste(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    target = member or interaction.user
    uid = str(target.id)
    rows = await bot.pool.fetch("SELECT id,title,content_type,status,rating,created_at FROM contents WHERE user_id=$1 ORDER BY content_type,title", uid)
    if not rows:
        return await interaction.response.send_message("❌ Aucun contenu.")
    statut_order = ["À voir", "En cours", "Terminé", "Abandonné"]
    colors = {"À voir":0xe74c3c, "En cours":0xf1c40f, "Terminé":0x2ecc71, "Abandonné":0x555555}
    embeds = []
    for st in statut_order:
        grp = [r for r in rows if r['status']==st]
        if not grp:
            continue
        emb = discord.Embed(title=f"{st} {STATUS_EMOJIS[st]}", color=colors[st], timestamp=datetime.utcnow())
        last = max(grp, key=lambda r: r['created_at'])
        thumb = await fetch_thumbnail(last['title'], last['content_type'])
        if thumb:
            emb.set_thumbnail(url=thumb)
        lines = []
        for r in grp:
            id_ms = f"`#{r['id']}`"
            note_ms = f" | `⭐{r['rating']}/10`" if r['rating'] is not None else ""
            lines.append(f"{TYPE_EMOJIS.get(r['content_type'],'')} **{r['title']}** {id_ms}{note_ms}")
        emb.add_field(name="​", value="\n".join(lines), inline=False)
        embeds.append(emb)
    view = PaginationView(embeds, timeout=None)
    await interaction.response.send_message(embed=embeds[0], view=view)

# ————— /contenu noter —————
@contenu.command(name="noter", description="Noter un contenu (0–10)")
@app_commands.describe(id="ID du contenu", note="Note 0–10")
async def cmd_noter(interaction: discord.Interaction, id: int, note: int):
    if note<0 or note>10:
        return await interaction.response.send_message("⚠️ Note 0-10.")
    res = await bot.pool.execute("UPDATE contents SET rating=$1 WHERE id=$2 AND user_id=$3", note, id, str(interaction.user.id))
    if res.endswith("UPDATE 1"):
        return await interaction.response.send_message(f"✅ Contenu #{id} noté {note}/10.")
    await interaction.response.send_message("❌ Non autorisé.")

# ————— /contenu modifier —————
@contenu.command(name="modifier", description="Modifier le statut d'un contenu")
@app_commands.describe(id="ID du contenu", statut="Nouveau statut")
@app_commands.choices(statut=[app_commands.Choice(name=s, value=s) for s in STATUS_EMOJIS])
async def cmd_modifier(interaction: discord.Interaction, id: int, statut: app_commands.Choice[str]):
    s_norm = normalize_status(statut.value)
    res = await bot.pool.execute("UPDATE contents SET status=$1 WHERE id=$2 AND user_id=$3", s_norm, id, str(interaction.user.id))
    if res.endswith("UPDATE 1"):
        return await interaction.response.send_message(f"✅ Contenu #{id} → {s_norm}.")
    await interaction.response.send_message("❌ Non autorisé.")

# ————— /contenu supprimer —————
@contenu.command(name="supprimer", description="Supprimer un contenu par ID")
@app_commands.describe(id="ID du contenu")
async def cmd_supprimer(interaction: discord.Interaction, id: int):
    row = await bot.pool.fetchrow("DELETE FROM contents WHERE id=$1 AND user_id=$2 RETURNING title", id, str(interaction.user.id))
    if row:
        return await interaction.response.send_message(f"✅ {row['title']} supprimé.")
    await interaction.response.send_message("❌ Non autorisé.")

# ————— Lancement —————
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)

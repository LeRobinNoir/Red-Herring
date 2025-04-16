# main.py – Red Herring bot complet et fonctionnel

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

# ————— Configuration —————
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL  = os.getenv("DATABASE_URL")
TMDB_API_KEY  = os.getenv("TMDB_API_KEY")
# (optionnel) GUILD_ID pour synchro rapide en dev
GUILD_ID = os.getenv("GUILD_ID")

# ————— Visuels —————
COLOR_MAP = {
    "Série":   0x1abc9c,
    "Animé":   0xe74c3c,
    "Webtoon": 0x9b59b6,
    "Manga":   0xf1c40f
}
TYPE_EMOJIS = {
    "Série": "📺", "Animé": "🎥", "Webtoon": "📱", "Manga": "📚"
}
STATUS_EMOJIS = {
    "À voir": "🔴", "En cours": "🟠", "Terminé": "🟢"
}

# ————— Flask healthcheck —————
app = Flask(__name__)
@app.route("/")
def home():
    return "Red Herring Bot en ligne"

def run_web():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8000)))

# ————— Helpers TMDB —————
_thumbnail_cache: Dict[str, str] = {}
async def fetch_thumbnail(title: str, content_type: str) -> Optional[str]:
    key = f"{title}|{content_type}"
    if key in _thumbnail_cache:
        return _thumbnail_cache[key]
    if not TMDB_API_KEY:
        return None
    kind = "tv" if content_type in ("Série", "Animé") else "movie"
    url = (
        f"https://api.themoviedb.org/3/search/{kind}"
        f"?api_key={TMDB_API_KEY}&query={quote_plus(title)}"
    )
    try:
        timeout = ClientTimeout(total=3)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(url) as resp:
                data = await resp.json()
    except:
        return None
    for res in data.get("results", []):
        if res.get("poster_path"):
            thumb = f"https://image.tmdb.org/t/p/w200{res['poster_path']}"
            _thumbnail_cache[key] = thumb
            return thumb
    return None

# ————— Normalisation —————
def normalize_type(v: str) -> str:
    m = {
        "série": "Série", "serie": "Série",
        "animé": "Animé", "anime": "Animé",
        "webtoon": "Webtoon", "manga": "Manga"
    }
    return m.get(v.lower().strip(), v.capitalize())

def normalize_status(v: str) -> str:
    m = {
        "à voir": "À voir", "a voir": "À voir",
        "en cours": "En cours",
        "terminé": "Terminé", "termine": "Terminé"
    }
    return m.get(v.lower().strip(), v.capitalize())

# ————— Bot Definition —————
class RedHerringBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.pool: Optional[asyncpg.Pool] = None
        self._stats_cache: Dict[str, dict] = {}

    async def setup_hook(self):
        # Création du pool et de la table
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
        # Synchronisation des slash commands
        if GUILD_ID:
            # Dev rapide sur un seul serveur
            await self.tree.sync(guild=discord.Object(id=int(GUILD_ID)))
        else:
            # Global (peut prendre du temps)
            await self.tree.sync()
        # Lancement du healthcheck
        threading.Thread(target=run_web, daemon=True).start()

bot = RedHerringBot()

# ————— Groupe principal /contenu —————
contenu = app_commands.Group(name="contenu", description="Gérer tes contenus")
bot.tree.add_command(contenu)

# ————— Autocomplete helpers —————
async def type_autocomplete(inter, cur: str):
    return [app_commands.Choice(name=t, value=t) for t in COLOR_MAP if cur.lower() in t.lower()][:5]

async def status_autocomplete(inter, cur: str):
    return [app_commands.Choice(name=s, value=s) for s in STATUS_EMOJIS if cur.lower() in s.lower()][:5]

# ————— /contenu ajouter —————
@contenu.command(name="ajouter", description="Ajouter un contenu")
@app_commands.describe(
    titre="Titre du contenu",
    type="Type (Manga, Animé, Webtoon, Série)",
    statut="Statut (À voir, En cours, Terminé)"
)
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
    emb.add_field(name="Type",   value=f"{t_norm} {TYPE_EMOJIS[t_norm]}",   inline=True)
    emb.add_field(name="Statut", value=f"{s_norm} {STATUS_EMOJIS[s_norm]}", inline=True)
    emb.set_footer(text="Tape `/contenu liste` pour voir ta liste.")
    await inter.response.send_message(embed=emb, ephemeral=True)


@contenu.command(
    name="liste",
    description="Afficher la liste par statut (embeds séparés, miniatures et mise en forme monospaced)"
)
async def cmd_liste(inter: discord.Interaction):
    uid = str(inter.user.id)
    # Récupère tous les contenus de l'utilisateur
    rows = await bot.pool.fetch(
        "SELECT id, title, content_type, status, rating "
        "FROM contents WHERE user_id=$1 ORDER BY content_type, title",
        uid
    )
    if not rows:
        return await inter.response.send_message("❌ Ta liste est vide.", ephemeral=True)

    # Ordre des statuts et couleurs associées
    statut_order = ["À voir", "En cours", "Terminé"]
    statut_colors = {
        "À voir": 0xe74c3c,
        "En cours": 0xf1c40f,
        "Terminé": 0x2ecc71
    }

    embeds = []
    for st in statut_order:
        group = [r for r in rows if r["status"] == st]
        if not group:
            continue

        # Embed principal pour cette section
        emb = discord.Embed(
            title=f"{st} {STATUS_EMOJIS[st]}",
            color=statut_colors[st],
            timestamp=datetime.utcnow()
        )

        # Miniature (image principale) de la première série de la section
        thumb = await fetch_thumbnail(group[0]["title"], group[0]["content_type"])
        if thumb:
            emb.set_image(url=thumb)

        # Corps du champ en format monospace pour ID et note
        lines = []
        for r in group:
            id_ms = f"`#{r['id']}`"
            rating_ms = f" | `⭐ {r['rating']}/10`" if r["rating"] is not None else ""
            lines.append(
                f"{TYPE_EMOJIS.get(r['content_type'], '')} **{r['title']}** {id_ms}{rating_ms}"
            )

        # On regroupe toutes les lignes dans un seul champ invisible
        emb.add_field(name="\u200b", value="\n".join(lines), inline=False)
        embeds.append(emb)

    # Envoi des embeds (Discord gère l'affichage en séquence)
    await inter.response.send_message(embeds=embeds)

    # Regroupe par statut
    by_stat: Dict[str, List[dict]] = {}
    for r in rows:
        by_stat.setdefault(r['status'], []).append(r)
    for status, group in by_stat.items():
        lines = []
        for r in group:
            line = f"**{r['title']}** {TYPE_EMOJIS.get(r['content_type'], '')} (#{r['id']})"
            if r['rating'] is not None:
                line += f" | {r['rating']}/10"
            lines.append(line)
        emb.add_field(
            name=f"{status} {STATUS_EMOJIS[status]}",
            value="\n".join(lines),
            inline=False
        )
    emb.set_footer(text="Réponds avec une sous‑commande pour modifier ou noter.")
    await inter.response.send_message(embed=emb)

# ————— /contenu noter —————
@contenu.command(name="noter", description="Noter un contenu (0–10)")
@app_commands.describe(id="ID du contenu", note="Note sur 10")
async def cmd_noter(inter: discord.Interaction, id: int, note: int):
    if note < 0 or note > 10:
        return await inter.response.send_message("⚠️ La note doit être entre 0 et 10.", ephemeral=True)
    res = await bot.pool.execute(
        "UPDATE contents SET rating=$1 WHERE id=$2 AND user_id=$3",
        note, id, str(inter.user.id)
    )
    if res.endswith("UPDATE 1"):
        return await inter.response.send_message(f"✅ Contenu #{id} noté **{note}/10**.", ephemeral=True)
    await inter.response.send_message("❌ Contenu non trouvé ou non autorisé.", ephemeral=True)

# ————— /contenu modifier —————
@contenu.command(name="modifier", description="Modifier le statut d'un contenu")
@app_commands.describe(id="ID du contenu", statut="Nouveau statut")
@app_commands.choices(statut=[app_commands.Choice(name=s, value=s) for s in STATUS_EMOJIS])
async def cmd_modifier(inter: discord.Interaction, id: int, statut: app_commands.Choice[str]):
    s_norm = normalize_status(statut.value)
    res = await bot.pool.execute(
        "UPDATE contents SET status=$1 WHERE id=$2 AND user_id=$3",
        s_norm, id, str(inter.user.id)
    )
    if res.endswith("UPDATE 1"):
        return await inter.response.send_message(f"✅ Statut de #{id} passé à **{s_norm}**.", ephemeral=True)
    await inter.response.send_message("❌ Contenu non trouvé ou non autorisé.", ephemeral=True)

# ————— /contenu supprimer —————
@contenu.command(name="supprimer", description="Supprimer contenu par ID")
@app_commands.describe(id="ID du contenu")
async def cmd_supprimer(inter: discord.Interaction, id: int):
    row = await bot.pool.fetchrow(
        "DELETE FROM contents WHERE id=$1 AND user_id=$2 RETURNING title",
        id, str(inter.user.id)
    )
    if row:
        return await inter.response.send_message(f"✅ **{row['title']}** supprimé.", ephemeral=True)
    return await inter.response.send_message("❌ Contenu non trouvé ou non autorisé.", ephemeral=True)

# ————— Lancement —————
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)

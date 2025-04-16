# ✅ Version complète et fonctionnelle du bot Red Herring
# Toutes les commandes sont regroupées sous /contenu
# Slash commands : ajouter, liste, noter, modifier, supprimer, etc. synchronisées automatiquement

import os
import threading
import discord
from discord import app_commands
from discord.ext import commands
import asyncpg
from flask import Flask
from typing import Optional
from datetime import datetime

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# Emoji / Couleurs
COLOR_MAP = {
    "Série": 0x1abc9c,
    "Animé": 0xe74c3c,
    "Webtoon": 0x9b59b6,
    "Manga": 0xf1c40f
}
STATUS_EMOJIS = {
    "À voir": "🔴",
    "En cours": "🟠",
    "Terminé": "🟢"
}

# Healthcheck Flask
app = Flask(__name__)
@app.route("/")
def home():
    return "RedHerring OK"

def run_web():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8000)))

# Bot
class RedHerringBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.pool = None

    async def setup_hook(self):
        self.pool = await asyncpg.create_pool(DATABASE_URL)
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS contents (
                    id SERIAL PRIMARY KEY,
                    user_id TEXT,
                    title TEXT,
                    content_type TEXT,
                    status TEXT,
                    rating INTEGER
                )
            """)
        self.tree.clear_commands(guild=None)
        await self.tree.sync()
        threading.Thread(target=run_web, daemon=True).start()

bot = RedHerringBot()
contenu = app_commands.Group(name="contenu", description="Gérer tes contenus")
bot.tree.add_command(contenu)

# COMMANDES
@contenu.command(name="ajouter", description="Ajouter un contenu")
@app_commands.describe(titre="Titre", type="Type (ex: Manga)", statut="Statut")
async def ajouter(interaction: discord.Interaction, titre: str, type: str, statut: str):
    await bot.pool.execute(
        "INSERT INTO contents (user_id, title, content_type, status) VALUES ($1, $2, $3, $4)",
        str(interaction.user.id), titre, type, statut
    )
    emb = discord.Embed(
        title="Contenu ajouté",
        description=f"**{titre}**",
        color=COLOR_MAP.get(type, 0x95a5a6),
        timestamp=datetime.utcnow()
    )
    emb.add_field(name="Type", value=type, inline=True)
    emb.add_field(name="Statut", value=f"{statut} {STATUS_EMOJIS.get(statut, '')}", inline=True)
    await interaction.response.send_message(embed=emb, ephemeral=True)

@contenu.command(name="liste", description="Afficher la liste")
async def liste(interaction: discord.Interaction):
    rows = await bot.pool.fetch("SELECT * FROM contents WHERE user_id=$1 ORDER BY id", str(interaction.user.id))
    if not rows:
        return await interaction.response.send_message("Ta liste est vide.", ephemeral=True)
    emb = discord.Embed(title=f"Liste de {interaction.user.display_name}", color=0x3498db)
    for r in rows:
        line = f"**{r['title']}** ({r['content_type']}) {STATUS_EMOJIS.get(r['status'], '')}"
        if r['rating']:
            line += f" | Note: {r['rating']}/10"
        emb.add_field(name=f"#{r['id']}", value=line, inline=False)
    await interaction.response.send_message(embed=emb)

@contenu.command(name="noter", description="Noter un contenu")
@app_commands.describe(id="ID du contenu", note="Note sur 10")
async def noter(interaction: discord.Interaction, id: int, note: int):
    if note < 0 or note > 10:
        return await interaction.response.send_message("La note doit être entre 0 et 10.", ephemeral=True)
    res = await bot.pool.execute(
        "UPDATE contents SET rating=$1 WHERE id=$2 AND user_id=$3",
        note, id, str(interaction.user.id)
    )
    if res.endswith("UPDATE 1"):
        await interaction.response.send_message(f"Contenu #{id} noté {note}/10 ✅", ephemeral=True)
    else:
        await interaction.response.send_message("Erreur : contenu non trouvé ou non autorisé.", ephemeral=True)

@contenu.command(name="modifier", description="Modifier le statut")
@app_commands.describe(id="ID du contenu", statut="Nouveau statut")
async def modifier(interaction: discord.Interaction, id: int, statut: str):
    res = await bot.pool.execute(
        "UPDATE contents SET status=$1 WHERE id=$2 AND user_id=$3",
        statut, id, str(interaction.user.id)
    )
    if res.endswith("UPDATE 1"):
        await interaction.response.send_message(f"Statut modifié pour le contenu #{id} ✅", ephemeral=True)
    else:
        await interaction.response.send_message("Erreur : contenu non trouvé.", ephemeral=True)

@contenu.command(name="supprimer", description="Supprimer un contenu")
@app_commands.describe(id="ID du contenu")
async def supprimer(interaction: discord.Interaction, id: int):
    row = await bot.pool.fetchrow("DELETE FROM contents WHERE id=$1 AND user_id=$2 RETURNING title", id, str(interaction.user.id))
    if row:
        await interaction.response.send_message(f"Contenu **{row['title']}** supprimé ✅", ephemeral=True)
    else:
        await interaction.response.send_message("Aucun contenu supprimé.", ephemeral=True)

# LANCEMENT
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)

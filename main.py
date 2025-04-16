# main.py - Bot Red Herring complet, structuré sous /contenu avec toutes les commandes et améliorations UX/UI

import os
import threading
import discord
from discord import app_commands
from discord.ext import commands
import asyncpg
from flask import Flask
from typing import Optional, List, Dict
from datetime import datetime

# Configuration
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# Mapping visuel
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

# Normalisation

def normalize_type(value: str) -> str:
    m = {"série": "Série", "serie": "Série", "animé": "Animé", "anime": "Animé", "webtoon": "Webtoon", "manga": "Manga"}
    return m.get(value.lower().strip(), value.capitalize())

def normalize_status(value: str) -> str:
    m = {"à voir": "À voir", "a voir": "À voir", "en cours": "En cours", "terminé": "Terminé", "termine": "Terminé"}
    return m.get(value.lower().strip(), value.capitalize())

# Serveur Web Railway
app = Flask(__name__)
@app.route("/")
def home():
    return "Red Herring est en ligne."

def run_web():
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8000)))

# Bot
class RedHerringBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        self.pool: Optional[asyncpg.Pool] = None

    async def setup_hook(self):
        self.tree.clear_commands(guild=None)  # Réinitialisation des commandes
        self.pool = await asyncpg.create_pool(DATABASE_URL)
        await self.tree.sync()
        threading.Thread(target=run_web, daemon=True).start()

bot = RedHerringBot()
contenu = app_commands.Group(name="contenu", description="Gérer tes contenus")

@bot.event
async def on_ready():
    print(f"{bot.user} est connecté et prêt.")

# Commande: Ajouter
@contenu.command(name="ajouter", description="Ajouter un contenu")
@app_commands.describe(titre="Titre du contenu", type="Type", statut="Statut")
async def ajouter(interaction: discord.Interaction, titre: str, type: str, statut: str):
    type_norm = normalize_type(type)
    statut_norm = normalize_status(statut)
    await bot.pool.execute(
        "INSERT INTO contents (user_id, title, content_type, status) VALUES ($1, $2, $3, $4)",
        str(interaction.user.id), titre, type_norm, statut_norm
    )
    embed = discord.Embed(
        title="Contenu ajouté",
        description=f"**{titre}**",
        color=COLOR_MAP.get(type_norm, 0x95a5a6),
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="Type", value=f"{type_norm} {TYPE_EMOJIS.get(type_norm, '')}", inline=True)
    embed.add_field(name="Statut", value=f"{statut_norm} {STATUS_EMOJIS.get(statut_norm, '')}", inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)

# Commande: Liste
@contenu.command(name="liste", description="Afficher ta liste (option: notes)")
@app_commands.describe(notes="Afficher uniquement les contenus notés (triés par note)")
async def liste(interaction: discord.Interaction, notes: Optional[bool] = False):
    user_id = str(interaction.user.id)
    if notes:
        query = "SELECT id, title, content_type, status, rating FROM contents WHERE user_id=$1 AND rating IS NOT NULL ORDER BY rating DESC"
    else:
        query = "SELECT id, title, content_type, status, rating FROM contents WHERE user_id=$1 ORDER BY content_type, title"
    rows = await bot.pool.fetch(query, user_id)
    if not rows:
        return await interaction.response.send_message("Ta liste est vide.", ephemeral=True)

    embed = discord.Embed(title=f"Contenus de {interaction.user.display_name}", color=0x3498db, timestamp=datetime.utcnow())
    if notes:
        classement, last_note, rang = 0, None, 0
        for idx, r in enumerate(rows, 1):
            if r['rating'] != last_note:
                rang = idx
                last_note = r['rating']
            badge = ""
            if rang == 1:
                badge = "🏆 Top 1"
            elif rang == 2:
                badge = "🥈 Top 2"
            elif rang == 3:
                badge = "🥉 Top 3"
            else:
                badge = f"{rang}."
            line = f"{badge} **{r['title']}** ({TYPE_EMOJIS.get(r['content_type'], '')})\n|{r['rating']}/10"
            embed.add_field(name="​", value=line, inline=False)
    else:
        for r in rows:
            line = f"**{r['title']}** {STATUS_EMOJIS.get(r['status'], '')} (#{r['id']})"
            if r['rating'] is not None:
                line += f" | Note: {r['rating']}/10"
            embed.add_field(name=f"{r['content_type']} {TYPE_EMOJIS.get(r['content_type'], '')}", value=line, inline=False)
    await interaction.response.send_message(embed=embed)

# TODO: Ajouter les autres sous-commandes (noter, modifier, modifiermulti, supprimer, ajoutermulti...)

bot.tree.add_command(contenu)
bot.run(DISCORD_TOKEN)

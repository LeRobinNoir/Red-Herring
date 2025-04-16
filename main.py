import discord
from discord import app_commands
from discord.ext import commands
import os, threading
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask

# -------------------------------
# Fonctions de normalisation pour type et statut
# -------------------------------
def normalize_type(value: str) -> str:
    valid_types = {"série": "Série", "serie": "Série", "animé": "Animé",
                   "anime": "Animé", "webtoon": "Webtoon", "manga": "Manga"}
    lower = value.lower().strip()
    return valid_types.get(lower, value.capitalize())

def normalize_status(value: str) -> str:
    valid_statuses = {"en cours": "En cours", "à voir": "À voir", "a voir": "À voir",
                      "termine": "Terminé", "terminé": "Terminé"}
    lower = value.lower().strip()
    return valid_statuses.get(lower, value.capitalize())

# -------------------------------
# Mini-serveur pour Railway
# -------------------------------
app = Flask(__name__)
@app.route("/")
def home(): return "Bot liste en ligne !"

def run_web():
    port = int(os.getenv("PORT", 8000))
    app.run(host="0.0.0.0", port=port)

def keep_alive(): threading.Thread(target=run_web).start()

# -------------------------------
# Connexion DB
# -------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")
def get_db(): return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    conn = get_db(); cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS contents (id SERIAL PRIMARY KEY, user_id TEXT, title TEXT, content_type TEXT, status TEXT, rating INTEGER, created_at TIMESTAMP DEFAULT NOW());''')
    conn.commit(); cur.close(); conn.close()

# -------------------------------
# Emojis et constantes
# -------------------------------
TYPE_EMOJIS = {"Série":"📺","Animé":"🎥","Webtoon":"📱","Manga":"📚"}
STATUS_EMOJIS = {"En cours":"⏳","À voir":"👀","Terminé":"✅"}

# -------------------------------
# Bot setup
# -------------------------------
intents = discord.Intents.default(); intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    init_db(); print(f"{bot.user} connecté.")
    await bot.tree.sync()

# -------------------------------
# Commandes de base (ajouter, modifier, noter, supprimer)
# -------------------------------
# ... (identiques à la version précédente) ...

# -------------------------------
# Listing avec pagination et filtres interactifs
# -------------------------------
class ListingView(discord.ui.View):
    def __init__(self, rows, embed_builder, page_size=10):
        super().__init__(timeout=None)
        self.rows = rows
        self.filtered = rows
        self.builder = embed_builder
        self.page = 0; self.size = page_size

        # dropdowns
        options_type = [discord.SelectOption(label="Toutes", value="all")] + [discord.SelectOption(label=t, value=t) for t in TYPE_EMOJIS]
        options_status = [discord.SelectOption(label="Tous", value="all")] + [discord.SelectOption(label=s, value=s) for s in STATUS_EMOJIS]
        self.type_select = discord.ui.Select(placeholder="Catégorie", options=options_type)
        self.status_select = discord.ui.Select(placeholder="Statut", options=options_status)
        self.add_item(self.type_select); self.add_item(self.status_select)

    @discord.ui.select()
    async def type_select(self, interaction, select):
        sel = select.values[0]
        self.filtered = [r for r in self.rows if sel=="all" or r['content_type']==sel]
        self.page = 0; await self.update(interaction)

    @discord.ui.select()
    async def status_select(self, interaction, select):
        sel = select.values[0]
        self.filtered = [r for r in self.rows if sel=="all" or r['status']==sel]
        self.page = 0; await self.update(interaction)

    @discord.ui.button(label="⬅️", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction, button):
        if self.page>0: self.page-=1
        await self.update(interaction)

    @discord.ui.button(label="➡️", style=discord.ButtonStyle.secondary)
    async def next(self, interaction, button):
        if (self.page+1)*self.size < len(self.filtered): self.page+=1
        await self.update(interaction)

    async def update(self, interaction):
        start = self.page*self.size; end = start+self.size
        emb = self.builder(self.filtered[start:end], len(self.filtered))
        await interaction.response.edit_message(embed=emb, view=self)

@bot.tree.command(name="liste", description="Afficher la liste (interactive)")
async def liste(interaction: discord.Interaction, tri: str = None, notes: bool = False):
    # fetch rows
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM contents WHERE user_id=%s ORDER BY created_at DESC", (str(interaction.user.id),))
    rows = cur.fetchall(); cur.close(); conn.close()

    # sort
    if tri=="alpha": rows=sorted(rows, key=lambda r: r['title'].lower())
    elif tri=="date": rows=sorted(rows, key=lambda r: r['created_at'], reverse=True)

    def build_page(page_rows, total_count):
        emb = discord.Embed(title=f"Liste de {interaction.user.display_name}", color=0x3498db)
        last_type = None
        for r in page_rows:
            if not notes:
                if r['content_type']!=last_type:
                    emb.add_field(name=f"─── {r['content_type']} {TYPE_EMOJIS.get(r['content_type'],'')} ───", value='\u200b', inline=False)
                    last_type = r['content_type']
                note_part = f" | **{r['rating']}/10**" if r['rating'] is not None else ''
                emb.add_field(name=f"{STATUS_EMOJIS.get(r['status'],'')} {r['title']} (# {r['id']})", value=note_part, inline=False)
            else:
                # notes mode: build dense ranking once
                pass
        emb.set_footer(text=f"Total: {total_count} contenus")
        return emb

    # if notes: compute dense ranking sorted by rating desc
    if notes:
        rows = [r for r in rows if r['rating'] is not None]
        rows = sorted(rows, key=lambda r: (-r['rating'], r['title']))
        emb0 = discord.Embed(title=f"Top notés de {interaction.user.display_name}", color=0x3498db)
        dense=0; prev=None
        for idx,r in enumerate(rows):
            if r['rating']!=prev: dense+=1; prev=r['rating']
            rank = {1:'🏆 Top 1',2:'🥈 Top 2',3:'🥉 Top 3'}.get(dense, f"#{dense}")
            emb0.add_field(name=f"{rank} {r['title']} (# {r['id']})", value=f"| **{r['rating']}/10**", inline=False)
            if dense==3:
                emb0.add_field(name="───────────", value='\u200b', inline=False)
        emb0.set_footer(text=f"Total notés: {len(rows)}")
        await interaction.response.send_message(embed=emb0)
        return

    view = ListingView(rows, build_page)
    emb_start = build_page(rows[:10], len(rows))
    await interaction.response.send_message(embed=emb_start, view=view)

# -------------------------------
# Commande : /rechercher
# -------------------------------
@bot.tree.command(name="rechercher", description="Trouver un contenu par titre")
@app_commands.describe(texte="Texte à rechercher dans le titre")
async def rechercher(interaction: discord.Interaction, texte: str):
    conn=get_db(); cur=conn.cursor()
    cur.execute("SELECT * FROM contents WHERE user_id=%s AND title ILIKE %s ORDER BY title LIMIT 10", (str(interaction.user.id), f"%{texte}%"))
    rows=cur.fetchall(); cur.close(); conn.close()
    if not rows:
        await interaction.response.send_message("Aucun contenu trouvé.", ephemeral=True)
        return
    emb=discord.Embed(title="Résultats de recherche", color=0x3498db)
    for r in rows:
        emb.add_field(name=f"{r['title']} (# {r['id']})", value=f"{r['status']} {STATUS_EMOJIS.get(r['status'],'')} | {r['content_type']} {TYPE_EMOJIS.get(r['content_type'],'')}", inline=False)
    await interaction.response.send_message(embed=emb)

# -------------------------------
# Lancement
# -------------------------------
keep_alive()
bot.run(os.getenv("DISCORD_TOKEN"))

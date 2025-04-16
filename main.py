import discord
from discord import app_commands
from discord.ext import commands
import os
import threading
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask

# -------------------------------
# Normalisation des saisies
# -------------------------------
def normalize_type(value: str) -> str:
    valid = {
        "série": "Série", "serie": "Série",
        "animé": "Animé", "anime": "Animé",
        "webtoon": "Webtoon", "manga": "Manga"
    }
    key = value.lower().strip()
    return valid.get(key, value.capitalize())

def normalize_status(value: str) -> str:
    valid = {
        "en cours": "En cours",
        "à voir": "À voir", "a voir": "À voir",
        "terminé": "Terminé", "termine": "Terminé"
    }
    key = value.lower().strip()
    return valid.get(key, value.capitalize())

# -------------------------------
# Mini-serveur Flask (Railway)
# -------------------------------
app = Flask(__name__)
@app.route("/")
def home():
    return "Bot Red Herring en ligne !"

def run_web():
    port = int(os.getenv("PORT", 8000))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    threading.Thread(target=run_web).start()

# -------------------------------
# Connexion DB et initialisation
# -------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("La variable DATABASE_URL n'est pas définie")

def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    conn = get_db()
    cur = conn.cursor()
    # Création de la table
    cur.execute('''
        CREATE TABLE IF NOT EXISTS contents (
            id SERIAL PRIMARY KEY,
            user_id TEXT,
            title TEXT,
            content_type TEXT,
            status TEXT,
            rating INTEGER,
            created_at TIMESTAMP DEFAULT NOW()
        );
    ''')
    # Migration : ajout de created_at si manquant
    cur.execute('''
        ALTER TABLE contents
        ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW();
    ''')
    conn.commit()
    cur.close()
    conn.close()

# -------------------------------
# Emojis utilisés
# -------------------------------
TYPE_EMOJIS = {
    "Série": "📺",
    "Animé": "🎥",
    "Webtoon": "📱",
    "Manga": "📚"
}
STATUS_EMOJIS = {
    "En cours": "⏳",
    "À voir": "👀",
    "Terminé": "✅"
}

# -------------------------------
# Bot Discord
# -------------------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    init_db()
    print(f"{bot.user} connecté.")
    try:
        synced = await bot.tree.sync()
        print(f"Commandes synchronisées : {len(synced)}")
    except Exception as e:
        print("Erreur synchronisation commandes :", e)

# -------------------------------
# /ajouter : ajouter un contenu
# -------------------------------
@bot.tree.command(name="ajouter", description="Ajouter un contenu (titre, type, statut)")
@app_commands.describe(
    titre="Titre du contenu",
    type="Type (Manga, Animé, Série, Webtoon)",
    statut="Statut (En cours, À voir, Terminé)"
)
async def ajouter(interaction: discord.Interaction, titre: str, type: str, statut: str):
    t_norm = normalize_type(type)
    s_norm = normalize_status(statut)
    uid = str(interaction.user.id)
    conn = get_db(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO contents (user_id, title, content_type, status) VALUES (%s,%s,%s,%s)",
        (uid, titre, t_norm, s_norm)
    )
    conn.commit(); cur.close(); conn.close()
    embed = discord.Embed(
        title="Contenu ajouté",
        description=f"**{titre}**", color=0x2ecc71
    )
    embed.add_field(name="Type", value=f"{t_norm} {TYPE_EMOJIS.get(t_norm,'')}", inline=True)
    embed.add_field(name="Statut", value=f"{s_norm} {STATUS_EMOJIS.get(s_norm,'')}", inline=True)
    await interaction.response.send_message(embed=embed)

# -------------------------------
# /modifier : modifier statut
# -------------------------------
@bot.tree.command(name="modifier", description="Modifier le statut d'un contenu par ID")
@app_commands.describe(
    id="ID du contenu",
    statut="Nouveau statut (En cours, À voir, Terminé)"
)
async def modifier(interaction: discord.Interaction, id: int, statut: str):
    s_norm = normalize_status(statut)
    uid = str(interaction.user.id)
    conn = get_db(); cur = conn.cursor()
    cur.execute(
        "UPDATE contents SET status=%s WHERE id=%s AND user_id=%s",
        (s_norm, id, uid)
    )
    if cur.rowcount == 0:
        await interaction.response.send_message("Contenu introuvable ou non autorisé.", ephemeral=True)
    else:
        conn.commit()
        await interaction.response.send_message(f"Contenu #{id} modifié en **{s_norm}** {STATUS_EMOJIS.get(s_norm,'')}")
    cur.close(); conn.close()

# -------------------------------
# /noter : attribuer note
# -------------------------------
@bot.tree.command(name="noter", description="Noter un contenu (0-10)")
@app_commands.describe(
    id="ID du contenu",
    note="Note entre 0 et 10"
)
async def noter(interaction: discord.Interaction, id: int, note: int):
    if note < 0 or note > 10:
        return await interaction.response.send_message("Note invalide (0-10)", ephemeral=True)
    uid = str(interaction.user.id)
    conn = get_db(); cur = conn.cursor()
    cur.execute(
        "UPDATE contents SET rating=%s WHERE id=%s AND user_id=%s",
        (note, id, uid)
    )
    if cur.rowcount == 0:
        await interaction.response.send_message("Contenu introuvable.", ephemeral=True)
    else:
        conn.commit()
        await interaction.response.send_message(f"Contenu #{id} noté **{note}/10**")
    cur.close(); conn.close()

# -------------------------------
# /supprimer : supprimer contenus
# -------------------------------
@bot.tree.command(name="supprimer", description="Supprimer contenus (IDs séparés par virgules)")
@app_commands.describe(ids="Ex: 2,4,7")
async def supprimer(interaction: discord.Interaction, ids: str):
    uid = str(interaction.user.id)
    try:
        lst = [int(x.strip()) for x in ids.split(',')]
    except:
        return await interaction.response.send_message("Format d'IDs invalide.", ephemeral=True)
    conn = get_db(); cur = conn.cursor()
    deleted = []
    for cid in lst:
        cur.execute("DELETE FROM contents WHERE id=%s AND user_id=%s RETURNING title", (cid, uid))
        row = cur.fetchone()
        if row: deleted.append(row['title'])
    conn.commit(); cur.close(); conn.close()
    if not deleted:
        await interaction.response.send_message("Aucun contenu supprimé.", ephemeral=True)
    else:
        await interaction.response.send_message("Supprimés: " + ", ".join(deleted))

# -------------------------------
# /ajoutermulti : multi-ajout
# -------------------------------
class MultiAddModal(discord.ui.Modal, title="Ajouter un contenu"):
    title_in = discord.ui.TextInput(label="Titre")
    type_in = discord.ui.TextInput(label="Type")
    status_in = discord.ui.TextInput(label="Statut")
    async def on_submit(self, interaction: discord.Interaction):
        entry = {
            'title': self.title_in.value,
            'type': normalize_type(self.type_in.value),
            'status': normalize_status(self.status_in.value)
        }
        self.view.entries.append(entry)
        await interaction.response.send_message(f"Ajouté **{entry['title']}**", ephemeral=True)

class MultiAddView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.entries = []
    @discord.ui.button(label="Ajouter un contenu", style=discord.ButtonStyle.primary)
    async def add_c(self, interaction, button):
        modal = MultiAddModal()
        modal.view = self
        await interaction.response.send_modal(modal)
    @discord.ui.button(label="Confirmer tout", style=discord.ButtonStyle.green)
    async def confirm(self, interaction, button):
        if not self.entries:
            return await interaction.response.send_message("Rien à ajouter.", ephemeral=True)
        conn = get_db(); cur = conn.cursor()
        names = []
        for e in self.entries:
            cur.execute(
                "INSERT INTO contents(user_id,title,content_type,status) VALUES(%s,%s,%s,%s) RETURNING id",
                (self.user_id, e['title'], e['type'], e['status'])
            )
            res = cur.fetchone()
            names.append(f"{e['title']} (#{res['id']})")
        conn.commit(); cur.close(); conn.close()
        await interaction.response.send_message("Ajouts: " + ", ".join(names))
        self.stop()

@bot.tree.command(name="ajoutermulti", description="Ajouter plusieurs contenus")
async def ajoutermulti(interaction: discord.Interaction):
    view = MultiAddView(str(interaction.user.id))
    await interaction.response.send_message("Cliquez pour ajouter plusieurs contenus.", view=view)

# -------------------------------
# /modifiermulti : multi-modif
# -------------------------------
@bot.tree.command(name="modifiermulti", description="Modifier plusieurs statuts")
async def modifiermulti(interaction: discord.Interaction):
    uid = str(interaction.user.id)
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT id,title FROM contents WHERE user_id=%s", (uid,))
    rows = cur.fetchall(); cur.close(); conn.close()
    if not rows:
        return await interaction.response.send_message("Aucun contenu.", ephemeral=True)
    class ModView(discord.ui.View):
        def __init__(self): super().__init__(timeout=300); self.ids=[]; self.new=None
        @discord.ui.select(
            placeholder="Choisir contenus",
            min_values=1, max_values=len(rows),
            options=[discord.SelectOption(label=f"{r['title']} (#{r['id']})", value=str(r['id'])) for r in rows]
        )
        async def sel(self, interaction, sel):
            self.ids = sel.values
            await interaction.response.send_message(f"{len(self.ids)} sélectionnés.", ephemeral=True)
        @discord.ui.select(
            placeholder="Nouveau statut",
            options=[discord.SelectOption(label=s, value=s) for s in STATUS_EMOJIS]
        )
        async def sel2(self, interaction, sel):
            self.new = sel.values[0]
            await interaction.response.send_message(f"Statut: {self.new}", ephemeral=True)
        @discord.ui.button(label="Confirmer", style=discord.ButtonStyle.green)
        async def conf(self, interaction, btn):
            if not self.ids or not self.new:
                return await interaction.response.send_message("Sélection et statut requis.", ephemeral=True)
            conn2=get_db(); cur2=conn2.cursor()
            for cid in self.ids:
                cur2.execute("UPDATE contents SET status=%s WHERE id=%s AND user_id=%s", (self.new, cid, uid))
            conn2.commit(); cur2.close(); conn2.close()
            await interaction.response.send_message(f"Mis à jour: IDs {', '.join(self.ids)} -> {self.new}")
            self.stop()
    await interaction.response.send_message("Sélectionnez et confirmez.", view=ModView())

# -------------------------------
# /liste et /rechercher déjà définis ci-dessus
# -------------------------------

# -------------------------------
# Démarrage
# -------------------------------
keep_alive()
bot.run(os.getenv("DISCORD_TOKEN"))
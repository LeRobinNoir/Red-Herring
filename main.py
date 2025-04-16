import discord
from discord import app_commands
from discord.ext import commands
import os, threading
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Charger les variables d'environnement depuis .env (en développement)
load_dotenv()

# Récupérer les variables d'environnement
TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

if TOKEN is None:
    raise Exception("DISCORD_TOKEN n'est pas défini.")
if DATABASE_URL is None:
    raise Exception("DATABASE_URL n'est pas défini.")

# -------------------------------
# Serveur Web minimal (pour Railway)
# -------------------------------
from flask import Flask
app = Flask(__name__)

@app.route("/")
def home():
    return "Le bot est en ligne !"

def run_web():
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = threading.Thread(target=run_web)
    t.start()

# -------------------------------
# Connexion à PostgreSQL & initialisation
# -------------------------------
def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    # Table pour gérer la liste de contenus
    cur.execute('''
        CREATE TABLE IF NOT EXISTS contents (
            id SERIAL PRIMARY KEY,
            user_id TEXT,
            title TEXT,
            content_type TEXT,
            status TEXT,
            rating INTEGER
        );
    ''')
    conn.commit()
    cur.close()
    conn.close()

# -------------------------------
# Dictionnaires d'emojis pour affichage
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
# Configuration du Bot Discord
# -------------------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# -------------------------------
# Événement on_ready
# -------------------------------
@bot.event
async def on_ready():
    init_db()
    print(f"{bot.user} est connecté.")
    try:
        synced = await bot.tree.sync()
        print(f"Commandes slash synchronisées : {len(synced)}")
    except Exception as e:
        print(f"Erreur de synchronisation : {e}")

# -------------------------------
# Commande pour ajouter un seul contenu (classique)
# -------------------------------
@bot.tree.command(name="ajouter", description="Ajouter un contenu unique")
@app_commands.describe(
    titre="Titre du contenu",
    type="Type du contenu (ex: Série, Animé, Webtoon, Manga)",
    statut="Statut du contenu (ex: En cours, À voir, Terminé)"
)
async def ajouter(interaction: discord.Interaction, titre: str, type: str, statut: str):
    user_id = str(interaction.user.id)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO contents (user_id, title, content_type, status) VALUES (%s, %s, %s, %s)",
        (user_id, titre, type, statut)
    )
    conn.commit()
    cur.close()
    conn.close()

    embed = discord.Embed(
        title="Contenu ajouté",
        description=f"**{titre}**",
        color=0x3498db
    )
    embed.add_field(name="Type", value=f"{type} {TYPE_EMOJIS.get(type, '')}", inline=True)
    embed.add_field(name="Statut", value=f"{statut} {STATUS_EMOJIS.get(statut, '')}", inline=True)
    await interaction.response.send_message(embed=embed)

# -------------------------------
# Commande pour afficher la liste des contenus (triée par type)
# -------------------------------
@bot.tree.command(name="liste", description="Afficher la liste de contenus (triée par type)")
async def liste(interaction: discord.Interaction, member: discord.Member = None):
    target = member or interaction.user
    user_id = str(target.id)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, title, content_type, status, rating 
        FROM contents 
        WHERE user_id = %s 
        ORDER BY content_type, title
    """, (user_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    if not rows:
        await interaction.response.send_message(f"{target.display_name} n'a aucun contenu dans sa liste.", ephemeral=True)
        return

    # Groupe par type
    by_type = {}
    for row in rows:
        ctype = row['content_type']
        by_type.setdefault(ctype, []).append(row)

    embed = discord.Embed(title=f"Liste de contenus de {target.display_name}", color=0x3498db)

    # Ordre fixe pour les types connus
    known_types = ["Série", "Animé", "Webtoon", "Manga"]
    sorted_types = [t for t in known_types if t in by_type] + sorted(t for t in by_type if t not in known_types)

    for ctype in sorted_types:
        contenu = ""
        for row in by_type[ctype]:
            entry_id = row['id']
            titre = row['title']
            statut = row['status']
            rating = row['rating']
            line = f"- **{titre}** {STATUS_EMOJIS.get(statut, '')} (#{entry_id})"
            if rating is not None:
                line += f" | Note: {rating}/10"
            contenu += line + "\n"
        embed.add_field(
            name=f"{ctype} {TYPE_EMOJIS.get(ctype, '')}",
            value=contenu,
            inline=False
        )
    await interaction.response.send_message(embed=embed)

# -------------------------------
# Commande pour modifier un contenu unique
# -------------------------------
@bot.tree.command(name="modifier", description="Modifier le statut d'un contenu par ID")
@app_commands.describe(
    id="ID du contenu à modifier",
    nouveau_statut="Nouveau statut (ex: En cours, À voir, Terminé)"
)
async def modifier(interaction: discord.Interaction, id: int, nouveau_statut: str):
    user_id = str(interaction.user.id)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE contents SET status = %s WHERE id = %s AND user_id = %s", (nouveau_statut, id, user_id))
    if cur.rowcount == 0:
        await interaction.response.send_message("Contenu non trouvé ou pas autorisé.", ephemeral=True)
    else:
        conn.commit()
        embed = discord.Embed(
            title="Contenu modifié",
            description=f"L'ID **{id}** a été mis à jour en **{nouveau_statut} {STATUS_EMOJIS.get(nouveau_statut, '')}**.",
            color=0x3498db
        )
        await interaction.response.send_message(embed=embed)
    cur.close()
    conn.close()

# -------------------------------
# Commande pour supprimer un ou plusieurs contenus
# -------------------------------
@bot.tree.command(name="supprimer", description="Supprimer un ou plusieurs contenus")
@app_commands.describe(
    ids="IDs des contenus à supprimer, séparés par des virgules (ex: 3,5,8)"
)
async def supprimer(interaction: discord.Interaction, ids: str):
    user_id = str(interaction.user.id)
    # Découper la chaîne en liste d'entiers
    try:
        id_list = [int(x.strip()) for x in ids.split(",") if x.strip().isdigit()]
    except Exception:
        await interaction.response.send_message("Veuillez fournir des IDs valides séparés par des virgules.", ephemeral=True)
        return
    if not id_list:
        await interaction.response.send_message("Aucun ID valide fourni.", ephemeral=True)
        return
    
    conn = get_db_connection()
    cur = conn.cursor()
    deleted = []
    for cid in id_list:
        cur.execute("DELETE FROM contents WHERE id = %s AND user_id = %s RETURNING title", (cid, user_id))
        result = cur.fetchone()
        if result is not None:
            deleted.append(result['title'])
    conn.commit()
    cur.close()
    conn.close()
    
    if not deleted:
        await interaction.response.send_message("Aucun contenu supprimé. Vérifiez les IDs fournis.", ephemeral=True)
    else:
        embed = discord.Embed(
            title="Suppression réussie",
            description="Les contenus suivants ont été supprimés : " + ", ".join(deleted),
            color=0x3498db
        )
        await interaction.response.send_message(embed=embed)

# -------------------------------
# Commande pour ajouter plusieurs contenus (ajout multiple)
# -------------------------------
# Modal pour saisir un contenu individuel
class ContentModal(discord.ui.Modal, title="Ajouter un contenu"):
    titre = discord.ui.TextInput(label="Titre", placeholder="Saisir le titre", max_length=100)
    type_ = discord.ui.TextInput(label="Type", placeholder="Ex: Série, Animé, Webtoon, Manga", max_length=50)
    statut = discord.ui.TextInput(label="Statut", placeholder="Ex: En cours, À voir, Terminé", max_length=50)
    
    async def on_submit(self, interaction: discord.Interaction):
        # Ajoute le contenu dans la liste de la vue associée
        self.view.entries.append({
            "titre": self.titre.value,
            "type": self.type_.value,
            "statut": self.statut.value
        })
        await interaction.response.send_message(f"Contenu **{self.titre.value}** ajouté à la liste temporaire.", ephemeral=True)

# Vue interactive pour la saisie multiple
class MultiAddView(discord.ui.View):
    def __init__(self, user_id: str):
        super().__init__(timeout=300)  # Timeout de 5 minutes
        self.user_id = user_id
        self.entries = []  # Liste des contenus saisis
    
    @discord.ui.button(label="Ajouter un contenu", style=discord.ButtonStyle.primary)
    async def ajouter_un(self, button: discord.ui.Button, interaction: discord.Interaction):
        modal = ContentModal()
        modal.view = self  # Associe cette vue au modal pour stocker les entrées
        await interaction.response.send_modal(modal)
    
    @discord.ui.button(label="Confirmer tout", style=discord.ButtonStyle.green)
    async def confirmer_tout(self, button: discord.ui.Button, interaction: discord.Interaction):
        if not self.entries:
            await interaction.response.send_message("Aucun contenu à ajouter.", ephemeral=True)
            return
        conn = get_db_connection()
        cur = conn.cursor()
        titres_ajoutes = []
        for entry in self.entries:
            titre = entry["titre"]
            ctype = entry["type"]
            statut = entry["statut"]
            cur.execute(
                "INSERT INTO contents (user_id, title, content_type, status) VALUES (%s, %s, %s, %s) RETURNING id",
                (self.user_id, titre, ctype, statut)
            )
            res = cur.fetchone()
            if res is not None:
                titres_ajoutes.append(f"{titre} (ID : {res['id']})")
        conn.commit()
        cur.close()
        conn.close()
        embed = discord.Embed(
            title="Contenus ajoutés",
            description="\n".join(titres_ajoutes),
            color=0x3498db
        )
        await interaction.response.send_message(embed=embed)
        self.stop()

@bot.tree.command(name="ajoutermulti", description="Ajouter plusieurs contenus en une fois.")
async def ajoutermulti(interaction: discord.Interaction):
    view = MultiAddView(user_id=str(interaction.user.id))
    await interaction.response.send_message(
        "Utilise le bouton **Ajouter un contenu** pour saisir chaque contenu. Puis clique sur **Confirmer tout** pour enregistrer tous les contenus.",
        view=view
    )

# -------------------------------
# Commande pour modifier plusieurs contenus simultanément
# -------------------------------
@bot.tree.command(name="modifiermulti", description="Modifier le statut de plusieurs contenus simultanément.")
async def modifiermulti(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, title FROM contents WHERE user_id = %s ORDER BY id", (user_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    if not rows:
        await interaction.response.send_message("Aucun contenu trouvé à modifier.", ephemeral=True)
        return

    class MultiModifyView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=300)
            self.selected_ids = []
            self.new_status = None

        @discord.ui.select(
            placeholder="Sélectionnez les contenus à modifier",
            min_values=1,
            max_values=len(rows),
            options=[discord.SelectOption(label=f"{row['title']} (ID : {row['id']})", value=str(row['id'])) for row in rows]
        )
        async def select_items(self, interaction: discord.Interaction, select: discord.ui.Select):
            self.selected_ids = select.values
            await interaction.response.send_message(f"{len(self.selected_ids)} contenu(s) sélectionné(s) pour modification.", ephemeral=True)

        @discord.ui.select(
            placeholder="Sélectionnez le nouveau statut",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="En cours", value="En cours"),
                discord.SelectOption(label="À voir", value="À voir"),
                discord.SelectOption(label="Terminé", value="Terminé")
            ]
        )
        async def select_status(self, interaction: discord.Interaction, select: discord.ui.Select):
            self.new_status = select.values[0]
            await interaction.response.send_message(f"Nouveau statut sélectionné : {self.new_status}.", ephemeral=True)

        @discord.ui.button(label="Confirmer modification", style=discord.ButtonStyle.green)
        async def confirm_modif(self, button: discord.ui.Button, interaction: discord.Interaction):
            if not self.selected_ids or not self.new_status:
                await interaction.response.send_message("Veuillez sélectionner au moins un contenu et un nouveau statut.", ephemeral=True)
                return
            conn2 = get_db_connection()
            cur2 = conn2.cursor()
            for cid in self.selected_ids:
                cur2.execute("UPDATE contents SET status = %s WHERE id = %s AND user_id = %s", (self.new_status, cid, user_id))
            conn2.commit()
            cur2.close()
            conn2.close()
            embed = discord.Embed(
                title="Modification appliquée",
                description=f"Les contenus avec les IDs : {', '.join(self.selected_ids)} ont été mis à jour en **{self.new_status}**.",
                color=0x3498db
            )
            await interaction.response.send_message(embed=embed)
            self.stop()

    view = MultiModifyView()
    await interaction.response.send_message("Sélectionnez les contenus à modifier, choisissez le nouveau statut, puis confirmez.", view=view)

# -------------------------------
# Lancement du bot et du serveur web
# -------------------------------
keep_alive()
bot.run(TOKEN)

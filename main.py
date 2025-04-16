import discord
from discord import app_commands
from discord.ext import commands
import os, threading
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask

# -------------------------------
# Fonctions de normalisation pour le type et le statut
# -------------------------------
def normalize_type(value: str) -> str:
    valid_types = {
        "série": "Série",
        "serie": "Série",
        "animé": "Animé",
        "anime": "Animé",
        "webtoon": "Webtoon",
        "manga": "Manga"
    }
    lower = value.lower().strip()
    return valid_types.get(lower, value.capitalize())

def normalize_status(value: str) -> str:
    valid_statuses = {
        "en cours": "En cours",
        "à voir": "À voir",
        "a voir": "À voir",
        "termine": "Terminé",
        "terminé": "Terminé"
    }
    lower = value.lower().strip()
    return valid_statuses.get(lower, value.capitalize())

# -------------------------------
# Serveur web minimal (pour Railway)
# -------------------------------
app = Flask(__name__)

@app.route("/")
def home():
    return "Le bot liste est en ligne !"

def run_web():
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)

def keep_alive():
    t = threading.Thread(target=run_web)
    t.start()

# -------------------------------
# Variables d'environnement
# -------------------------------
TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

if TOKEN is None:
    raise Exception("La variable DISCORD_TOKEN n'est pas définie.")
if DATABASE_URL is None:
    raise Exception("La variable DATABASE_URL n'est pas définie.")

# -------------------------------
# Connexion à PostgreSQL et initialisation de la base
# -------------------------------
def get_db_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
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
# Dictionnaires d'emojis pour l'affichage
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
# Commande : /ajouter (Ajouter un contenu unique)
# -------------------------------
@bot.tree.command(name="ajouter", description="Ajouter un contenu (titre, type, statut)")
@app_commands.describe(
    titre="Titre du contenu (ex: One Piece)",
    type="Type de contenu (ex: Manga, Animé, Webtoon, Série)",
    statut="Statut du contenu (ex: En cours, À voir, Terminé)"
)
async def ajouter(interaction: discord.Interaction, titre: str, type: str, statut: str):
    type_normalized = normalize_type(type)
    statut_normalized = normalize_status(statut)
    user_id = str(interaction.user.id)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO contents (user_id, title, content_type, status) VALUES (%s, %s, %s, %s)",
        (user_id, titre, type_normalized, statut_normalized)
    )
    conn.commit()
    cur.close()
    conn.close()
    embed = discord.Embed(
        title="Nouveau contenu ajouté",
        description=f"**{titre}**",
        color=0x3498db
    )
    embed.add_field(name="Type", value=f"{type_normalized} {TYPE_EMOJIS.get(type_normalized, '')}", inline=True)
    embed.add_field(name="Statut", value=f"{statut_normalized} {STATUS_EMOJIS.get(statut_normalized, '')}", inline=True)
    await interaction.response.send_message(embed=embed)

# -------------------------------
# Commande : /liste (Afficher la liste avec filtres et classement par note)
# -------------------------------
@bot.tree.command(name="liste", description="Afficher la liste de contenus. Filtrez par catégorie, statut ou notes.")
@app_commands.describe(
    member="Afficher la liste d'un autre utilisateur (optionnel)",
    categorie="Filtrer par type (ex: Manga, Animé, Webtoon, Série) (optionnel)",
    statut="Filtrer par statut (ex: En cours, À voir, Terminé) (optionnel)",
    notes="Si vrai, affiche uniquement les contenus notés triés par note décroissante (optionnel, false par défaut)"
)
async def liste(interaction: discord.Interaction, member: discord.Member = None, categorie: str = None, statut: str = None, notes: bool = False):
    target = member or interaction.user
    user_id = str(target.id)
    conn = get_db_connection()
    cur = conn.cursor()
    query = "SELECT id, title, content_type, status, rating FROM contents WHERE user_id = %s"
    params = [user_id]
    if categorie:
        categorie_norm = normalize_type(categorie)
        query += " AND content_type = %s"
        params.append(categorie_norm)
    if statut:
        statut_norm = normalize_status(statut)
        query += " AND status = %s"
        params.append(statut_norm)
    if notes:
        query += " AND rating IS NOT NULL ORDER BY rating DESC, title"
    else:
        query += " ORDER BY content_type, title"
    cur.execute(query, tuple(params))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    if not rows:
        await interaction.response.send_message(
            f"{target.display_name} n'a aucun contenu correspondant aux critères.",
            ephemeral=True
        )
        return
    
    embed = discord.Embed(color=0x3498db)
    if notes:
        embed.title = f"Contenus notés de {target.display_name} (classés par note décroissante)"
        # Calcul du classement avec égalité
        lines = []
        current_rank = 0
        index = 0
        previous_rating = None
        for row in rows:
            index += 1
            rating = row['rating']
            if previous_rating is None or rating < previous_rating:
                current_rank = index
            previous_rating = rating
            # Si plusieurs contenus ont la même note, ils partagent le même rang
            if current_rank == 1:
                rank_str = "🏆 Top 1"
            elif current_rank == 2:
                rank_str = "🥈 Top 2"
            elif current_rank == 3:
                rank_str = "🥉 Top 3"
            else:
                rank_str = f"{current_rank}."
            lines.append(f"{rank_str} **{row['title']}** {STATUS_EMOJIS.get(row['status'], '')} (#{row['id']}) | Note: {rating}/10")
        embed.description = "\n".join(lines)
    else:
        embed.title = f"Liste de contenus de {target.display_name}"
        by_type = {}
        for row in rows:
            ctype = row['content_type']
            by_type.setdefault(ctype, []).append(row)
        known_types = ["Série", "Animé", "Webtoon", "Manga"]
        sorted_types = [t for t in known_types if t in by_type] + sorted(t for t in by_type if t not in known_types)
        for ctype in sorted_types:
            contenu = ""
            for row in by_type[ctype]:
                contenu += f"- **{row['title']}** {STATUS_EMOJIS.get(row['status'], '')} (#{row['id']})"
                if row['rating'] is not None:
                    contenu += f" | Note: {row['rating']}/10"
                contenu += "\n"
            embed.add_field(name=f"{ctype} {TYPE_EMOJIS.get(ctype, '')}", value=contenu, inline=False)
    await interaction.response.send_message(embed=embed)

# -------------------------------
# Commande : /modifier (Modifier un contenu unique par ID)
# -------------------------------
@bot.tree.command(name="modifier", description="Modifier le statut d'un contenu par ID")
@app_commands.describe(
    id="ID du contenu",
    nouveau_statut="Nouveau statut (ex: En cours, À voir, Terminé)"
)
async def modifier(interaction: discord.Interaction, id: int, nouveau_statut: str):
    nouveau_statut_norm = normalize_status(nouveau_statut)
    user_id = str(interaction.user.id)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE contents SET status = %s WHERE id = %s AND user_id = %s",
                (nouveau_statut_norm, id, user_id))
    if cur.rowcount == 0:
        await interaction.response.send_message("Contenu introuvable ou non autorisé.", ephemeral=True)
        conn.close()
        return
    conn.commit()
    cur.close()
    conn.close()
    embed = discord.Embed(
        title="Statut modifié",
        description=f"Le contenu #{id} a été mis à jour en **{nouveau_statut_norm} {STATUS_EMOJIS.get(nouveau_statut_norm, '')}**.",
        color=0x3498db
    )
    await interaction.response.send_message(embed=embed)

# -------------------------------
# Commande : /noter (Attribuer une note sur 10 à un contenu)
# -------------------------------
@bot.tree.command(name="noter", description="Attribuer une note à un contenu (sur 10)")
@app_commands.describe(
    id="ID du contenu",
    note="Note (entre 0 et 10)"
)
async def noter(interaction: discord.Interaction, id: int, note: int):
    if note < 0 or note > 10:
        await interaction.response.send_message("La note doit être comprise entre 0 et 10.", ephemeral=True)
        return
    user_id = str(interaction.user.id)
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE contents SET rating = %s WHERE id = %s AND user_id = %s",
                (note, id, user_id))
    if cur.rowcount == 0:
        await interaction.response.send_message("Contenu introuvable ou non autorisé.", ephemeral=True)
        conn.close()
        return
    conn.commit()
    cur.close()
    conn.close()
    await interaction.response.send_message(f"Contenu #{id} noté **{note}/10** avec succès !")

# -------------------------------
# Commande : /supprimer (Supprimer un ou plusieurs contenus)
# -------------------------------
@bot.tree.command(name="supprimer", description="Supprimer un ou plusieurs contenus (IDs séparés par des virgules)")
@app_commands.describe(
    ids="IDs des contenus à supprimer, séparés par des virgules (ex: 2,4,9)"
)
async def supprimer(interaction: discord.Interaction, ids: str):
    user_id = str(interaction.user.id)
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
        row = cur.fetchone()
        if row:
            deleted.append(row['title'])
    conn.commit()
    cur.close()
    conn.close()
    if not deleted:
        await interaction.response.send_message("Aucun contenu supprimé (IDs invalides ou non autorisés).", ephemeral=True)
    else:
        embed = discord.Embed(
            title="Suppression réussie",
            description="Les contenus suivants ont été supprimés : " + ", ".join(deleted),
            color=0x3498db
        )
        await interaction.response.send_message(embed=embed)

# -------------------------------
# Commande : /ajoutermulti (Ajouter plusieurs contenus via interaction)
# -------------------------------
class ContentModal(discord.ui.Modal, title="Ajouter un contenu"):
    titre = discord.ui.TextInput(label="Titre", placeholder="Ex: One Piece", max_length=100)
    type_ = discord.ui.TextInput(label="Type", placeholder="Ex: Manga, Animé, etc.", max_length=50)
    statut = discord.ui.TextInput(label="Statut", placeholder="Ex: En cours, À voir, Terminé", max_length=50)

    async def on_submit(self, interaction: discord.Interaction):
        entry = {
            "titre": self.titre.value,
            "type": normalize_type(self.type_.value),
            "statut": normalize_status(self.statut.value)
        }
        self.view.entries.append(entry)
        await interaction.response.send_message(f"Contenu **{self.titre.value}** ajouté à la liste temporaire.", ephemeral=True)

class MultiAddView(discord.ui.View):
    def __init__(self, user_id: str):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.entries = []

    @discord.ui.button(label="Ajouter un contenu", style=discord.ButtonStyle.primary)
    async def add_content(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = ContentModal()
        modal.view = self
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Confirmer tout", style=discord.ButtonStyle.green)
    async def confirm_all(self, interaction: discord.Interaction, button: discord.ui.Button):
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
            if res:
                titres_ajoutes.append(f"{titre} (ID: {res['id']})")
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
        "Clique sur **Ajouter un contenu** pour saisir chaque contenu, puis sur **Confirmer tout** pour enregistrer le tout.",
        view=view
    )

# -------------------------------
# Commande : /modifiermulti (Modifier plusieurs contenus simultanément)
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
        await interaction.response.send_message("Aucun contenu à modifier.", ephemeral=True)
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
            options=[discord.SelectOption(label=f"{row['title']} (ID: {row['id']})", value=str(row['id'])) for row in rows]
        )
        async def select_contents(self, interaction: discord.Interaction, select: discord.ui.Select):
            self.selected_ids = select.values
            await interaction.response.send_message(f"{len(self.selected_ids)} contenu(s) sélectionné(s).", ephemeral=True)

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
        async def confirm_modif(self, interaction: discord.Interaction, button: discord.ui.Button):
            if not self.selected_ids_

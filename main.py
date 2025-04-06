import discord
from discord import app_commands
from discord.ext import commands
import os, threading
from flask import Flask
import psycopg2
from psycopg2.extras import RealDictCursor

# ===============================
# Serveur web minimal (pour Railway)
# ===============================
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

# ===============================
# Configuration de la base de données PostgreSQL
# ===============================
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL is None:
    raise Exception("DATABASE_URL n'est pas défini dans les variables d'environnement.")

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

# ===============================
# Dictionnaires d'emojis
# ===============================
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

# ===============================
# Configuration du bot Discord
# ===============================
TOKEN = os.getenv("DISCORD_TOKEN")
if TOKEN is None:
    raise Exception("DISCORD_TOKEN n'est pas défini dans les variables d'environnement.")

intents = discord.Intents.default()
intents.message_content = True  # Nécessaire pour lire le contenu des messages

bot = commands.Bot(command_prefix="!", intents=intents)

# ===============================
# Vues interactives (UI)
# ===============================
class AddContentView(discord.ui.View):
    """
    Vue interactive pour ajouter un contenu (ou plusieurs) via des menus déroulants.
    """
    def __init__(self, user_id, title, multiple=False, titles=None):
        super().__init__()
        self.user_id = user_id
        self.title = title      # Titre pour un ajout simple
        self.titles = titles    # Liste de titres pour un ajout multiple
        self.multiple = multiple
        self.selected_type = None
        self.selected_status = None

    @discord.ui.select(
        placeholder="Sélectionne le type de contenu",
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(label="Série", description="Ajouter une série"),
            discord.SelectOption(label="Animé", description="Ajouter un animé"),
            discord.SelectOption(label="Webtoon", description="Ajouter un webtoon"),
            discord.SelectOption(label="Manga", description="Ajouter un manga")
        ]
    )
    async def select_type(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.selected_type = select.values[0]
        # Confirmation éphémère
        await interaction.response.send_message(
            f"Type sélectionné : **{self.selected_type} {TYPE_EMOJIS.get(self.selected_type, '')}**", 
            ephemeral=True
        )

    @discord.ui.select(
        placeholder="Sélectionne le statut",
        min_values=1,
        max_values=1,
        options=[
            discord.SelectOption(label="En cours", description="En cours de visionnage/lecture"),
            discord.SelectOption(label="À voir", description="À voir/à lire"),
            discord.SelectOption(label="Terminé", description="Contenu terminé")
        ]
    )
    async def select_status(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.selected_status = select.values[0]
        # Confirmation éphémère
        await interaction.response.send_message(
            f"Statut sélectionné : **{self.selected_status} {STATUS_EMOJIS.get(self.selected_status, '')}**", 
            ephemeral=True
        )

    @discord.ui.button(label="Confirmer", style=discord.ButtonStyle.green)
    async def confirm(self, button: discord.ui.Button, interaction: discord.Interaction):
        if self.selected_type is None or self.selected_status is None:
            await interaction.response.send_message("Merci de sélectionner le type et le statut.", ephemeral=True)
            return

        conn = get_db_connection()
        cur = conn.cursor()
        if not self.multiple:
            cur.execute("INSERT INTO contents (user_id, title, content_type, status) VALUES (%s, %s, %s, %s)",
                        (self.user_id, self.title, self.selected_type, self.selected_status))
            content_title = self.title
        else:
            titles_added = []
            for t in self.titles:
                t = t.strip()
                if t:
                    cur.execute("INSERT INTO contents (user_id, title, content_type, status) VALUES (%s, %s, %s, %s)",
                                (self.user_id, t, self.selected_type, self.selected_status))
                    titles_added.append(t)
            content_title = ", ".join(titles_added)
        conn.commit()
        cur.close()
        conn.close()

        # Embed public montrant le contenu ajouté
        embed = discord.Embed(
            title="Nouveau contenu ajouté",
            description=f"**{content_title}**",
            color=0x3498db
        )
        embed.add_field(name="Type", value=f"{self.selected_type} {TYPE_EMOJIS.get(self.selected_type, '')}", inline=True)
        embed.add_field(name="Statut", value=f"{self.selected_status} {STATUS_EMOJIS.get(self.selected_status, '')}", inline=True)

        await interaction.response.send_message(embed=embed)
        self.stop()

# ===============================
# Commandes Slash du bot
# ===============================
@bot.tree.command(name="ajouter", description="Ajouter un contenu")
async def ajouter(interaction: discord.Interaction, title: str):
    view = AddContentView(user_id=str(interaction.user.id), title=title)
    await interaction.response.send_message(
        f"Ajout du contenu : **{title}**\nSélectionne le type et le statut :", 
        view=view
    )

@bot.tree.command(name="ajouterplus", description="Ajouter plusieurs contenus")
async def ajouterplus(interaction: discord.Interaction, titles: str):
    title_list = titles.split(',')
    view = AddContentView(user_id=str(interaction.user.id), title=None, multiple=True, titles=title_list)
    titles_clean = ', '.join([t.strip() for t in title_list if t.strip()])
    await interaction.response.send_message(
        f"Ajout de plusieurs contenus : **{titles_clean}**\nSélectionne le type et le statut pour tous :", 
        view=view
    )

@bot.tree.command(name="liste", description="Afficher la liste de contenus d'un utilisateur (triée par type)")
async def liste(interaction: discord.Interaction, member: discord.Member = None):
    """
    Affiche la liste des contenus d'un utilisateur, triés et regroupés par type de contenu.
    """
    target = member or interaction.user
    conn = get_db_connection()
    cur = conn.cursor()
    # On trie d'abord par content_type, puis par title ou id
    cur.execute("""
        SELECT id, title, content_type, status, rating 
        FROM contents 
        WHERE user_id = %s 
        ORDER BY content_type, title
    """, (str(target.id),))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        await interaction.response.send_message(f"{target.display_name} n'a aucun contenu dans sa liste.", ephemeral=True)
        return

    # On groupe par type
    by_type = {}
    for row in rows:
        ctype = row['content_type']
        if ctype not in by_type:
            by_type[ctype] = []
        by_type[ctype].append(row)

    embed = discord.Embed(title=f"Liste de contenus de {target.display_name}", color=0x3498db)

    # Pour un ordre fixe, on peut lister les types connus dans un certain ordre, sinon on tri par ordre alpha
    known_types = ["Série", "Animé", "Webtoon", "Manga"]
    # On récupère aussi d'éventuels types hors du dictionnaire
    sorted_types = [t for t in known_types if t in by_type] + sorted(t for t in by_type if t not in known_types)

    for ctype in sorted_types:
        contents_str = ""
        for row in by_type[ctype]:
            entry_id = row['id']
            title = row['title']
            status = row['status']
            rating = row['rating']
            note_str = f" | Note : {rating}/10" if rating is not None else ""
            # On assemble les infos dans une ligne
            contents_str += f"**{title}** {STATUS_EMOJIS.get(status, '')} (ID : {entry_id}){note_str}\n"
        # On ajoute un champ par type
        embed.add_field(
            name=f"{ctype} {TYPE_EMOJIS.get(ctype, '')}",
            value=contents_str,
            inline=False
        )

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="modifier", description="Modifier le statut d'un contenu par ID")
async def modifier(interaction: discord.Interaction, id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT title, content_type, status FROM contents WHERE id = %s AND user_id = %s", (id, str(interaction.user.id)))
    row = cur.fetchone()
    if not row:
        await interaction.response.send_message("Contenu non trouvé ou vous n'êtes pas le propriétaire de ce contenu.", ephemeral=True)
        cur.close()
        conn.close()
        return
    title = row['title']
    current_status = row['status']
    cur.close()
    conn.close()

    class ModifierStatusView(discord.ui.View):
        def __init__(self, user_id, content_id):
            super().__init__()
            self.user_id = user_id
            self.content_id = content_id
            self.new_status = None

        @discord.ui.select(
            placeholder="Sélectionne le nouveau statut",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="En cours", description="En cours de visionnage/lecture"),
                discord.SelectOption(label="À voir", description="À voir/à lire"),
                discord.SelectOption(label="Terminé", description="Contenu terminé")
            ]
        )
        async def select_new_status(self, interaction: discord.Interaction, select: discord.ui.Select):
            self.new_status = select.values[0]
            await interaction.response.send_message(
                f"Nouveau statut sélectionné : **{self.new_status} {STATUS_EMOJIS.get(self.new_status, '')}**", 
                ephemeral=True
            )

        @discord.ui.button(label="Confirmer", style=discord.ButtonStyle.green)
        async def confirm(self, button: discord.ui.Button, interaction: discord.Interaction):
            if self.new_status is None:
                await interaction.response.send_message("Merci de sélectionner un nouveau statut.", ephemeral=True)
                return
            conn2 = get_db_connection()
            cur2 = conn2.cursor()
            cur2.execute("UPDATE contents SET status = %s WHERE id = %s AND user_id = %s", (self.new_status, self.content_id, str(interaction.user.id)))
            conn2.commit()
            cur2.close()
            conn2.close()
            await interaction.response.send_message("Statut mis à jour avec succès !", ephemeral=True)
            self.stop()

    view = ModifierStatusView(user_id=str(interaction.user.id), content_id=id)
    await interaction.response.send_message(
        f"Modification du statut pour **{title}** (Actuel : {current_status} {STATUS_EMOJIS.get(current_status, '')}). Sélectionne le nouveau statut :",
        view=view
    )

@bot.tree.command(name="supprimer", description="Supprimer du contenu par type et/ou statut")
@app_commands.describe(
    member="L'utilisateur dont vous voulez supprimer le contenu (par défaut : vous-même)",
    content_type="Filtrer par type de contenu",
    status="Filtrer par statut"
)
@app_commands.choices(content_type=[
    app_commands.Choice(name="Série 📺", value="Série"),
    app_commands.Choice(name="Animé 🎥", value="Animé"),
    app_commands.Choice(name="Webtoon 📱", value="Webtoon"),
    app_commands.Choice(name="Manga 📚", value="Manga")
])
@app_commands.choices(status=[
    app_commands.Choice(name="En cours ⏳", value="En cours"),
    app_commands.Choice(name="À voir 👀", value="À voir"),
    app_commands.Choice(name="Terminé ✅", value="Terminé")
])
async def supprimer(interaction: discord.Interaction, member: discord.Member = None, content_type: str = None, status: str = None):
    target = member or interaction.user
    if target.id != interaction.user.id and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Vous ne pouvez supprimer que vos propres contenus.", ephemeral=True)
        return

    conn = get_db_connection()
    cur = conn.cursor()

    query = "SELECT id, title, content_type, status FROM contents WHERE user_id = %s"
    params = [str(target.id)]
    if content_type is not None:
        query += " AND content_type = %s"
        params.append(content_type)
    if status is not None:
        query += " AND status = %s"
        params.append(status)

    cur.execute(query, tuple(params))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        await interaction.response.send_message("Aucun contenu correspondant n'a été trouvé.", ephemeral=True)
        return

    class DeleteContentView(discord.ui.View):
        def __init__(self, entries):
            super().__init__()
            self.entries = entries
            self.selected_ids = []
            options = []
            for entry in entries:
                entry_id = entry['id']
                title = entry['title']
                c_type = entry['content_type']
                c_status = entry['status']
                label = f"{entry_id} - {title}"
                description = f"Type: {c_type} {TYPE_EMOJIS.get(c_type, '')} | Statut: {c_status} {STATUS_EMOJIS.get(c_status, '')}"
                options.append(discord.SelectOption(label=label, value=str(entry_id), description=description))
            self.select = discord.ui.Select(
                placeholder="Sélectionnez les contenus à supprimer",
                min_values=1,
                max_values=len(options),
                options=options
            )
            self.select.callback = self.select_callback
            self.add_item(self.select)

        async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
            self.selected_ids = select.values
            await interaction.response.send_message(f"{len(self.selected_ids)} contenu(s) sélectionné(s) pour suppression.", ephemeral=True)

        @discord.ui.button(label="Confirmer suppression", style=discord.ButtonStyle.red)
        async def confirm(self, button: discord.ui.Button, interaction: discord.Interaction):
            if not self.selected_ids:
                await interaction.response.send_message("Aucun contenu sélectionné.", ephemeral=True)
                return
            conn2 = get_db_connection()
            cur2 = conn2.cursor()
            for entry_id in self.selected_ids:
                cur2.execute("DELETE FROM contents WHERE id = %s AND user_id = %s", (entry_id, str(target.id)))
            conn2.commit()
            cur2.close()
            conn2.close()
            await interaction.response.send_message("Contenu(s) supprimé(s) avec succès.", ephemeral=True)
            self.stop()

    view = DeleteContentView(rows)
    await interaction.response.send_message("Contenus trouvés. Sélectionnez celui(s) à supprimer :", view=view, ephemeral=True)

@bot.tree.command(name="noter", description="Attribuer une note à un contenu (sur 10)")
async def noter(interaction: discord.Interaction, id: int, note: int):
    if note < 0 or note > 10:
        await interaction.response.send_message("La note doit être comprise entre 0 et 10.", ephemeral=True)
        return

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT title FROM contents WHERE id = %s AND user_id = %s", (id, str(interaction.user.id)))
    row = cur.fetchone()
    if not row:
        await interaction.response.send_message("Contenu non trouvé ou vous n'êtes pas le propriétaire de ce contenu.", ephemeral=True)
        cur.close()
        conn.close()
        return

    cur.execute("UPDATE contents SET rating = %s WHERE id = %s AND user_id = %s", (note, id, str(interaction.user.id)))
    conn.commit()
    cur.close()
    conn.close()
    await interaction.response.send_message(f"Contenu noté **{note}/10** avec succès !", ephemeral=True)

# ===============================
# Démarrage du bot et du serveur web
# ===============================
@bot.event
async def on_ready():
    init_db()
    try:
        synced = await bot.tree.sync()
        print(f"Synchronisation réussie pour {len(synced)} commande(s).")
    except Exception as e:
        print(e)
    print(f"Bot connecté en tant que {bot.user}")

keep_alive()
bot.run(TOKEN)

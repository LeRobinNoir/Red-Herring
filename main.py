@bot.tree.command(name="liste", description="Afficher la liste de contenus d'un utilisateur (triée par type)")
async def liste(interaction: discord.Interaction, member: discord.Member = None):
    """
    Affiche la liste des contenus d'un utilisateur, triés et regroupés par type de contenu, avec un style plus épuré.
    """
    target = member or interaction.user
    conn = get_db_connection()
    cur = conn.cursor()
    # On trie d'abord par content_type, puis par title
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

    # Types connus dans un certain ordre
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

            # Construction d'une ligne épurée
            # ex : "- **Tower of God** 👀 (#2) | Note: 8/10"
            line = f"- **{title}** {STATUS_EMOJIS.get(status, '')} (# {entry_id})"
            if rating is not None:
                line += f" | Note: {rating}/10"
            contents_str += line + "\n"

        # Ajout d'un champ pour chaque type
        embed.add_field(
            name=f"{ctype} {TYPE_EMOJIS.get(ctype, '')}",
            value=contents_str,
            inline=False
        )

    await interaction.response.send_message(embed=embed)

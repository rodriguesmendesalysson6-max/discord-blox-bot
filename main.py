import discord
from discord import app_commands
from discord.ext import commands, tasks
import sqlite3
import os
import random
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Tuple
from dotenv import load_dotenv
import json

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

DATABASE = "bot.db"
MOEDA = "BC"
SALDO_INICIAL = 1000
DAILY_AMOUNT = 250
DAILY_COOLDOWN = 86400
TRABALHAR_MIN = 50
TRABALHAR_MAX = 150
APOSTA_MIN = 10
APOSTA_MAX = 10000

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_db()

    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        conn = self.get_connection()
        c = conn.cursor()

        c.execute('''
            CREATE TABLE IF NOT EXISTS usuarios (
                user_id INTEGER PRIMARY KEY,
                saldo INTEGER DEFAULT 1000,
                ultimo_daily INTEGER DEFAULT 0,
                ultimo_trabalho INTEGER DEFAULT 0,
                partidas INTEGER DEFAULT 0,
                vitórias INTEGER DEFAULT 0,
                derrotas INTEGER DEFAULT 0,
                total_ganho INTEGER DEFAULT 0,
                total_perdido INTEGER DEFAULT 0,
                maior_prêmio INTEGER DEFAULT 0,
                conquistas TEXT DEFAULT '[]'
            )
        ''')

        c.execute('''
            CREATE TABLE IF NOT EXISTS transacoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                tipo TEXT,
                quantidade INTEGER,
                timestamp INTEGER,
                descricao TEXT
            )
        ''')

        c.execute('''
            CREATE TABLE IF NOT EXISTS logs_admin (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER,
                acao TEXT,
                detalhes TEXT,
                timestamp INTEGER
            )
        ''')

        conn.commit()
        conn.close()

    def get_or_create_user(self, user_id: int):
        conn = self.get_connection()
        c = conn.cursor()
        
        c.execute("SELECT * FROM usuarios WHERE user_id = ?", (user_id,))
        user = c.fetchone()
        
        if not user:
            c.execute('''
                INSERT INTO usuarios (user_id, saldo, conquistas)
                VALUES (?, ?, ?)
            ''', (user_id, SALDO_INICIAL, json.dumps([])))
            conn.commit()
        
        conn.close()

    def get_saldo(self, user_id: int) -> int:
        self.get_or_create_user(user_id)
        conn = self.get_connection()
        c = conn.cursor()
        c.execute("SELECT saldo FROM usuarios WHERE user_id = ?", (user_id,))
        result = c.fetchone()
        conn.close()
        return result['saldo'] if result else SALDO_INICIAL

    def add_saldo(self, user_id: int, amount: int, tipo: str = "manual", descricao: str = ""):
        if amount == 0:
            return True
        
        self.get_or_create_user(user_id)
        conn = self.get_connection()
        c = conn.cursor()

        try:
            c.execute("BEGIN EXCLUSIVE")

            saldo_atual = self.get_saldo(user_id)
            novo_saldo = saldo_atual + amount

            if novo_saldo < 0:
                conn.rollback()
                conn.close()
                return False

            c.execute("UPDATE usuarios SET saldo = ? WHERE user_id = ?", (novo_saldo, user_id))

            if amount > 0:
                c.execute('''
                    UPDATE usuarios SET total_ganho = total_ganho + ? WHERE user_id = ?
                ''', (amount, user_id))
            else:
                c.execute('''
                    UPDATE usuarios SET total_perdido = total_perdido + ? WHERE user_id = ?
                ''', (abs(amount), user_id))

            c.execute('''
                INSERT INTO transacoes (user_id, tipo, quantidade, timestamp, descricao)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, tipo, amount, int(datetime.now().timestamp()), descricao))

            conn.commit()
            conn.close()
            return True
        except Exception as e:
            conn.rollback()
            conn.close()
            return False

    def transferir(self, user_id_origem: int, user_id_destino: int, amount: int) -> bool:
        if amount <= 0 or amount > APOSTA_MAX or amount < APOSTA_MIN:
            return False

        saldo = self.get_saldo(user_id_origem)
        if saldo < amount:
            return False

        self.get_or_create_user(user_id_destino)

        if not self.add_saldo(user_id_origem, -amount, "transferência", f"Transferência para {user_id_destino}"):
            return False

        if not self.add_saldo(user_id_destino, amount, "transferência", f"Transferência de {user_id_origem}"):
            self.add_saldo(user_id_origem, amount, "transferência", "Reversão de transferência")
            return False

        return True

    def get_daily_cooldown(self, user_id: int) -> int:
        self.get_or_create_user(user_id)
        conn = self.get_connection()
        c = conn.cursor()
        c.execute("SELECT ultimo_daily FROM usuarios WHERE user_id = ?", (user_id,))
        result = c.fetchone()
        conn.close()
        
        if not result:
            return 0
        
        tempo_desde = int(datetime.now().timestamp()) - result['ultimo_daily']
        if tempo_desde >= DAILY_COOLDOWN:
            return 0
        
        return DAILY_COOLDOWN - tempo_desde

    def usar_daily(self, user_id: int) -> bool:
        if self.get_daily_cooldown(user_id) > 0:
            return False

        self.get_or_create_user(user_id)
        conn = self.get_connection()
        c = conn.cursor()

        c.execute("UPDATE usuarios SET ultimo_daily = ? WHERE user_id = ?",
                  (int(datetime.now().timestamp()), user_id))
        conn.commit()
        conn.close()

        return self.add_saldo(user_id, DAILY_AMOUNT, "daily", "Prêmio daily")

    def get_trabalho_cooldown(self, user_id: int) -> int:
        self.get_or_create_user(user_id)
        conn = self.get_connection()
        c = conn.cursor()
        c.execute("SELECT ultimo_trabalho FROM usuarios WHERE user_id = ?", (user_id,))
        result = c.fetchone()
        conn.close()

        if not result or result['ultimo_trabalho'] == 0:
            return 0

        tempo_desde = int(datetime.now().timestamp()) - result['ultimo_trabalho']
        cooldown_trabalho = 3600
        
        if tempo_desde >= cooldown_trabalho:
            return 0

        return cooldown_trabalho - tempo_desde

    def usar_trabalho(self, user_id: int) -> int:
        if self.get_trabalho_cooldown(user_id) > 0:
            return 0

        self.get_or_create_user(user_id)
        ganho = random.randint(TRABALHAR_MIN, TRABALHAR_MAX)

        conn = self.get_connection()
        c = conn.cursor()
        c.execute("UPDATE usuarios SET ultimo_trabalho = ? WHERE user_id = ?",
                  (int(datetime.now().timestamp()), user_id))
        conn.commit()
        conn.close()

        self.add_saldo(user_id, ganho, "trabalho", "Prêmio por trabalhar")
        return ganho

    def add_partida(self, user_id: int, ganhou: bool, ganho: int = 0):
        self.get_or_create_user(user_id)
        conn = self.get_connection()
        c = conn.cursor()

        c.execute("UPDATE usuarios SET partidas = partidas + 1 WHERE user_id = ?", (user_id,))

        if ganhou:
            c.execute("UPDATE usuarios SET vitórias = vitórias + 1 WHERE user_id = ?", (user_id,))
        else:
            c.execute("UPDATE usuarios SET derrotas = derrotas + 1 WHERE user_id = ?", (user_id,))

        if ganho > 0:
            c.execute('''
                UPDATE usuarios SET maior_prêmio = MAX(maior_prêmio, ?) WHERE user_id = ?
            ''', (ganho, user_id))

        conn.commit()
        conn.close()

    def get_perfil(self, user_id: int) -> dict:
        self.get_or_create_user(user_id)
        conn = self.get_connection()
        c = conn.cursor()
        c.execute("SELECT * FROM usuarios WHERE user_id = ?", (user_id,))
        result = c.fetchone()
        conn.close()

        if not result:
            return {}

        return {
            'user_id': result['user_id'],
            'saldo': result['saldo'],
            'partidas': result['partidas'],
            'vitórias': result['vitórias'],
            'derrotas': result['derrotas'],
            'total_ganho': result['total_ganho'],
            'total_perdido': result['total_perdido'],
            'maior_prêmio': result['maior_prêmio'],
            'conquistas': json.loads(result['conquistas']) if result['conquistas'] else []
        }

    def get_ranking(self, limite: int = 10) -> list:
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('''
            SELECT user_id, saldo FROM usuarios
            ORDER BY saldo DESC
            LIMIT ?
        ''', (limite,))
        results = c.fetchall()
        conn.close()
        return [{'user_id': r['user_id'], 'saldo': r['saldo']} for r in results]

    def add_conquista(self, user_id: int, conquista_id: str, nome: str, emoji: str):
        self.get_or_create_user(user_id)
        conn = self.get_connection()
        c = conn.cursor()

        c.execute("SELECT conquistas FROM usuarios WHERE user_id = ?", (user_id,))
        result = c.fetchone()
        conquistas = json.loads(result['conquistas']) if result['conquistas'] else []

        if not any(c['id'] == conquista_id for c in conquistas):
            conquistas.append({
                'id': conquista_id,
                'nome': nome,
                'emoji': emoji,
                'data': int(datetime.now().timestamp())
            })
            c.execute("UPDATE usuarios SET conquistas = ? WHERE user_id = ?",
                      (json.dumps(conquistas), user_id))
            conn.commit()
            conn.close()
            return True

        conn.close()
        return False

    def resetar_usuario(self, user_id: int):
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('''
            UPDATE usuarios
            SET saldo = ?, ultimo_daily = 0, ultimo_trabalho = 0,
                partidas = 0, vitórias = 0, derrotas = 0,
                total_ganho = 0, total_perdido = 0, maior_prêmio = 0,
                conquistas = ?
            WHERE user_id = ?
        ''', (SALDO_INICIAL, json.dumps([]), user_id))
        conn.commit()
        conn.close()

    def resetar_economia(self):
        conn = self.get_connection()
        c = conn.cursor()
        c.execute("DELETE FROM usuarios")
        c.execute("DELETE FROM transacoes")
        conn.commit()
        conn.close()
        self.init_db()

    def log_admin(self, admin_id: int, acao: str, detalhes: str):
        conn = self.get_connection()
        c = conn.cursor()
        c.execute('''
            INSERT INTO logs_admin (admin_id, acao, detalhes, timestamp)
            VALUES (?, ?, ?, ?)
        ''', (admin_id, acao, detalhes, int(datetime.now().timestamp())))
        conn.commit()
        conn.close()

db = Database(DATABASE)

def criar_embed_erro(titulo: str, descricao: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"❌ {titulo}",
        description=descricao,
        color=discord.Color.red()
    )
    return embed

def criar_embed_sucesso(titulo: str, descricao: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"✅ {titulo}",
        description=descricao,
        color=discord.Color.green()
    )
    return embed

def criar_embed_info(titulo: str, descricao: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"ℹ️ {titulo}",
        description=descricao,
        color=discord.Color.blue()
    )
    return embed

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ Bot {bot.user} está online")
    print(f"🔗 Convide o bot: https://discord.com/api/oauth2/authorize?client_id={bot.user.id}&permissions=268435456&scope=bot%20applications.commands")

@bot.tree.command(name="saldo", description="Mostra seu saldo em Blox Coins")
async def saldo(interaction: discord.Interaction):
    user_id = interaction.user.id
    saldo_user = db.get_saldo(user_id)

    embed = discord.Embed(
        title=f"💰 Saldo de {interaction.user.name}",
        description=f"**{saldo_user:,} {MOEDA}**",
        color=discord.Color.gold()
    )
    embed.set_thumbnail(url=interaction.user.avatar.url)

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="daily", description="Receba 250 BC uma vez a cada 24 horas")
async def daily(interaction: discord.Interaction):
    user_id = interaction.user.id
    cooldown = db.get_daily_cooldown(user_id)

    if cooldown > 0:
        horas = cooldown // 3600
        minutos = (cooldown % 3600) // 60
        embed = criar_embed_erro(
            "Daily em Cooldown",
            f"Você poderá usar daily novamente em **{horas}h {minutos}m**"
        )
        await interaction.response.send_message(embed=embed)
        return

    if db.usar_daily(user_id):
        embed = criar_embed_sucesso(
            "Daily Coletado!",
            f"Você ganhou **{DAILY_AMOUNT:,} {MOEDA}**! 🎁\n\nVolte amanhã para mais!"
        )

        db.add_conquista(user_id, "primeiro_daily", "Primeiro Daily", "🎁")

        await interaction.response.send_message(embed=embed)
    else:
        embed = criar_embed_erro("Erro", "Não foi possível coletar o daily.")
        await interaction.response.send_message(embed=embed)

@bot.tree.command(name="trabalhar", description="Trabalhe e ganhe BC aleatoriamente (1h cooldown)")
async def trabalhar(interaction: discord.Interaction):
    user_id = interaction.user.id
    cooldown = db.get_trabalho_cooldown(user_id)

    if cooldown > 0:
        minutos = cooldown // 60
        embed = criar_embed_erro(
            "Trabalho em Cooldown",
            f"Você poderá trabalhar novamente em **{minutos} minutos**"
        )
        await interaction.response.send_message(embed=embed)
        return

    ganho = db.usar_trabalho(user_id)

    if ganho > 0:
        embed = criar_embed_sucesso(
            "Trabalho Realizado!",
            f"Você ganhou **{ganho:,} {MOEDA}** pelo seu trabalho! 💼"
        )
        await interaction.response.send_message(embed=embed)
    else:
        embed = criar_embed_erro("Erro", "Não foi possível registrar seu trabalho.")
        await interaction.response.send_message(embed=embed)

@bot.tree.command(name="transferir", description="Transfira BC para outro usuário")
@app_commands.describe(
    usuario="Usuário para transferir",
    quantidade="Quantidade de BC"
)
async def transferir(interaction: discord.Interaction, usuario: discord.User, quantidade: int):
    user_id = interaction.user.id
    destino_id = usuario.id

    if usuario.bot:
        embed = criar_embed_erro("Erro", "Você não pode transferir para bots!")
        await interaction.response.send_message(embed=embed)
        return

    if user_id == destino_id:
        embed = criar_embed_erro("Erro", "Você não pode transferir para si mesmo!")
        await interaction.response.send_message(embed=embed)
        return

    if quantidade < APOSTA_MIN or quantidade > APOSTA_MAX:
        embed = criar_embed_erro(
            "Valor Inválido",
            f"A transferência deve ser entre **{APOSTA_MIN:,}** e **{APOSTA_MAX:,} {MOEDA}**"
        )
        await interaction.response.send_message(embed=embed)
        return

    saldo = db.get_saldo(user_id)
    if saldo < quantidade:
        embed = criar_embed_erro(
            "Saldo Insuficiente",
            f"Você possui apenas **{saldo:,} {MOEDA}**"
        )
        await interaction.response.send_message(embed=embed)
        return

    if db.transferir(user_id, destino_id, quantidade):
        embed = criar_embed_sucesso(
            "Transferência Realizada!",
            f"Você transferiu **{quantidade:,} {MOEDA}** para {usuario.mention}! 💸"
        )
        await interaction.response.send_message(embed=embed)
    else:
        embed = criar_embed_erro("Erro", "Falha ao realizar transferência.")
        await interaction.response.send_message(embed=embed)

@bot.tree.command(name="perfil", description="Veja seu perfil e estatísticas")
@app_commands.describe(usuario="Usuário (deixe em branco para você mesmo)")
async def perfil(interaction: discord.Interaction, usuario: Optional[discord.User] = None):
    target = usuario if usuario else interaction.user
    user_id = target.id

    perfil_data = db.get_perfil(user_id)

    if not perfil_data:
        embed = criar_embed_erro("Erro", "Usuário não encontrado no banco de dados.")
        await interaction.response.send_message(embed=embed)
        return

    taxa_vitoria = (
        (perfil_data['vitórias'] / perfil_data['partidas'] * 100)
        if perfil_data['partidas'] > 0 else 0
    )

    conquistas_text = ""
    if perfil_data['conquistas']:
        conquistas_text = " ".join(
            [f"{c['emoji']} {c['nome']}" for c in perfil_data['conquistas']]
        )
    else:
        conquistas_text = "Sem conquistas ainda"

    embed = discord.Embed(
        title=f"👤 Perfil de {target.name}",
        color=discord.Color.blurple()
    )
    embed.set_thumbnail(url=target.avatar.url)
    embed.add_field(
        name="💰 Saldo",
        value=f"**{perfil_data['saldo']:,} {MOEDA}**",
        inline=False
    )
    embed.add_field(
        name="🎮 Estatísticas de Jogos",
        value=f"Partidas: **{perfil_data['partidas']}**\n"
              f"Vitórias: **{perfil_data['vitórias']}**\n"
              f"Derrotas: **{perfil_data['derrotas']}**\n"
              f"Taxa de Vitória: **{taxa_vitoria:.1f}%**",
        inline=False
    )
    embed.add_field(
        name="💎 Ganhos e Perdas",
        value=f"Total Ganho: **+{perfil_data['total_ganho']:,} {MOEDA}**\n"
              f"Total Perdido: **-{perfil_data['total_perdido']:,} {MOEDA}**\n"
              f"Maior Prêmio: **{perfil_data['maior_prêmio']:,} {MOEDA}**",
        inline=False
    )
    embed.add_field(
        name="🏆 Conquistas",
        value=conquistas_text,
        inline=False
    )

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="ranking", description="Veja os 10 usuários mais ricos")
async def ranking(interaction: discord.Interaction):
    ranking_data = db.get_ranking(10)

    if not ranking_data:
        embed = criar_embed_info("Ranking", "Nenhum usuário encontrado ainda")
        await interaction.response.send_message(embed=embed)
        return

    embed = discord.Embed(
        title="👑 Top 10 Mais Ricos",
        color=discord.Color.gold()
    )

    texto = ""
    for idx, entry in enumerate(ranking_data, 1):
        try:
            user = await bot.fetch_user(entry['user_id'])
            nome = user.name
        except:
            nome = f"ID: {entry['user_id']}"

        medalha = "🥇" if idx == 1 else "🥈" if idx == 2 else "🥉" if idx == 3 else f"{idx}."
        texto += f"{medalha} **{nome}** - {entry['saldo']:,} {MOEDA}\n"

    embed.description = texto
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="caraoucoroa", description="Aposte e escolha: Cara ou Coroa")
@app_commands.describe(
    quantidade="Quantidade de BC para apostar",
    escolha="Sua escolha: Cara ou Coroa"
)
async def caraoucoroa(interaction: discord.Interaction, quantidade: int, escolha: str):
    user_id = interaction.user.id

    if escolha.lower() not in ["cara", "coroa"]:
        embed = criar_embed_erro("Erro", "Escolha deve ser 'Cara' ou 'Coroa'")
        await interaction.response.send_message(embed=embed)
        return

    if quantidade < APOSTA_MIN or quantidade > APOSTA_MAX:
        embed = criar_embed_erro(
            "Aposta Inválida",
            f"A aposta deve ser entre **{APOSTA_MIN:,}** e **{APOSTA_MAX:,} {MOEDA}**"
        )
        await interaction.response.send_message(embed=embed)
        return

    saldo = db.get_saldo(user_id)
    if saldo < quantidade:
        embed = criar_embed_erro(
            "Saldo Insuficiente",
            f"Você possui apenas **{saldo:,} {MOEDA}**"
        )
        await interaction.response.send_message(embed=embed)
        return

    resultado = random.choice(["Cara", "Coroa"])
    ganhou = resultado.lower() == escolha.lower()

    if ganhou:
        premio = quantidade
        db.add_saldo(user_id, premio, "jogo", f"Vitória em Cara ou Coroa")
        db.add_partida(user_id, True, premio)

        embed = discord.Embed(
            title="🎉 Você Venceu!",
            color=discord.Color.green()
        )
        embed.add_field(name="Resultado", value=f"**{resultado}**", inline=False)
        embed.add_field(name="Aposta", value=f"**{quantidade:,} {MOEDA}**", inline=True)
        embed.add_field(name="Prêmio", value=f"**+{premio:,} {MOEDA}**", inline=True)
    else:
        db.add_saldo(user_id, -quantidade, "jogo", f"Derrota em Cara ou Coroa")
        db.add_partida(user_id, False)

        embed = discord.Embed(
            title="😢 Você Perdeu!",
            color=discord.Color.red()
        )
        embed.add_field(name="Resultado", value=f"**{resultado}**", inline=False)
        embed.add_field(name="Sua Escolha", value=f"**{escolha.capitalize()}**", inline=True)
        embed.add_field(name="Perda", value=f"**-{quantidade:,} {MOEDA}**", inline=True)

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="dados", description="Jogue um dado contra a casa")
@app_commands.describe(quantidade="Quantidade de BC para apostar")
async def dados(interaction: discord.Interaction, quantidade: int):
    user_id = interaction.user.id

    if quantidade < APOSTA_MIN or quantidade > APOSTA_MAX:
        embed = criar_embed_erro(
            "Aposta Inválida",
            f"A aposta deve ser entre **{APOSTA_MIN:,}** e **{APOSTA_MAX:,} {MOEDA}**"
        )
        await interaction.response.send_message(embed=embed)
        return

    saldo = db.get_saldo(user_id)
    if saldo < quantidade:
        embed = criar_embed_erro(
            "Saldo Insuficiente",
            f"Você possui apenas **{saldo:,} {MOEDA}**"
        )
        await interaction.response.send_message(embed=embed)
        return

    dado_jogador = random.randint(1, 6)
    dado_casa = random.randint(1, 6)

    if dado_jogador > dado_casa:
        premio = quantidade
        db.add_saldo(user_id, premio, "jogo", "Vitória em Dados")
        db.add_partida(user_id, True, premio)

        embed = discord.Embed(
            title="🎰 Você Venceu!",
            color=discord.Color.green()
        )
        embed.add_field(name="Seu Dado", value=f"**{dado_jogador}** 🎲", inline=True)
        embed.add_field(name="Dado da Casa", value=f"**{dado_casa}** 🎲", inline=True)
        embed.add_field(name="Prêmio", value=f"**+{premio:,} {MOEDA}**", inline=False)

    elif dado_jogador < dado_casa:
        db.add_saldo(user_id, -quantidade, "jogo", "Derrota em Dados")
        db.add_partida(user_id, False)

        embed = discord.Embed(
            title="😢 Você Perdeu!",
            color=discord.Color.red()
        )
        embed.add_field(name="Seu Dado", value=f"**{dado_jogador}** 🎲", inline=True)
        embed.add_field(name="Dado da Casa", value=f"**{dado_casa}** 🎲", inline=True)
        embed.add_field(name="Perda", value=f"**-{quantidade:,} {MOEDA}**", inline=False)

    else:
        embed = discord.Embed(
            title="🤝 Empate!",
            color=discord.Color.gold()
        )
        embed.add_field(name="Seu Dado", value=f"**{dado_jogador}** 🎲", inline=True)
        embed.add_field(name="Dado da Casa", value=f"**{dado_casa}** 🎲", inline=True)
        embed.add_field(name="Resultado", value="Sua aposta foi devolvida!", inline=False)

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="roleta", description="Aposte em Vermelho, Preto ou Verde")
@app_commands.describe(
    quantidade="Quantidade de BC para apostar",
    escolha="Cor: Vermelho, Preto ou Verde"
)
async def roleta(interaction: discord.Interaction, quantidade: int, escolha: str):
    user_id = interaction.user.id

    cores_validas = ["vermelho", "preto", "verde"]
    if escolha.lower() not in cores_validas:
        embed = criar_embed_erro(
            "Cor Inválida",
            "Escolha deve ser: Vermelho, Preto ou Verde"
        )
        await interaction.response.send_message(embed=embed)
        return

    if quantidade < APOSTA_MIN or quantidade > APOSTA_MAX:
        embed = criar_embed_erro(
            "Aposta Inválida",
            f"A aposta deve ser entre **{APOSTA_MIN:,}** e **{APOSTA_MAX:,} {MOEDA}**"
        )
        await interaction.response.send_message(embed=embed)
        return

    saldo = db.get_saldo(user_id)
    if saldo < quantidade:
        embed = criar_embed_erro(
            "Saldo Insuficiente",
            f"Você possui apenas **{saldo:,} {MOEDA}**"
        )
        await interaction.response.send_message(embed=embed)
        return

    rand = random.randint(1, 100)

    if rand <= 45:
        resultado_cor = "Vermelho"
        multiplicador = 2
    elif rand <= 90:
        resultado_cor = "Preto"
        multiplicador = 2
    else:
        resultado_cor = "Verde"
        multiplicador = 14

    ganhou = resultado_cor.lower() == escolha.lower()

    if ganhou:
        premio = quantidade * multiplicador
        db.add_saldo(user_id, premio, "jogo", "Vitória em Roleta")
        db.add_partida(user_id, True, premio)

        db.add_conquista(user_id, "sortudo", "Sortudo", "🍀")

        embed = discord.Embed(
            title="🎡 Você Venceu!",
            color=discord.Color.green()
        )
        embed.add_field(name="Resultado", value=f"**{resultado_cor}** 🎯", inline=False)
        embed.add_field(name="Aposta", value=f"**{quantidade:,} {MOEDA}**", inline=True)
        embed.add_field(name="Prêmio", value=f"**+{premio:,} {MOEDA}** (x{multiplicador})", inline=True)

    else:
        db.add_saldo(user_id, -quantidade, "jogo", "Derrota em Roleta")
        db.add_partida(user_id, False)

        db.add_conquista(user_id, "azarado", "Azarado", "💀")

        embed = discord.Embed(
            title="😢 Você Perdeu!",
            color=discord.Color.red()
        )
        embed.add_field(name="Resultado", value=f"**{resultado_cor}** 🎯", inline=False)
        embed.add_field(name="Sua Escolha", value=f"**{escolha.capitalize()}**", inline=True)
        embed.add_field(name="Perda", value=f"**-{quantidade:,} {MOEDA}**", inline=True)

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="loja", description="Veja os itens disponíveis na loja")
async def loja(interaction: discord.Interaction):
    itens = [
        {"id": "titulo_rico", "nome": "Título: Rico", "preco": 5000, "descricao": "Mostre que você é rico!", "emoji": "💰"},
        {"id": "titulo_sortudo", "nome": "Título: Sortudo", "preco": 3000, "descricao": "Você tem muita sorte!", "emoji": "🍀"},
        {"id": "cargo_vip", "nome": "Cargo VIP", "preco": 10000, "descricao": "Um cargo especial do servidor", "emoji": "👑"},
    ]

    embed = discord.Embed(
        title="🏪 Loja Virtual",
        description="Use `/comprar <item_id>` para comprar itens",
        color=discord.Color.purple()
    )

    for item in itens:
        embed.add_field(
            name=f"{item['emoji']} {item['nome']}",
            value=f"{item['descricao']}\n💵 **{item['preco']:,} {MOEDA}**",
            inline=False
        )

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="comprar", description="Compre um item da loja")
@app_commands.describe(item_id="ID do item a comprar")
async def comprar(interaction: discord.Interaction, item_id: str):
    user_id = interaction.user.id

    itens = {
        "titulo_rico": {"nome": "Título: Rico", "preco": 5000, "emoji": "💰"},
        "titulo_sortudo": {"nome": "Título: Sortudo", "preco": 3000, "emoji": "🍀"},
        "cargo_vip": {"nome": "Cargo VIP", "preco": 10000, "emoji": "👑"},
    }

    if item_id not in itens:
        embed = criar_embed_erro("Erro", "Item não encontrado na loja!")
        await interaction.response.send_message(embed=embed)
        return

    item = itens[item_id]
    saldo = db.get_saldo(user_id)

    if saldo < item['preco']:
        embed = criar_embed_erro(
            "Saldo Insuficiente",
            f"Você precisa de **{item['preco'] - saldo:,} {MOEDA}** a mais"
        )
        await interaction.response.send_message(embed=embed)
        return

    if db.add_saldo(user_id, -item['preco'], "compra", f"Compra: {item['nome']}"):
        embed = criar_embed_sucesso(
            "Compra Realizada!",
            f"Você comprou **{item['emoji']} {item['nome']}** por **{item['preco']:,} {MOEDA}**"
        )
        await interaction.response.send_message(embed=embed)
    else:
        embed = criar_embed_erro("Erro", "Falha ao realizar compra.")
        await interaction.response.send_message(embed=embed)

@bot.tree.command(name="addsaldo", description="[ADMIN] Adicionar saldo a um usuário")
@app_commands.describe(usuario="Usuário", quantidade="Quantidade de BC")
async def addsaldo(interaction: discord.Interaction, usuario: discord.User, quantidade: int):
    if not interaction.user.guild_permissions.administrator:
        embed = criar_embed_erro("Permissão Negada", "Apenas administradores podem usar esse comando")
        await interaction.response.send_message(embed=embed)
        return

    if quantidade <= 0:
        embed = criar_embed_erro("Erro", "A quantidade deve ser maior que zero")
        await interaction.response.send_message(embed=embed)
        return

    db.add_saldo(usuario.id, quantidade, "admin", f"Adicionado por {interaction.user.name}")
    db.log_admin(interaction.user.id, "add_saldo", f"Adicionou {quantidade} BC para {usuario.id}")

    embed = criar_embed_sucesso(
        "Saldo Adicionado",
        f"**{quantidade:,} {MOEDA}** adicionado para {usuario.mention}"
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="removersaldo", description="[ADMIN] Remover saldo de um usuário")
@app_commands.describe(usuario="Usuário", quantidade="Quantidade de BC")
async def removersaldo(interaction: discord.Interaction, usuario: discord.User, quantidade: int):
    if not interaction.user.guild_permissions.administrator:
        embed = criar_embed_erro("Permissão Negada", "Apenas administradores podem usar esse comando")
        await interaction.response.send_message(embed=embed)
        return

    if quantidade <= 0:
        embed = criar_embed_erro("Erro", "A quantidade deve ser maior que zero")
        await interaction.response.send_message(embed=embed)
        return

    saldo = db.get_saldo(usuario.id)
    if saldo < quantidade:
        embed = criar_embed_erro(
            "Saldo Insuficiente",
            f"O usuário possui apenas **{saldo:,} {MOEDA}**"
        )
        await interaction.response.send_message(embed=embed)
        return

    db.add_saldo(usuario.id, -quantidade, "admin", f"Removido por {interaction.user.name}")
    db.log_admin(interaction.user.id, "remove_saldo", f"Removeu {quantidade} BC de {usuario.id}")

    embed = criar_embed_sucesso(
        "Saldo Removido",
        f"**{quantidade:,} {MOEDA}** removido de {usuario.mention}"
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="setsaldo", description="[ADMIN] Definir saldo de um usuário")
@app_commands.describe(usuario="Usuário", quantidade="Novo saldo em BC")
async def setsaldo(interaction: discord.Interaction, usuario: discord.User, quantidade: int):
    if not interaction.user.guild_permissions.administrator:
        embed = criar_embed_erro("Permissão Negada", "Apenas administradores podem usar esse comando")
        await interaction.response.send_message(embed=embed)
        return

    if quantidade < 0:
        embed = criar_embed_erro("Erro", "O saldo não pode ser negativo")
        await interaction.response.send_message(embed=embed)
        return

    saldo_atual = db.get_saldo(usuario.id)
    diferenca = quantidade - saldo_atual

    if diferenca > 0:
        db.add_saldo(usuario.id, diferenca, "admin", f"Set por {interaction.user.name}")
    elif diferenca < 0:
        db.add_saldo(usuario.id, diferenca, "admin", f"Set por {interaction.user.name}")

    db.log_admin(interaction.user.id, "set_saldo", f"Definiu saldo de {usuario.id} para {quantidade}")

    embed = criar_embed_sucesso(
        "Saldo Definido",
        f"Saldo de {usuario.mention} definido para **{quantidade:,} {MOEDA}**"
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="resetar_usuario", description="[ADMIN] Resetar economia de um usuário")
@app_commands.describe(usuario="Usuário a resetar")
async def resetar_usuario_cmd(interaction: discord.Interaction, usuario: discord.User):
    if not interaction.user.guild_permissions.administrator:
        embed = criar_embed_erro("Permissão Negada", "Apenas administradores podem usar esse comando")
        await interaction.response.send_message(embed=embed)
        return

    embed = discord.Embed(
        title="⚠️ Confirmação",
        description=f"Tem certeza que quer resetar a economia de {usuario.mention}?\n\n"
                    f"Isso não pode ser desfeito!",
        color=discord.Color.orange()
    )

    view = ConfirmacaoView(interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view)

    await view.wait()

    if view.confirmado:
        db.resetar_usuario(usuario.id)
        db.log_admin(interaction.user.id, "resetar_usuario", f"Resetou economia de {usuario.id}")

        embed = criar_embed_sucesso(
            "Usuário Resetado",
            f"Economia de {usuario.mention} foi resetada para 1000 BC"
        )
        await interaction.followup.send(embed=embed)
    else:
        embed = criar_embed_info("Cancelado", "Operação cancelada.")
        await interaction.followup.send(embed=embed)

@bot.tree.command(name="resetar_economia", description="[ADMIN] Resetar TODA a economia (AÇÃO DESTRUTIVA)")
async def resetar_economia_cmd(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        embed = criar_embed_erro("Permissão Negada", "Apenas administradores podem usar esse comando")
        await interaction.response.send_message(embed=embed)
        return

    embed = discord.Embed(
        title="🚨 AÇÃO DESTRUTIVA - Confirmação Necessária",
        description="Você está prestes a **RESETAR TODA A ECONOMIA** do servidor!\n\n"
                    "Isto irá:\n"
                    "❌ Deletar TODOS os dados de usuários\n"
                    "❌ Resetar TODOS os saldos\n"
                    "❌ Apagar TODAS as transações\n\n"
                    "**Isto NÃO pode ser desfeito!**",
        color=discord.Color.red()
    )

    view = ConfirmacaoView(interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view)

    await view.wait()

    if view.confirmado:
        db.resetar_economia()
        db.log_admin(interaction.user.id, "resetar_economia", "Resetou TODA a economia")

        embed = discord.Embed(
            title="🔄 Economia Resetada",
            description="Toda a economia foi resetada com sucesso!",
            color=discord.Color.green()
        )
        await interaction.followup.send(embed=embed)
    else:
        embed = criar_embed_info("Cancelado", "Operação cancelada.")
        await interaction.followup.send(embed=embed)

class ConfirmacaoView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.confirmado = False

    @discord.ui.button(label="✅ Confirmar", style=discord.ButtonStyle.red)
    async def confirmar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.defer()
            return

        self.confirmado = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="❌ Cancelar", style=discord.ButtonStyle.grey)
    async def cancelar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.defer()
            return

        self.confirmado = False
        self.stop()
        await interaction.response.defer()

if __name__ == "__main__":
    bot.run(TOKEN)

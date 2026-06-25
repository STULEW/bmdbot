import os
import re
import discord
import requests
import feedparser
from discord import app_commands
from discord.ext import commands, tasks
from discord.ui import Modal, TextInput
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()
TOKEN = os.getenv("TOKEN")
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID"))
WAR_CHANNEL_ID = int(os.getenv("WAR_CHANNEL_ID"))
TAG_CHANNEL_ID = int(os.getenv("TAG_CHANNEL_ID"))
GUILD_ID = int(os.getenv("GUILD_ID"))
MEDIA_ROLE_ID = int(os.getenv("MEDIA_ROLE_ID"))
MEDIA_CHANNEL_ID = int(os.getenv("MEDIA_CHANNEL_ID"))

CLAN_TAG = "ცოშ" 

# База данных в памяти (В реальном проекте лучше использовать файлы/БД)
trolled_users = set()
# Словарь для мониторинга: { "channel_id": "последнее_видео_id" }
monitored_channels = {}

# Функция для получения реального Channel ID из любой ссылки на YouTube
def get_youtube_channel_id(url):
    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        # Ищем тег channelId в исходном коде страницы
        match = re.search(r'meta itemprop="channelId" content="([^"]+)"', response.text)
        if match:
            return match.group(1)
        # Если ссылка уже имеет вид /channel/UC...
        match_direct = re.search(r'youtube\.com/channel/([^/?#&]+)', url)
        if match_direct:
            return match_direct.group(1)
    except Exception as e:
        print(f"Ошибка при парсинге YouTube URL: {e}")
    return None

class ClanBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)

    async def on_ready(self):
        print(f"Бот {self.user.name} успешно запущен!")
        try:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            print(f"Синхронизировано команд для сервера: {len(synced)}")
        except Exception as e:
            print(f"Ошибка синхронизации команд: {e}")

        # Запуск циклической проверки YouTube
        if not self.check_youtube_videos.is_running():
            self.check_youtube_videos.start()

        # Проверка канала клан-тега на дубликаты
        tag_channel = self.get_channel(TAG_CHANNEL_ID)
        if tag_channel:
            already_sent = False
            try:
                async for message in tag_channel.history(limit=20):
                    if message.author.id == self.user.id and message.embeds:
                        if message.embeds[0].title == "✔️ ОФИЦИАЛЬНЫЙ ТЕГ КЛАНА ✔️":
                            already_sent = True
                            break
            except Exception as e:
                print(f"Ошибка истории: {e}")

            if not already_sent:
                embed = discord.Embed(title="✔️ ОФИЦИАЛЬНЫЙ ТЕГ КЛАНА ✔️", description=f"Приветствуем участников клана DDNet!\nИспользуйте этот тег в настройках игры:\n\n**` {CLAN_TAG} `**", color=discord.Color.blue())
                embed.set_footer(text="Бот клана активен")
                await tag_channel.send(embed=embed)

        log_channel = self.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            await log_channel.send("🤖 **Бот успешно перезапущен и готов к работе.**")

    async def on_message(self, message):
        if message.author.bot or message.guild is None:
            return
        if message.author.id in trolled_users:
            try:
                await message.delete()
                webhooks = await message.channel.webhooks()
                webhook = discord.utils.get(webhooks, name="ClanTrollWebhook")
                if not webhook:
                    webhook = await message.channel.create_webhook(name="ClanTrollWebhook")
                await webhook.send(content="Я глупость", username=message.author.display_name, avatar_url=message.author.display_avatar.url)
            except Exception as e:
                print(f"Ошибка троллинга: {e}")
        await self.process_commands(message)

    # Фоновая задача: проверяет новые видео каждые 10 минут
    @tasks.loop(minutes=10)
    async def check_youtube_videos(self):
        media_channel = self.get_channel(MEDIA_CHANNEL_ID)
        if not media_channel:
            return

        for yt_id, last_video_id in list(monitored_channels.items()):
            # YouTube предоставляет официальный бесплатный RSS фид для каждого канала
            rss_url = f"https://youtube.com{yt_id}"
            feed = feedparser.parse(rss_url)
            
            if not feed.entries:
                continue
                
            latest_entry = feed.entries[0]
            video_id = latest_entry.yt_videoid
            video_url = latest_entry.link
            video_title = latest_entry.title

            # Если мы только добавили канал, просто запоминаем последнее видео, чтобы не спамить старьем
            if last_video_id is None:
                monitored_channels[yt_id] = video_id
                continue

            # Если появилось новое видео, которого мы еще не видели
            if video_id != last_video_id:
                monitored_channels[yt_id] = video_id
                # Публикуем анонс в канал медиа
                await media_channel.send(f"🎬 **Новое видео на канале!**\nВышел ролик: *{video_title}*\n{video_url}")

bot = ClanBot()

# НОВАЯ КОМАНДА /channel (Опечатка /chennal исправлена для удобства)
@bot.tree.command(name="channel", description="Привязать свой YouTube-канал и получить роль Media")
@app_commands.describe(link="Ссылка на ваш YouTube канал (например, https://youtube.com)")
async def channel(interaction: discord.Interaction, link: str):
    # Фильтруем базовую проверку ссылки
    if "youtube.com" not in link and "youtu.be" not in link:
        await interaction.followup.send("❌ Не удалось найти ID YouTube канала. Проверьте правильность ссылки.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True) # Даем боту время подумать (запрос в интернет)

    yt_channel_id = get_youtube_channel_id(link)
    if not yt_channel_id:
        await interaction.followup.send("❌ Не удалось найти ID YouTube канала. Проверьте правильность ссылки.", ephemeral=True)
        return

    # Выдаем роль Media пользователю в Discord
    guild = interaction.guild
    role = guild.get_role(MEDIA_ROLE_ID)
    
    if role:
        try:
            await interaction.user.add_roles(role)
            
            # Добавляем YouTube-канал в систему отслеживания, если его там еще нет
            if yt_channel_id not in monitored_channels:
                monitored_channels[yt_channel_id] = None # Бот запомнит текущее видео при следующей проверке

            await interaction.followup.send(f"✅ Успешно! Вам выдана роль **{role.name}**. Бот теперь следит за вашим каналом и опубликует новые ролики!", ephemeral=True)
            
            # Пишем в логи
            log_channel = interaction.client.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                await log_channel.send(f"📸 {interaction.user.mention} привязал YouTube канал (`{yt_channel_id}`) и получил роль Media.")
        except Exception as e:
            await interaction.followup.send(f"❌ Не удалось выдать роль. Проверьте, что роль бота находится ВЫШЕ роли Media в настройках сервера.", ephemeral=True)
    else:
        await interaction.followup.send("❌ Ошибка: Роль Media не найдена на сервере. Проверьте MEDIA_ROLE_ID в файле .env.", ephemeral=True)


# Команда /war
class WarModal(Modal, title="Регистрация нового Клан-Вара ☠"):
    target_nick = TextInput(label="Никнейм нарушителя / Клана", placeholder="Введите ник...", required=True, max_length=100)
    reason = TextInput(label="Причина объявления вара", placeholder="Опишите ситуацию...", style=discord.TextStyle.long, required=True, max_length=1000)

    async def on_submit(self, interaction: discord.Interaction):
        war_channel = interaction.client.get_channel(WAR_CHANNEL_ID)
        if war_channel:
            embed = discord.Embed(title="☠️ ОБЪЯВЛЕН НОВЫЙ КЛАН-ВАР! ☠️", color=discord.Color.red())
            embed.add_field(name="👤 Цель / Нарушитель:", value=f"`{self.target_nick.value}`", inline=False)
            embed.add_field(name="📝 Причина:", value=self.reason.value, inline=False)
            embed.add_field(name="👑 Инициатор:", value=interaction.user.mention, inline=True)
            embed.set_footer(text=f"Дата фиксации: {interaction.created_at.strftime('%d.%m.%Y')}")
            await war_channel.send(embed=embed)
            await interaction.response.send_message("✅ Вар успешно зарегистрирован!", ephemeral=True)

@bot.tree.command(name="war", description="Открыть форму для объявления нового клан-вара")
async def war(interaction: discord.Interaction):
    await interaction.response.send_modal(WarModal())


# Команда /troll
@bot.tree.command(name="troll", description="Заменить все сообщения пользователя на 'Я глупость' (Только для Админов)")
@app_commands.describe(member="Выберите пользователя для троллинга")
@app_commands.checks.has_permissions(administrator=True)
async def troll(interaction: discord.Interaction, member: discord.Member):
    if member.id == interaction.user.id:
        await interaction.response.send_message("❌ Вы не можете затроллить самого себя!", ephemeral=True)
        return

    if member.id in trolled_users:
        trolled_users.remove(member.id)
        await interaction.response.send_message(f"😇 Пользователь {member.mention} помилован.", ephemeral=True)
        log_channel = interaction.client.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            await log_channel.send(f"😇 Администратор {interaction.user.mention} снял эффект троллинга с {member.mention}.")
    else:
        trolled_users.add(member.id)
        await interaction.response.send_message(f"😈 Пользователь {member.mention} отправлен в режим глупости!", ephemeral=True)
        
        log_channel = interaction.client.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            await log_channel.send(f"😈 Администратор {interaction.user.mention} включил троллинг для {member.mention}.")

# Обработчик ошибок для команды /troll
@troll.error
async def troll_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.errors.MissingPermissions):
        await interaction.response.send_message("❌ У вас нет прав Администратора для использования этой команды!", ephemeral=True)

# Запуск бота
bot.run(TOKEN)

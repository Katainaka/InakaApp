import discord
from discord.ext import commands, tasks
from discord.ui import View, Button
import dateparser
import datetime
import sqlite3
import os
import pytz
from dotenv import load_dotenv
from flask import Flask
from threading import Thread

def parse_relative_time(time_str):
    """Парсим относительное время вроде '10m', '2h', '3d'"""
    time_str = time_str.lower()
    now = datetime.datetime.now(pytz.utc)
    try:
        if time_str.endswith('h') and time_str[:-1].isdigit():
            return now + datetime.timedelta(hours=int(time_str[:-1]))
        elif time_str.endswith('m') and time_str[:-1].isdigit():
            return now + datetime.timedelta(minutes=int(time_str[:-1]))
        elif time_str.endswith('d') and time_str[:-1].isdigit():
            return now + datetime.timedelta(days=int(time_str[:-1]))
    except:
        return None
    return None

# Загрузка токена из .env
load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="rm ", intents=intents, help_command=None)

moscow_tz = pytz.timezone("Europe/Moscow")

# Инициализация БД
def init_db():
    with sqlite3.connect('reminders.db') as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                channel_id TEXT,
                task TEXT,
                remind_time TEXT,
                repeat_interval TEXT
            )
        ''')
        conn.commit()
init_db()

def format_time_left(remind_time):
    now = datetime.datetime.now(pytz.utc)
    delta = remind_time - now
    if delta.total_seconds() <= 0:
        return "⚠️ Прошло"
    days, seconds = divmod(delta.total_seconds(), 86400)
    hours, remainder = divmod(seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{int(days)}д {int(hours)}ч {int(minutes)}м"

@bot.event
async def on_ready():
    print(f'✅ Бот {bot.user} запущен!')
    check_reminders.start()

@bot.command(name="add")
async def add(ctx, *, args: str):
    # Автоудаление команды пользователя через 10 секунд
    try:
        await ctx.message.delete(delay=10)
    except:
        pass

    # Разбиваем строку на текст задачи и время
    words = args.strip().split()
    remind_time = None
    task_text = args

    # Ищем время в конце строки
    for i in range(len(words), 0, -1):
        time_candidate = " ".join(words[i-1:])
        remind_time = parse_relative_time(time_candidate) or dateparser.parse(
            time_candidate,
            settings={'TIMEZONE': '+0300', 'RETURN_AS_TIMEZONE_AWARE': True, 'PREFER_DATES_FROM': 'future'}
        )
        if remind_time:
            task_text = " ".join(words[:i-1])
            break

    if not remind_time or remind_time < datetime.datetime.now(tz=pytz.utc):
        await ctx.send("❌ Не удалось определить будущее время.", delete_after=10)
        return

    if not task_text.strip():
        task_text = "Без названия"

    with sqlite3.connect('reminders.db') as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO reminders (user_id, channel_id, task, remind_time, repeat_interval) VALUES (?, ?, ?, ?, ?)",
            (str(ctx.author.id), str(ctx.channel.id), task_text.strip(), remind_time.isoformat(), None)
        )
        conn.commit()
    await ctx.message.add_reaction("✅")

@bot.command(name="list")
async def list_tasks(ctx):
    user_id = str(ctx.author.id)
    with sqlite3.connect('reminders.db') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, task, remind_time, repeat_interval FROM reminders WHERE user_id=?", (user_id,))
        tasks = cursor.fetchall()

    if not tasks:
        embed = discord.Embed(title="📭 Нет задач", description="У вас нет активных задач.", color=discord.Color.orange())
        await ctx.send(embed=embed, delete_after=60)
        return

    class TaskView(View):
        def __init__(self, user_tasks):
            super().__init__(timeout=60)
            self.user_tasks = user_tasks
            self.current_page = 0
            self.page_size = 5
            self.update_buttons()

        def update_buttons(self):
            self.clear_items()
            start_idx = self.current_page * self.page_size
            end_idx = start_idx + self.page_size
            page_tasks = self.user_tasks[start_idx:end_idx]

            for idx, (task_id, task, remind_time_str, repeat) in enumerate(page_tasks, start=1):
                remind_time = datetime.datetime.fromisoformat(remind_time_str)
                timestamp = int(remind_time.timestamp())
                repeat_text = f"🔄 {repeat}" if repeat else ""
                button_label = f"❌ Удалить {idx}"
                btn = Button(label=button_label, style=discord.ButtonStyle.red)

                async def remove_callback(interaction, t_id=task_id):
                    with sqlite3.connect('reminders.db') as conn:
                        cursor = conn.cursor()
                        cursor.execute("DELETE FROM reminders WHERE id=?", (t_id,))
                        conn.commit()
                    await interaction.response.send_message(f"🗑 Задача удалена.", ephemeral=True)
                    self.user_tasks = [t for t in self.user_tasks if t[0] != t_id]
                    if not self.user_tasks:
                        await interaction.message.edit(embed=discord.Embed(title="📭 Нет задач", color=discord.Color.orange()), view=None)
                    else:
                        await self.update_message(interaction.message)

                btn.callback = remove_callback
                self.add_item(btn)

            if len(self.user_tasks) > self.page_size:
                prev_btn = Button(label="⬅️", disabled=self.current_page == 0)
                next_btn = Button(label="➡️", disabled=(end_idx >= len(self.user_tasks)))

                async def prev_callback(interaction):
                    self.current_page -= 1
                    await self.update_message(interaction.message)

                async def next_callback(interaction):
                    self.current_page += 1
                    await self.update_message(interaction.message)

                prev_btn.callback = prev_callback
                next_btn.callback = next_callback
                self.add_item(prev_btn)
                self.add_item(next_btn)

        async def update_message(self, message):
            start_idx = self.current_page * self.page_size
            end_idx = start_idx + self.page_size
            page_tasks = self.user_tasks[start_idx:end_idx]

            embed = discord.Embed(
                title=f"📋 Ваши задачи — Страница {self.current_page+1}/{(len(self.user_tasks)-1)//self.page_size+1}",
                color=discord.Color.blue()
            )
            for idx, (tid, task, remind_time_str, repeat) in enumerate(page_tasks, start=1):
                remind_time = datetime.datetime.fromisoformat(remind_time_str)
                timestamp = int(remind_time.timestamp())
                repeat_text = f"🔄 {repeat}" if repeat else ""
                embed.add_field(name=f"{idx + start_idx}. {task}",
                                value=f"<t:{timestamp}:R> {repeat_text}", inline=False)
            self.update_buttons()
            await message.edit(embed=embed, view=self)

    view = TaskView(tasks)

# Создаём временный Embed с первой страницей
start_embed = discord.Embed(
    title="📋 Ваши задачи — Страница 1",
    color=discord.Color.blue()
)
for idx, (tid, task, remind_time_str, repeat) in enumerate(tasks[:view.page_size], start=1):
    remind_time = datetime.datetime.fromisoformat(remind_time_str)
    timestamp = int(remind_time.timestamp())
    repeat_text = f"🔄 {repeat}" if repeat else ""
    start_embed.add_field(
        name=f"{idx}. {task}",
        value=f"<t:{timestamp}:R> {repeat_text}", inline=False
    )

# Отправляем сообщение с первой страницей и кнопками
msg = await ctx.send(embed=start_embed, view=view, delete_after=60)

# Обновляем View и кнопки
await view.update_message(msg)


@tasks.loop(seconds=10)
async def check_reminders():
    now = datetime.datetime.now(tz=pytz.utc)
    with sqlite3.connect('reminders.db') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, user_id, channel_id, task, repeat_interval FROM reminders WHERE remind_time <= ?", (now.isoformat(),))
        rows = cursor.fetchall()

    for rid, user_id, channel_id, task, repeat in rows:
        channel = bot.get_channel(int(channel_id))
        if channel:
            try:
                embed = discord.Embed(title="⏰ Напоминание!", description=f"{task}", color=discord.Color.gold())
                await channel.send(f"<@{user_id}>", embed=embed)
            except Exception as e:
                print(f"⚠️ Ошибка отправки: {e}")
        with sqlite3.connect('reminders.db') as conn:
            cursor = conn.cursor()
            if repeat == "daily":
                next_time = now + datetime.timedelta(days=1)
                cursor.execute("UPDATE reminders SET remind_time=? WHERE id=?", (next_time.isoformat(), rid))
            elif repeat == "hourly":
                next_time = now + datetime.timedelta(hours=1)
                cursor.execute("UPDATE reminders SET remind_time=? WHERE id=?", (next_time.isoformat(), rid))
            else:
                cursor.execute("DELETE FROM reminders WHERE id=?", (rid,))
            conn.commit()

app = Flask(__name__)
@app.route("/")
def index(): return "Бот работает!"
def run(): app.run(host='0.0.0.0', port=8080)
def keep_alive(): Thread(target=run).start()

keep_alive()
bot.run(os.getenv("DISCORD_TOKEN"))

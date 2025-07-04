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
    time_str = time_str.lower()
    now = datetime.datetime.now(pytz.utc)
    try:
        if 'h' in time_str and time_str.replace('h', '').isdigit():
            return now + datetime.timedelta(hours=int(time_str.replace('h', '')))
        elif 'm' in time_str and time_str.replace('m', '').isdigit():
            return now + datetime.timedelta(minutes=int(time_str.replace('m', '')))
        elif 'd' in time_str and time_str.replace('d', '').isdigit():
            return now + datetime.timedelta(days=int(time_str.replace('d', '')))
    except:
        return None
    return None

# Загрузка токена из .env файла
load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
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
    parts = args.rsplit(' ', 1)
    if len(parts) != 2:
        await ctx.send("❌ Использование: `rm add <задача> <время>`", delete_after=10)
        return
    task_text, time_str = parts
    remind_time = parse_relative_time(time_str) or dateparser.parse(
        time_str,
        settings={'TIMEZONE': '+0300', 'RETURN_AS_TIMEZONE_AWARE': True}
    )
    if not remind_time or remind_time < datetime.datetime.now(tz=pytz.utc):
        await ctx.send("❌ Не удалось определить будущее время.", delete_after=10)
        return

    with sqlite3.connect('reminders.db') as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO reminders (user_id, channel_id, task, remind_time, repeat_interval) VALUES (?, ?, ?, ?, ?)",
            (str(ctx.author.id), str(ctx.channel.id), task_text, remind_time.isoformat(), None)
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

    PAGE_SIZE = 5
    pages = [tasks[i:i + PAGE_SIZE] for i in range(0, len(tasks), PAGE_SIZE)]
    current_page = 0

    async def send_page(page_num):
        nonlocal current_page
        current_page = page_num
        embed = discord.Embed(title=f"📋 Ваши задачи — Страница {page_num+1}/{len(pages)}", color=discord.Color.blue())
        for idx, (tid, task, remind_time_str, repeat) in enumerate(pages[page_num], start=1):
            remind_time = datetime.datetime.fromisoformat(remind_time_str)
            timestamp = int(remind_time.timestamp())
            repeat_text = f"🔄 {repeat}" if repeat else ""
            embed.add_field(name=f"{idx + page_num*PAGE_SIZE}. {task}",
                            value=f"<t:{timestamp}:R> {repeat_text}", inline=False)

        view = View(timeout=60)
        if len(pages) > 1:
            prev_button = Button(label="⬅️", disabled=page_num == 0)
            next_button = Button(label="➡️", disabled=page_num == len(pages) - 1)

            async def prev_callback(interaction): await send_page(page_num - 1)
            async def next_callback(interaction): await send_page(page_num + 1)

            prev_button.callback = prev_callback
            next_button.callback = next_callback
            view.add_item(prev_button)
            view.add_item(next_button)

        msg = await ctx.send(embed=embed, view=view)
        await msg.delete(delay=60)

    await send_page(0)

@tasks.loop(seconds=10)
async def check_reminders():
    now = datetime.datetime.now(tz=pytz.utc)
    with sqlite3.connect('reminders.db') as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, user_id, channel_id, task, repeat_interval FROM reminders WHERE remind_time <= ?",
            (now.isoformat(),)
        )
        rows = cursor.fetchall()

    for rid, user_id, channel_id, task, repeat in rows:
        channel = bot.get_channel(int(channel_id))
        if channel:
            try:
                embed = discord.Embed(title="⏰ Напоминание!", description=f"<@{user_id}> — {task}", color=discord.Color.gold())
                await channel.send(f"<@{user_id}>", embed=embed)
            except Exception as e:
                print(f"⚠️ Ошибка при отправке: {e}")

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

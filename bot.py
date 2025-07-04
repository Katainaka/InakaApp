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
            hours = int(time_str.replace('h', ''))
            return now + datetime.timedelta(hours=hours)
        elif 'm' in time_str and time_str.replace('m', '').isdigit():
            minutes = int(time_str.replace('m', ''))
            return now + datetime.timedelta(minutes=minutes)
        elif 'd' in time_str and time_str.replace('d', '').isdigit():
            days = int(time_str.replace('d', ''))
            return now + datetime.timedelta(days=days)
        else:
            return None
    except:
        return None


# Загрузка токена из .env файла
load_dotenv()

# Настройки бота
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.messages = True
bot = commands.Bot(command_prefix="rm ", intents=intents, help_command=None)


# Подключение к базе данных
conn = sqlite3.connect('reminders.db')
cursor = conn.cursor()

# Создаем таблицу, если её нет + поля для повторений и канала
cursor.execute('''
    CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        channel_id TEXT,
        task TEXT,
        remind_time TEXT,
        repeat_interval TEXT  -- hourly, daily
    )
''')
conn.commit()


# Московский часовой пояс
moscow_tz = pytz.timezone("Europe/Moscow")


def format_time_left(remind_time):
    now = datetime.datetime.now(pytz.utc)
    delta = remind_time - now

    seconds = int(delta.total_seconds())
    if seconds <= 0:
        return "⚠️ Прошло"

    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    days, hours = divmod(hours, 24)

    parts = []
    if days > 0:
        parts.append(f"{days}д")
    if hours > 0:
        parts.append(f"{hours}ч")
    if minutes > 0:
        parts.append(f"{minutes}м")

    return " ".join(parts) or "меньше минуты"


@bot.event
async def on_ready():
    print(f'✅ Бот {bot.user} запущен и готов к работе!')
    check_reminders.start()


@bot.command(name="add")
async def add(ctx, name: str, *, time_str: str):
    remind_time = parse_relative_time(time_str)

    if not remind_time:
        remind_time = dateparser.parse(time_str, settings={'TIMEZONE': '+0300', 'RETURN_AS_TIMEZONE_AWARE': True})

    if not remind_time:
        await ctx.message.add_reaction("❌")
        return

    user_id = str(ctx.author.id)
    channel_id = str(ctx.channel.id)

    cursor.execute(
        "INSERT INTO reminders (user_id, channel_id, task, remind_time, repeat_interval) VALUES (?, ?, ?, ?, ?)",
        (user_id, channel_id, name, remind_time.isoformat(), None)
    )
    conn.commit()

    await ctx.message.add_reaction("✅")


@bot.command(name="list")
async def list_tasks(ctx):
    user_id = str(ctx.author.id)
    cursor.execute("SELECT id, task, remind_time, repeat_interval FROM reminders WHERE user_id=?", (user_id,))
    tasks = cursor.fetchall()

    if not tasks:
        embed = discord.Embed(
            title="📭 Нет задач",
            description="У вас нет активных задач.",
            color=discord.Color.orange()
        )
        msg = await ctx.send(embed=embed)
        await msg.delete(delay=60)
        return

    # Разбиваем задачи на страницы по 5 штук
    PAGE_SIZE = 5
    pages = [tasks[i:i + PAGE_SIZE] for i in range(0, len(tasks), PAGE_SIZE)]
    current_page = 0

    async def update_message(page_num):
        nonlocal current_page
        current_page = page_num

        embed = discord.Embed(
            title=f"📋 Ваши задачи — Страница {current_page + 1}/{len(pages)}",
            color=discord.Color.blue()
        )

        for index, (tid, task, remind_time_str, repeat) in enumerate(pages[page_num], start=1):
            remind_time = datetime.datetime.fromisoformat(remind_time_str)
            timestamp = int(remind_time.timestamp())
            repeat_text = f"🔄 {repeat}" if repeat else ""

            embed.add_field(
                name=f"{index + page_num * PAGE_SIZE}. {task}",
                value=f"<t:{timestamp}:R> {repeat_text}",
                inline=False
            )

        view = View(timeout=None)

        if len(pages) > 1:
            prev_button = Button(label="⬅️", style=discord.ButtonStyle.gray, disabled=current_page == 0)
            next_button = Button(label="➡️", style=discord.ButtonStyle.gray, disabled=current_page == len(pages) - 1)

            async def prev_callback(interaction: discord.Interaction):
                await update_message(current_page - 1)

            async def next_callback(interaction: discord.Interaction):
                await update_message(current_page + 1)

            prev_button.callback = prev_callback
            next_button.callback = next_callback
            view.add_item(prev_button)
            view.add_item(next_button)

        # Отправляем сообщение
        msg = await ctx.send(embed=embed, view=view)
        await msg.delete(delay=60)  # Удаление через 60 секунд
        await ctx.message.delete(delay=10)
    await update_message(0)


@bot.command(name="remove", aliases=["del"])
async def remove_task(ctx, position: int):
    user_id = str(ctx.author.id)
    cursor.execute("SELECT id FROM reminders WHERE user_id=? ORDER BY remind_time", (user_id,))
    all_ids = [row[0] for row in cursor.fetchall()]

    if position < 1 or position > len(all_ids):
        await ctx.message.add_reaction("❌")
        return

    task_id_to_delete = all_ids[position - 1]
    cursor.execute("DELETE FROM reminders WHERE id=?", (task_id_to_delete,))
    conn.commit()

    if cursor.rowcount > 0:
        await ctx.message.add_reaction("✅")
    else:
        await ctx.message.add_reaction("❌")


@bot.command(name="repeat")
async def set_repeat(ctx, position: int, interval: str):
    valid_intervals = ['daily', 'hourly']
    if interval not in valid_intervals:
        await ctx.message.add_reaction("❌")
        return

    user_id = str(ctx.author.id)
    cursor.execute("SELECT id FROM reminders WHERE user_id=? ORDER BY remind_time", (user_id,))
    all_ids = [row[0] for row in cursor.fetchall()]

    if position < 1 or position > len(all_ids):
        await ctx.message.add_reaction("❌")
        return

    task_id = all_ids[position - 1]
    cursor.execute("UPDATE reminders SET repeat_interval=? WHERE id=?", (interval, task_id))
    conn.commit()

    await ctx.message.add_reaction("✅")


@tasks.loop(seconds=1)
async def check_reminders():
    now = datetime.datetime.now(tz=pytz.utc)
    cursor.execute(
        "SELECT id, user_id, channel_id, task, repeat_interval FROM reminders WHERE remind_time <= ?",
        (now.isoformat(),)
    )
    rows = cursor.fetchall()

    for row in rows:
        rid, user_id, channel_id, task, repeat = row
        try:
            channel = bot.get_channel(int(channel_id))
            if channel:
                embed = discord.Embed(
                    title="⏰ Напоминание!",
                    description=f"<@{user_id}> — {task}",
                    color=discord.Color.gold()
                )
                await channel.send(embed=embed)
        except Exception as e:
            print(f"Ошибка при отправке напоминания пользователю {user_id}: {e}")

        if repeat == "daily":
            new_time = now + datetime.timedelta(days=1)
        elif repeat == "hourly":
            new_time = now + datetime.timedelta(hours=1)
        else:
            cursor.execute("DELETE FROM reminders WHERE id=?", (rid,))
            conn.commit()
            continue

        cursor.execute("UPDATE reminders SET remind_time=? WHERE id=?", (new_time.isoformat(), rid))
        conn.commit()
        
@bot.command(name="help")
async def help_command(ctx):
    embed = discord.Embed(
        title="📘 Список команд",
        description="Вот все доступные вам команды:",
        color=discord.Color.purple()
    )

    embed.add_field(
        name="`rm add <задача> <время>`",
        value="Добавляет напоминание. Пример: `rm add Задача завтра в 3:00`",
        inline=False
    )

    embed.add_field(
        name="`rm list`",
        value="Показывает список всех ваших задач с таймерами.",
        inline=False
    )

    embed.add_field(
        name="`rm del <номер>`",
        value="Удаляет задачу по номеру из списка. Пример: `rm del 2`",
        inline=False
    )

    embed.add_field(
        name="`rm repeat <номер> <интервал>`",
        value="Делает задачу повторяющейся. Интервалы: `daily`, `hourly`. Пример: `rm repeat 1 daily`",
        inline=False
    )

    embed.add_field(
        name="Примеры времён",
        value="`через 10 минут`, `tomorrow at 14:00`, `в пятницу`, `2h`, `3d`",
        inline=False
    )

  
    embed.set_thumbnail(url=bot.user.avatar.url)

    await ctx.send(embed=embed)


app = Flask(__name__)

@app.route("/")
def index():
    return "Бот работает!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

keep_alive()
# 🔑 Запуск бота
bot.run(os.getenv("DISCORD_TOKEN"))  # или bot.run("ВАШ_ТОКЕН")

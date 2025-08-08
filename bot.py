import os
import sqlite3
import asyncio
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = '/app/data/reminders.db'  # путь к базе данных внутри volume

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                text TEXT,
                remind_at TEXT
            )
        ''')
        conn.commit()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Используй /add <время> <текст> для добавления напоминания.")

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        time_str = context.args[0]
        text = ' '.join(context.args[1:])
        remind_at = datetime.now() + timedelta(minutes=int(time_str))
        
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('INSERT INTO reminders (user_id, text, remind_at) VALUES (?, ?, ?)', (
                update.effective_user.id,
                text,
                remind_at.isoformat()
            ))
            conn.commit()

        await update.message.reply_text(f"Напоминание добавлено на {remind_at.strftime('%H:%M:%S')}: {text}")
    except (IndexError, ValueError):
        await update.message.reply_text("Неправильный формат. Используй: /add <минуты> <текст>")

async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id, text, remind_at FROM reminders WHERE user_id = ?', (update.effective_user.id,))
        rows = cursor.fetchall()

    if not rows:
        await update.message.reply_text("У вас нет активных напоминаний.")
    else:
        response = "\n".join([f"{row[0]}: {row[1]} (в {row[2]})" for row in rows])
        await update.message.reply_text(response)

async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        reminder_id = int(context.args[0])
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM reminders WHERE id = ? AND user_id = ?', (
                reminder_id,
                update.effective_user.id
            ))
            conn.commit()
        await update.message.reply_text(f"Напоминание {reminder_id} удалено.")
    except (IndexError, ValueError):
        await update.message.reply_text("Используй: /delete <id>")

async def check_reminders(application):
    while True:
        now = datetime.now()
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id, user_id, text FROM reminders WHERE remind_at <= ?', (now.isoformat(),))
            reminders = cursor.fetchall()

            for reminder in reminders:
                user_id = reminder[1]
                text = reminder[2]
                await application.bot.send_message(chat_id=user_id, text=f"🔔 Напоминание: {text}")
                cursor.execute('DELETE FROM reminders WHERE id = ?', (reminder[0],))

            conn.commit()
        await asyncio.sleep(60)

async def main():
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("list", list_tasks))
    app.add_handler(CommandHandler("delete", delete))

    # Запуск фона
    app.job_queue.run_repeating(lambda _: asyncio.create_task(check_reminders(app)), interval=60, first=0)

    await app.run_polling()

if __name__ == '__main__':
    asyncio.run(main())

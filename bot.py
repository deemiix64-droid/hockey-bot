import os
import asyncio
import logging
import sqlite3
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp import web

# --- НАСТРОЙКИ ---
TOKEN = os.getenv("BOT_TOKEN")
ADMINS = [8239542728]  # <-- ЗАМЕНИ ЭТО НА СВОЙ ID (например [55667788])

bot = Bot(token=TOKEN)
dp = Dispatcher()
logging.basicConfig(level=logging.INFO)

# --- БАЗА ДАННЫХ ---
def db_query(query, params=(), fetch=False):
    conn = sqlite3.connect('hockey_elo.db')
    cur = conn.cursor()
    cur.execute(query, params)
    res = cur.fetchall() if fetch else None
    conn.commit()
    conn.close()
    return res

def init_db():
    db_query('''CREATE TABLE IF NOT EXISTS users 
                (id INTEGER PRIMARY KEY, username TEXT, elo INTEGER DEFAULT 1000)''')

# --- КЛАВИАТУРЫ ---
def get_main_kb(user_id):
    buttons = [
        [KeyboardButton(text="🏆 Топ 10"), KeyboardButton(text="🌟 Топ 100")],
        [KeyboardButton(text="📊 Мой рейтинг"), KeyboardButton(text="👥 Сотрудники")]
    ]
    # Если зашел админ, добавляем кнопку Админ-панели
    if user_id in ADMINS:
        buttons.append([KeyboardButton(text="⚙️ Админ Панель")])
    
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_admin_inline():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📈 Изменить Эло (инструкция)", callback_url="https://t.me/your_bot")],
        [InlineKeyboardButton(text="📊 Всего игроков", callback_data="admin_stats")],
        [InlineKeyboardButton(text="🔄 Сброс ТОПа (опасно)", callback_data="admin_reset")]
    ])
    return kb

# --- ХЕНДЛЕРЫ ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    db_query("INSERT OR IGNORE INTO users (id, username, elo) VALUES (?, ?, ?)", (user_id, username, 1000))
    await message.answer(f"🏒 Привет, {username}! Бот запущен.", reply_markup=get_main_kb(user_id))

@dp.message(F.text == "⚙️ Админ Панель")
async def admin_menu(message: types.Message):
    if message.from_user.id not in ADMINS:
        return
    await message.answer("🛠 **Панель управления администратора**\n\nЧтобы изменить Эло игроку, ответь на его сообщение командой:\n`/setelo 5000`", 
                         reply_markup=get_admin_inline(), parse_mode="Markdown")

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: types.CallbackQuery):
    count = db_query("SELECT COUNT(*) FROM users", fetch=True)[0][0]
    await callback.answer(f"Всего игроков в базе: {count}", show_alert=True)

@dp.message(F.text == "🏆 Топ 10")
async def show_top10(message: types.Message):
    users = db_query("SELECT username, elo FROM users ORDER BY elo DESC LIMIT 10", fetch=True)
    res = "🏆 **Топ 10 игроков:**\n\n"
    for i, u in enumerate(users):
        res += f"{i+1}. {u[0]} — `{u[1]}`\n"
    await message.answer(res, parse_mode="Markdown")

@dp.message(F.text == "📊 Мой рейтинг")
async def my_rating(message: types.Message):
    user = db_query("SELECT elo FROM users WHERE id = ?", (message.from_user.id,), fetch=True)
    await message.answer(f"👤 Твой рейтинг: **{user[0][0]} эло**", parse_mode="Markdown")

@dp.message(Command("setelo"))
async def set_elo_cmd(message: types.Message):
    if message.from_user.id not in ADMINS: return
    if not message.reply_to_message:
        return await message.answer("⚠️ Ответь на сообщение игрока!")
    try:
        val = int(message.text.split()[1])
        db_query("UPDATE users SET elo = ? WHERE id = ?", (val, message.reply_to_message.from_user.id))
        await message.answer(f"✅ Установлено {val} эло.")
    except:
        await message.answer("❌ Формат: /setelo 5000")

# --- СЕРВЕРНАЯ ЧАСТЬ (RENDER) ---
async def handle_ping(request):
    return web.Response(text="Alive")

async def start_webhook():
    app = web.Application(); app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app); await runner.setup()
    port = int(os.getenv("PORT", 8080))
    await web.TCPSite(runner, "0.0.0.0", port).start()

async def main():
    init_db()
    asyncio.create_task(start_webhook())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

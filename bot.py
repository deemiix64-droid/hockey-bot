import os
import asyncio
import sqlite3
import logging
import time
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiohttp import web

# --- НАСТРОЙКИ ---
TOKEN = os.getenv("BOT_TOKEN")
ADMINS = [8239542728] # Твой ID

bot = Bot(token=TOKEN)
dp = Dispatcher()
logging.basicConfig(level=logging.INFO)

# --- СОСТОЯНИЯ ---
class RequestScore(StatesGroup):
    waiting_for_data = State()
    waiting_for_photo = State()

class UpdateScore(StatesGroup):
    waiting_for_elo = State()
    waiting_for_photo = State()

class BroadcastState(StatesGroup):
    waiting_for_msg = State()

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
    db_query('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, elo INTEGER)''')
    db_query('''CREATE TABLE IF NOT EXISTS all_users (user_id INTEGER PRIMARY KEY)''')
    db_query('''CREATE TABLE IF NOT EXISTS banned (user_id INTEGER PRIMARY KEY)''')
    db_query('''CREATE TABLE IF NOT EXISTS staff_activity (user_id INTEGER PRIMARY KEY, last_seen INTEGER, elo INTEGER DEFAULT 4002)''')

def get_time_ago(seconds):
    diff = int(time.time()) - seconds
    if diff < 60: return "только что"
    elif diff < 3600: return f"{diff // 60} мин. назад"
    else: return f"{diff // 3600} ч. назад"

# --- КЛАВИАТУРЫ ---
def get_main_kb(user_id):
    buttons = [
        [KeyboardButton(text="➕ Добавить аккаунт"), KeyboardButton(text="🔄 Обновить эло")],
        [KeyboardButton(text="🏆 Топ 10"), KeyboardButton(text="🌟 Топ 100")],
        [KeyboardButton(text="📊 Мой рейтинг"), KeyboardButton(text="👥 Сотрудники")]
    ]
    if user_id in ADMINS:
        buttons.append([KeyboardButton(text="⚙️ Админ Панель")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# --- ЛОГИКА ОНЛАЙНА ---
@dp.message()
async def track_activity(message: types.Message):
    if message.from_user.id in ADMINS:
        db_query("INSERT OR REPLACE INTO staff_activity (user_id, last_seen, elo) VALUES (?, ?, (SELECT elo FROM staff_activity WHERE user_id = ?))", 
                 (message.from_user.id, int(time.time()), message.from_user.id))

# --- АДМИН-КОМАНДЫ ---

@dp.message(Command("myelo"))
async def set_my_elo(message: types.Message):
    if message.from_user.id not in ADMINS: return
    val = message.text.split()[1]
    db_query("UPDATE staff_activity SET elo = ? WHERE user_id = ?", (val, message.from_user.id))
    await message.answer(f"✅ Твое Эло изменено на {val}")

@dp.message(Command("del"))
async def delete_player(message: types.Message):
    if message.from_user.id not in ADMINS: return
    name = message.text.split()[1]
    db_query("DELETE FROM users WHERE username = ?", (name,))
    await message.answer(f"🗑 Игрок {name} удален из топа.")

# --- СТАТИСТИКА В ПАНЕЛИ ---
@dp.callback_query(F.data == "stats")
async def show_stats(call: types.CallbackQuery):
    total_users = db_query("SELECT COUNT(*) FROM all_users", fetch=True)[0][0]
    players_in_top = db_query("SELECT COUNT(*) FROM users", fetch=True)[0][0]
    banned = db_query("SELECT COUNT(*) FROM banned", fetch=True)[0][0]
    
    text = (
        "📊 **Статистика бота**\n\n"
        f"👥 Всего пользователей: **{total_users}**\n"
        f"🏒 Игроков в ТОПе: **{players_in_top}**\n"
        f"🚫 В бане: **{banned}**\n\n"
        "📈 Бот работает стабильно!"
    )
    await call.message.answer(text, parse_mode="Markdown")
    await call.answer()

# --- КНОПКА СОТРУДНИКИ ---
@dp.message(F.text == "👥 Сотрудники")
async def staff(message: types.Message):
    res = db_query("SELECT last_seen, elo FROM staff_activity WHERE user_id = ?", (8239542728,), fetch=True)
    online = get_time_ago(res[0][0]) if res else "давно"
    elo = res[0][1] if res else 4002
    await message.answer(f"👥 **Наша команда**\n\n👑 **orbsi** ({elo} эло)\n└ Владелец\n🕒 Активен: {online}", parse_mode="Markdown")

# --- ВСЁ ОСТАЛЬНОЕ (ЗАПУСК) ---
@dp.message(CommandStart())
async def start(message: types.Message):
    db_query("INSERT OR IGNORE INTO all_users (user_id) VALUES (?)", (message.from_user.id,))
    await message.answer("🏒 Привет! Я бот Три Кота Хоккей Эло.", reply_markup=get_main_kb(message.from_user.id))

@dp.message(F.text == "⚙️ Админ Панель")
async def adm(message: types.Message):
    if message.from_user.id not in ADMINS: return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="broadcast")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="stats")]
    ])
    await message.answer("🛠 Админ-панель:", reply_markup=kb)

# (Остальной код заявок и сортировки остается как в прошлом шаге)
# ... [Код заявок, одобрения и запуска сервера] ...

async def handle_ping(r): return web.Response(text="ok")
async def main():
    init_db()
    app = web.Application(); app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8080))).start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

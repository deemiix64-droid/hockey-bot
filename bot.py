import os
import asyncio
import sqlite3
import logging
import time
import shutil
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import CommandStart, Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiohttp import web

# --- 1. ЛОГИРОВАНИЕ ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(message)s",
    handlers=[logging.FileHandler("hockey_final.log"), logging.StreamHandler()]
)
logger = logging.getLogger("HockeyCore")

# --- 2. НАСТРОЙКИ ---
TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = 8239542728 

bot = Bot(token=TOKEN)
dp = Dispatcher()

WELCOME_MSG = (
    "🏒 *Добро пожаловать в Лидерборд Три Кота Хоккей\\!*\n\n"
    "Следи за своим рейтингом и стань лучшим игроком\\!\n\n"
    "⚠️ *Отказ от ответственности:*\n"
    "Бот создан фанатами\\. Данные проверяются вручную\\."
)

class RegState(StatesGroup):
    data = State()
    photo = State()

class UpdState(StatesGroup):
    elo = State()
    photo = State()

# --- 4. БАЗА ДАННЫХ ---
class Database:
    def __init__(self, db_name='hockey_data.db'):
        self.db_name = db_name
        self._setup()

    def _setup(self):
        with sqlite3.connect(self.db_name) as conn:
            c = conn.cursor()
            c.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, elo INTEGER)")
            c.execute("CREATE TABLE IF NOT EXISTS stats_all (user_id INTEGER PRIMARY KEY)")
            c.execute("CREATE TABLE IF NOT EXISTS staff (user_id INTEGER PRIMARY KEY, name TEXT, role TEXT, elo INTEGER, last_seen INTEGER)")
            c.execute("CREATE TABLE IF NOT EXISTS queue (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, q_type TEXT, name TEXT, elo INTEGER, photo TEXT)")
            c.execute("INSERT OR IGNORE INTO staff (user_id, name, role, elo, last_seen) VALUES (?, ?, ?, ?, ?)", 
                      (OWNER_ID, "orbsi", "Владелец", 4002, int(time.time())))
            conn.commit()

    def call(self, sql, params=(), fetch=False):
        try:
            with sqlite3.connect(self.db_name) as conn:
                c = conn.cursor()
                c.execute(sql, params)
                res = c.fetchall() if fetch else None
                conn.commit()
                return res
        except Exception as e:
            logger.error(f"DB Error: {e}")
            return []

db = Database()

# --- 5. МЕНЮ ---
def get_main_menu(uid):
    is_staff = db.call("SELECT 1 FROM staff WHERE user_id = ?", (uid,), fetch=True)
    btns = [
        [KeyboardButton(text="➕ Добавить аккаунт"), KeyboardButton(text="🔄 Обновить эло")],
        [KeyboardButton(text="🏆 Топ 10"), KeyboardButton(text="🌟 Топ 100")],
        [KeyboardButton(text="📊 Мой рейтинг"), KeyboardButton(text="👥 Сотрудники")]
    ]
    if uid == OWNER_ID or is_staff:
        btns.append([KeyboardButton(text="⚙️ Admin Panel")])
    return ReplyKeyboardMarkup(keyboard=btns, resize_keyboard=True)

# --- 7. ОБРАБОТЧИКИ ---

@dp.message(CommandStart())
async def start_handler(msg: types.Message, state: FSMContext):
    await state.clear()
    db.call("INSERT OR IGNORE INTO stats_all (user_id) VALUES (?)", (msg.from_user.id,))
    await msg.answer(WELCOME_MSG, reply_markup=get_main_menu(msg.from_user.id), parse_mode="MarkdownV2")

@dp.message(F.text.contains("Сотрудники"))
async def staff_handler(msg: types.Message):
    data = db.call("SELECT name, role, elo, last_seen FROM staff", fetch=True)
    txt = "👥 *Наша команда:*\n\n"
    now = int(time.time())
    for s in data:
        status = "🟢 онлайн" if (now - (s[3] or 0)) < 300 else "⚪ оффлайн"
        txt += f"⚙️ *{s[0]}* — {s[1]}\n└ {status} | {s[2]} эло\n\n"
    await msg.answer(txt, parse_mode="MarkdownV2")

# (Пропускаем стандартные блоки регистрации для экономии места, они идентичны V17)
# ... [Регистрация и Обновление здесь] ...

# --- 9. АДМИН-ПАНЕЛЬ И УВЕДОМЛЕНИЯ ---

@dp.callback_query(F.data == "adm_view")
async def admin_view_queue(call: types.CallbackQuery):
    q = db.call("SELECT id, q_type, name, elo, photo FROM queue LIMIT 1", fetch=True)
    if not q: return await call.message.answer("📦 Заявок нет!")
    
    btns = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"y_{q[0][0]}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"n_{q[0][0]}")
    ]])
    
    cap = f"📦 Заявка #{q[0][0]} ({q[0][1]})\n👤 Ник: `{q[0][2]}`\n🏒 Эло: *{q[0][3]}*"
    try:
        await bot.send_photo(call.from_user.id, photo=q[0][4], caption=cap, reply_markup=btns, parse_mode="MarkdownV2")
    except:
        await bot.send_message(call.from_user.id, f"{cap}\n\n⚠️ Ошибка фото", reply_markup=btns)
    await call.answer()

@dp.callback_query(F.data.startswith(("y_", "n_")))
async def admin_decision(call: types.CallbackQuery):
    act, rid = call.data.split("_")
    # Получаем данные пользователя перед удалением из очереди
    item = db.call("SELECT user_id, q_type, name, elo FROM queue WHERE id = ?", (int(rid),), fetch=True)
    
    if item:
        user_id = item[0][0]
        q_type = item[0][1]
        
        if act == "y":
            # Сохраняем в ТОП
            db.call("INSERT OR REPLACE INTO users (user_id, username, elo) VALUES (?, ?, ?)", (user_id, item[0][2], item[0][3]))
            try:
                msg = "✅ Ваша заявка на регистрацию одобрена!" if q_type == "NEW" else "✅ Обновление Эло принято!"
                await bot.send_message(user_id, f"{msg}\nВаш Эло: {item[0][3]}")
            except: logger.error(f"Не удалось отправить уведомление юзеру {user_id}")
        
        else:
            # ОТКЛОНЕНИЕ
            try:
                msg = "❌ Ваша заявка на регистрацию отклонена." if q_type == "NEW" else "❌ Запрос на обновление Эло отклонен."
                await bot.send_message(user_id, f"{msg}\nПроверьте данные или скриншот.")
            except: logger.error(f"Не удалось отправить уведомление об отказе юзеру {user_id}")

    # Удаляем из очереди и обновляем админку
    db.call("DELETE FROM queue WHERE id = ?", (int(rid),))
    await call.message.delete()
    await admin_view_queue(call)

# --- 10. СТАРТ ---
async def main():
    # Настройка порта для хостинга
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Bot Active"))
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8080))).start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

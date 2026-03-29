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

# --- КОНФИГУРАЦИЯ И ЛОГИРОВАНИЕ ---
TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = 8239542728 

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(name)s: %(message)s",
    handlers=[logging.FileHandler("hockey_system.log"), logging.StreamHandler()]
)
logger = logging.getLogger("HockeyCore")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# ТЕКСТЫ ИНТЕРФЕЙСА
WELCOME_MSG = (
    "🏒 *Добро пожаловать в Лидерборд Три Кота Хоккей\\!*\n\n"
    "Следи за своим рейтингом и стань лучшим игроком\\!\n\n"
    "⚠️ *Отказ от ответственности:*\n"
    "Бот создан фанатами\\. Не связан с разработчиками игры\\."
)

# --- СОСТОЯНИЯ (FSM) ---
class RegState(StatesGroup):
    data = State()
    photo = State()

class UpdState(StatesGroup):
    elo = State()
    photo = State()

# --- СИСТЕМА УПРАВЛЕНИЯ ДАННЫМИ ---
class Database:
    def __init__(self, db_name='hockey_leader.db'):
        self.db_name = db_name
        self._init_db()
        self._backup_db()

    def _init_db(self):
        """Создание таблиц с проверкой целостности"""
        with sqlite3.connect(self.db_name) as conn:
            c = conn.cursor()
            c.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, elo INTEGER)")
            c.execute("CREATE TABLE IF NOT EXISTS stats_all (user_id INTEGER PRIMARY KEY)")
            c.execute("CREATE TABLE IF NOT EXISTS staff (user_id INTEGER PRIMARY KEY, name TEXT, role TEXT, elo INTEGER, last_seen INTEGER)")
            c.execute("CREATE TABLE IF NOT EXISTS queue (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, q_type TEXT, name TEXT, elo INTEGER, photo TEXT)")
            # Регистрация Владельца
            c.execute("INSERT OR IGNORE INTO staff (user_id, name, role, elo, last_seen) VALUES (?, ?, ?, ?, ?)", 
                      (OWNER_ID, "orbsi", "Владелец", 4002, int(time.time())))
            conn.commit()
            logger.info("Database initialized.")

    def _backup_db(self):
        """Создание резервной копии базы данных при запуске"""
        if os.path.exists(self.db_name):
            shutil.copy2(self.db_name, f"{self.db_name}.bak")
            logger.info("Backup created.")

    def execute(self, sql, params=(), fetch=False):
        """Метод выполнения SQL-транзакций с обработкой исключений"""
        try:
            with sqlite3.connect(self.db_name) as conn:
                c = conn.cursor()
                c.execute(sql, params)
                res = c.fetchall() if fetch else None
                conn.commit()
                return res
        except sqlite3.Error as e:
            logger.error(f"Database Error: {e}")
            return []

db = Database()

# --- MIDDLEWARE ДЛЯ МОНИТОРИНГА ---
class StaffMonitor(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user:
            db.execute("UPDATE staff SET last_seen = ? WHERE user_id = ?", (int(time.time()), user.id))
        return await handler(event, data)

dp.update.outer_middleware(StaffMonitor())

# --- КЛАВИАТУРЫ ---
def get_main_kb(uid):
    is_adm = db.execute("SELECT 1 FROM staff WHERE user_id = ?", (uid,), fetch=True)
    btns = [
        [KeyboardButton(text="➕ Добавить аккаунт"), KeyboardButton(text="🔄 Обновить эло")],
        [KeyboardButton(text="🏆 Топ 10"), KeyboardButton(text="🌟 Топ 100")],
        [KeyboardButton(text="📊 Мой рейтинг"), KeyboardButton(text="👥 Сотрудники")]
    ]
    if uid == OWNER_ID or is_adm:
        btns.append([KeyboardButton(text="⚙️ Admin Panel")])
    return ReplyKeyboardMarkup(keyboard=btns, resize_keyboard=True)

def get_cancel_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Отменить действие")]], resize_keyboard=True)

# --- ОБРАБОТЧИКИ (HANDLERS) ---

@dp.message(CommandStart())
async def start_cmd(msg: types.Message, state: FSMContext):
    await state.clear()
    db.execute("INSERT OR IGNORE INTO stats_all (user_id) VALUES (?)", (msg.from_user.id,))
    await msg.answer(WELCOME_MSG, reply_markup=get_main_kb(msg.from_user.id), parse_mode="MarkdownV2")

@dp.message(F.text == "❌ Отменить действие")
async def cancel_cmd(msg: types.Message, state: FSMContext):
    await state.clear()
    await msg.answer("🛑 *Действие отменено\\.*", reply_markup=get_main_kb(msg.from_user.id), parse_mode="MarkdownV2")

@dp.message(F.text == "👥 Сотрудники")
async def staff_cmd(msg: types.Message):
    data = db.execute("SELECT name, role, elo, last_seen FROM staff", fetch=True)
    out = "👥 *Команда проекта:*\n\n"
    now = int(time.time())
    for s in data:
        ls = s[3] if s[3] else now
        diff = now - ls
        status = "🟢 онлайн" if diff < 300 else f"⚪ был {diff // 60} мин\\. назад"
        out += f"⚙️ *{s[0]}* \\({s[2]} эло\\)\n└ {s[1]} | {status}\n\n"
    await msg.answer(out, parse_mode="MarkdownV2")

@dp.message(F.text == "📊 Мой рейтинг")
async def my_rating_cmd(msg: types.Message):
    u = db.execute("SELECT username, elo FROM users WHERE user_id = ?", (msg.from_user.id,), fetch=True)
    if not u: return await msg.answer("❌ *Вас нет в базе лидеров\\!*", parse_mode="MarkdownV2")
    all_p = db.execute("SELECT user_id FROM users ORDER BY elo DESC, user_id ASC", fetch=True)
    rank = next((i for i, (uid,) in enumerate(all_p, 1) if uid == msg.from_user.id), "?")
    await msg.answer(f"📊 *Ваша статистика:*\n\n👤 Ник: `{u[0][0]}`\n🏒 Эло: `{u[0][1]}`\n🏆 Место: *{rank}*", parse_mode="MarkdownV2")

@dp.message(F.text == "🏆 Топ 10")
async def top10_cmd(msg: types.Message):
    d = db.execute("SELECT username, elo FROM users ORDER BY elo DESC LIMIT 10", fetch=True)
    txt = "🏆 *Топ 10 сильнейших:*\n\n"
    if not d: txt += "_Данных нет\\._"
    else:
        for i, (n, e) in enumerate(d, 1): txt += f"{i}\\. `{n}` — *{e}*\n"
    await msg.answer(txt, parse_mode="MarkdownV2")

@dp.message(F.text == "🌟 Топ 100")
async def top100_cmd(msg: types.Message):
    d = db.execute("SELECT username, elo FROM users ORDER BY elo DESC LIMIT 100", fetch=True)
    txt = "🌟 *Топ 100 лидеров:*\n\n"
    if not d: txt += "_Данных нет\\._"
    else:
        for i, (n, e) in enumerate(d, 1): txt += f"{i}\\. `{n}` — *{e}*\n"
    await msg.answer(txt, parse_mode="MarkdownV2")

# --- ЛОГИКА ЗАЯВОК ---

@dp.message(F.text == "➕ Добавить аккаунт")
async def add_acc_1(msg: types.Message, state: FSMContext):
    await msg.answer("📝 *Шаг 1 из 2*\nВведите Ник и Эло через пробел\\.\n\nНапр: `Player 1000`", 
                     reply_markup=get_cancel_kb(), parse_mode="MarkdownV2")
    await state.set_state(RegState.data)

@dp.message(RegState.data)
async def add_acc_2(msg: types.Message, state: FSMContext):
    try:
        p = msg.text.split()
        if len(p) < 2: raise ValueError
        name, elo = p[0], int(p[1])
        await state.update_data(n=name, e=elo)
        await msg.answer("📸 *Шаг 2 из 2*\nПришлите скриншот\\.", parse_mode="MarkdownV2")
        await state.set_state(RegState.photo)
    except:
        await msg.answer("⚠️ *Ошибка\\!* Формат: `Ник Число` (через пробел)\\.")

@dp.message(RegState.photo, F.photo)
async def add_acc_3(msg: types.Message, state: FSMContext):
    d = await state.get_data()
    db.execute("INSERT INTO queue (user_id, q_type, name, elo, photo) VALUES (?, ?, ?, ?, ?)", 
               (msg.from_user.id, "NEW", d['n'], d['e'], msg.photo[-1].file_id))
    await msg.answer("⏳ *Заявка на регистрацию создана\\!*", reply_markup=get_main_kb(msg.from_user.id), parse_mode="MarkdownV2")
    await state.clear()

@dp.message(F.text == "🔄 Обновить эло")
async def update_elo_1(msg: types.Message, state: FSMContext):
    u = db.execute("SELECT username FROM users WHERE user_id = ?", (msg.from_user.id,), fetch=True)
    if not u: return await msg.answer("❌ *Вы не зарегистрированы\\!*", parse_mode="MarkdownV2")
    await state.update_data(n=u[0][0])
    await msg.answer(f"👤 Ник: *{u[0][0]}*\nВведите новое значение Эло:", reply_markup=get_cancel_kb(), parse_mode="MarkdownV2")
    await state.set_state(UpdState.elo)

@dp.message(UpdState.elo)
async def update_elo_2(msg: types.Message, state: FSMContext):
    if not msg.text.isdigit(): return await msg.answer("⚠️ *Введите число\\!*")
    await state.update_data(e=int(msg.text))
    await msg.answer("📸 Пришлите актуальный скриншот\\.", parse_mode="MarkdownV2")
    await state.set_state(UpdState.photo)

@dp.message(UpdState.photo, F.photo)
async def update_elo_3(msg: types.Message, state: FSMContext):
    d = await state.get_data()
    db.execute("INSERT INTO queue (user_id, q_type, name, elo, photo) VALUES (?, ?, ?, ?, ?)", 
               (msg.from_user.id, "UPD", d['n'], d['e'], msg.photo[-1].file_id))
    await msg.answer("⏳ *Запрос на обновление отправлен\\!*", reply_markup=get_main_kb(msg.from_user.id), parse_mode="MarkdownV2")
    await state.clear()

# --- ADMIN PANEL ---

@dp.message(F.text == "⚙️ Admin Panel")
async def admin_menu_cmd(msg: types.Message):
    is_s = db.execute("SELECT 1 FROM staff WHERE user_id = ?", (msg.from_user.id,), fetch=True)
    if not (msg.from_user.id == OWNER_ID or is_s): return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📂 Просмотр заявок", callback_data="v_q")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="v_s")]
    ])
    await msg.answer("🛠 *Панель управления:*", reply_markup=kb, parse_mode="MarkdownV2")

@dp.callback_query(F.data == "v_q")
async def admin_view_queue(call: types.CallbackQuery):
    r = db.execute("SELECT id, q_type, name, elo, photo FROM queue LIMIT 1", fetch=True)
    if not r: return await call.message.answer("🎉 *Очередь пуста\\!*", parse_mode="MarkdownV2")
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"y_{r[0][0]}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"n_{r[0][0]}")
    ]])
    cap = f"📦 *Заявка #{r[0][0]}* \\({r[0][1]}\\)\n👤 Ник: `{r[0][2]}`\n🏒 Эло: *{r[0][3]}*"
    await bot.send_photo(call.from_user.id, r[0][4], caption=cap, reply_markup=kb, parse_mode="MarkdownV2")
    await call.answer()

@dp.callback_query(F.data.startswith(("y_", "n_")))
async def admin_decision(call: types.CallbackQuery):
    act, rid = call.data.split("_")
    d = db.execute("SELECT user_id, q_type, name, elo FROM queue WHERE id = ?", (int(rid),), fetch=True)
    if d and act == "y":
        db.execute("INSERT OR REPLACE INTO users (user_id, username, elo) VALUES (?, ?, ?)", (d[0][0], d[0][2], d[0][3]))
        try: await bot.send_message(d[0][0], f"✅ *Ваша заявка одобрена\\!*", parse_mode="MarkdownV2")
        except: pass
    db.execute("DELETE FROM queue WHERE id = ?", (int(rid),))
    await call.message.delete(); await call.answer("Выполнено")

@dp.callback_query(F.data == "v_s")
async def admin_stats(call: types.CallbackQuery):
    u = db.execute("SELECT COUNT(*) FROM stats_all", fetch=True)[0][0]
    t = db.execute("SELECT COUNT(*) FROM users", fetch=True)[0][0]
    q = db.execute("SELECT COUNT(*) FROM queue", fetch=True)[0][0]
    await call.message.answer(f"📊 *Статистика:*\nВсего в боте: {u}\nВ ТОПе: {t}\nВ очереди: {q}", parse_mode="MarkdownV2")
    await call.answer()

@dp.message(Command("del"))
async def admin_delete(msg: types.Message):
    if msg.from_user.id != OWNER_ID: return
    n = msg.text.replace("/del ", "").strip()
    db.execute("DELETE FROM users WHERE username = ?", (n,))
    await msg.answer(f"🗑 Игрок `{n}` удален\\.", parse_mode="MarkdownV2")

# --- СЕРВЕР И ТОЧКА ВХОДА ---
async def health_check(request): return web.Response(text="Bot Alive")

async def start_bot():
    logger.info("Starting Hockey System Application...")
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8080))).start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(start_bot())
    except Exception as fatal:
        logger.error(f"Fatal error: {fatal}")

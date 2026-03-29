import os
import asyncio
import sqlite3
import logging
import time
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiohttp import web

# --- НАСТРОЙКА ЛОГИРОВАНИЯ ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("HockeyBot")

# --- КОНФИГУРАЦИЯ ---
TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = 8239542728 

bot = Bot(token=TOKEN)
dp = Dispatcher()

# ТЕКСТ С ЭКРАНИРОВАНИЕМ
WELCOME_TEXT = (
    "🏒 *Добро пожаловать в Лидерборд Три Кота Хоккей\\!*\n\n"
    "Следи за своим рейтингом и стань лучшим игроком\\!\n\n"
    "⚠️ *Отказ от ответственности:*\n"
    "Бот создан фанатами\\. Не связан с AppQuiz/Edujoy\\."
)

# --- СОСТОЯНИЯ (FSM) ---
class RegistrationState(StatesGroup):
    input_data = State()
    input_photo = State()

class UpdateEloState(StatesGroup):
    input_elo = State()
    input_photo = State()

# --- КЛАСС БАЗЫ ДАННЫХ ---
class Database:
    def __init__(self, db_path='hockey_elo.db'):
        self.db_path = db_path
        self._create_tables()

    def _create_tables(self):
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            # Игроки
            cur.execute('''CREATE TABLE IF NOT EXISTS users 
                (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, elo INTEGER)''')
            # Все пользователи
            cur.execute('''CREATE TABLE IF NOT EXISTS all_users (user_id INTEGER PRIMARY KEY)''')
            # Персонал
            cur.execute('''CREATE TABLE IF NOT EXISTS staff 
                (user_id INTEGER PRIMARY KEY, name TEXT, role TEXT, elo INTEGER, last_seen INTEGER)''')
            # Очередь
            cur.execute('''CREATE TABLE IF NOT EXISTS pending 
                (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, type TEXT, name TEXT, elo INTEGER, photo_id TEXT)''')
            
            # Владелец
            cur.execute("INSERT OR IGNORE INTO staff (user_id, name, role, elo, last_seen) VALUES (?, ?, ?, ?, ?)", 
                         (OWNER_ID, "orbsi", "Владелец", 4002, int(time.time())))
            conn.commit()

    def query(self, sql, params=(), fetch=False):
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            res = cur.fetchall() if fetch else None
            conn.commit()
            return res

db = Database()

# --- КЛАВИАТУРЫ ---
def get_main_kb(user_id):
    is_staff = db.query("SELECT 1 FROM staff WHERE user_id = ?", (user_id,), fetch=True)
    kb = [
        [KeyboardButton(text="➕ Добавить аккаунт"), KeyboardButton(text="🔄 Обновить эло")],
        [KeyboardButton(text="🏆 Топ 10"), KeyboardButton(text="🌟 Топ 100")],
        [KeyboardButton(text="📊 Мой рейтинг"), KeyboardButton(text="👥 Сотрудники")]
    ]
    if user_id == OWNER_ID or is_staff:
        kb.append([KeyboardButton(text="⚙️ Admin Panel")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_cancel_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Отменить действие")]], resize_keyboard=True)

# --- ВСПОМОГАТЕЛЬНОЕ ---
async def track_online(user_id):
    db.query("UPDATE staff SET last_seen = ? WHERE user_id = ?", (int(time.time()), user_id))

# --- ХЕНДЛЕРЫ ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    db.query("INSERT OR IGNORE INTO all_users (user_id) VALUES (?)", (uid,))
    await track_online(uid)
    await message.answer(WELCOME_TEXT, reply_markup=get_main_kb(uid), parse_mode="MarkdownV2")

@dp.message(F.text == "❌ Отменить действие")
async def cmd_cancel(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("🛑 *Действие прервано\\.*", reply_markup=get_main_kb(message.from_user.id), parse_mode="MarkdownV2")

@dp.message(F.text == "👥 Сотрудники")
async def cmd_staff(message: types.Message):
    await track_online(message.from_user.id)
    staff = db.query("SELECT name, role, elo, last_seen FROM staff", fetch=True)
    out = "👥 *Команда проекта:*\n\n"
    now = int(time.time())
    for s in staff:
        diff = now - s[3]
        status = "🟢 онлайн" if diff < 300 else f"⚪ был {diff // 60} мин\\. назад"
        out += f"⚙️ *{s[0]}* \\({s[2]} эло\\)\n└ {s[1]} | {status}\n\n"
    await message.answer(out, parse_mode="MarkdownV2")

@dp.message(F.text == "📊 Мой рейтинг")
async def cmd_my(message: types.Message):
    await track_online(message.from_user.id)
    u = db.query("SELECT username, elo FROM users WHERE user_id = ?", (message.from_user.id,), fetch=True)
    if not u: return await message.answer("❌ *Вас нет в базе\\.*", parse_mode="MarkdownV2")
    
    all_p = db.query("SELECT user_id FROM users ORDER BY elo DESC, id ASC", fetch=True)
    rank = next((i for i, (uid,) in enumerate(all_p, 1) if uid == message.from_user.id), "?")
    await message.answer(f"📊 *Ваш профиль:*\n\nНик: `{u[0][0]}`\nЭло: `{u[0][1]}`\nМесто: *{rank}*", parse_mode="MarkdownV2")

@dp.message(F.text == "🏆 Топ 10")
async def cmd_t10(message: types.Message):
    await track_online(message.from_user.id)
    data = db.query("SELECT username, elo FROM users ORDER BY elo DESC LIMIT 10", fetch=True)
    msg = "🏆 *Топ 10 игроков:*\n\n"
    if not data: msg += "_Пусто\\._"
    else:
        for i, (n, e) in enumerate(data, 1): msg += f"{i}\\. `{n}` — *{e}*\n"
    await message.answer(msg, parse_mode="MarkdownV2")

@dp.message(F.text == "🌟 Топ 100")
async def cmd_t100(message: types.Message):
    await track_online(message.from_user.id)
    data = db.query("SELECT username, elo FROM users ORDER BY elo DESC LIMIT 100", fetch=True)
    msg = "🌟 *Топ 100 лидеров:*\n\n"
    if not data: msg += "_Пусто\\._"
    else:
        for i, (n, e) in enumerate(data, 1): msg += f"{i}\\. `{n}` — *{e}*\n"
    await message.answer(msg, parse_mode="MarkdownV2")

# --- ПРОЦЕСС РЕГИСТРАЦИИ ---

@dp.message(F.text == "➕ Добавить аккаунт")
async def reg_1(message: types.Message, state: FSMContext):
    await track_online(message.from_user.id)
    await message.answer("📝 *Шаг 1 из 2*\nВведите Ник и Эло через пробел\\.\n\nНапр: `Player 1000`", 
                         reply_markup=get_cancel_kb(), parse_mode="MarkdownV2")
    await state.set_state(RegistrationState.input_data)

@dp.message(RegistrationState.input_data)
async def reg_2(message: types.Message, state: FSMContext):
    try:
        n, e = message.text.split()[0], int(message.text.split()[1])
        await state.update_data(n=n, e=e)
        await message.answer("📸 *Шаг 2 из 2*\nПришлите скриншот игры для проверки\\.", parse_mode="MarkdownV2")
        await state.set_state(RegistrationState.input_photo)
    except: await message.answer("⚠️ *Ошибка\\!* Введите: `Ник Число`")

@dp.message(RegistrationState.input_photo, F.photo)
async def reg_3(message: types.Message, state: FSMContext):
    d = await state.get_data()
    db.query("INSERT INTO pending (user_id, type, name, elo, photo_id) VALUES (?, ?, ?, ?, ?)",
             (message.from_user.id, "NEW", d['n'], d['e'], message.photo[-1].file_id))
    await message.answer("⏳ *Заявка создана\\!*", reply_markup=get_main_kb(message.from_user.id), parse_mode="MarkdownV2")
    await state.clear()

# --- ОБНОВЛЕНИЕ ---

@dp.message(F.text == "🔄 Обновить эло")
async def upd_1(message: types.Message, state: FSMContext):
    await track_online(message.from_user.id)
    u = db.query("SELECT username FROM users WHERE user_id = ?", (message.from_user.id,), fetch=True)
    if not u: return await message.answer("❌ *Сначала добавьте аккаунт\\!*", parse_mode="MarkdownV2")
    await state.update_data(n=u[0][0])
    await message.answer(f"👤 Ник: *{u[0][0]}*\n\nВведите новое Эло:", reply_markup=get_cancel_kb(), parse_mode="MarkdownV2")
    await state.set_state(UpdateEloState.input_elo)

@dp.message(UpdateEloState.input_elo)
async def upd_2(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("⚠️ *Введите число\\!*")
    await state.update_data(e=int(message.text))
    await message.answer("📸 Пришлите новый скриншот\\.", parse_mode="MarkdownV2")
    await state.set_state(UpdateEloState.input_photo)

@dp.message(UpdateEloState.input_photo, F.photo)
async def upd_3(message: types.Message, state: FSMContext):
    d = await state.get_data()
    db.query("INSERT INTO pending (user_id, type, name, elo, photo_id) VALUES (?, ?, ?, ?, ?)",
             (message.from_user.id, "UPDATE", d['n'], d['e'], message.photo[-1].file_id))
    await message.answer("⏳ *Запрос отправлен\\!*", reply_markup=get_main_kb(message.from_user.id), parse_mode="MarkdownV2")
    await state.clear()

# --- АДМИНКА ---

@dp.message(F.text == "⚙️ Admin Panel")
async def adm_p(message: types.Message):
    is_s = db.query("SELECT 1 FROM staff WHERE user_id = ?", (message.from_user.id,), fetch=True)
    if not (message.from_user.id == OWNER_ID or is_s): return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📂 Заявки", callback_data="v_r")],
        [InlineKeyboardButton(text="📊 Стата", callback_data="st_b")]
    ])
    await message.answer("🛠 *Панель администратора:*", reply_markup=kb, parse_mode="MarkdownV2")

@dp.callback_query(F.data == "v_r")
async def v_req(call: types.CallbackQuery):
    r = db.query("SELECT id, type, name, elo, photo_id FROM pending LIMIT 1", fetch=True)
    if not r: return await call.message.answer("🎉 *Заявок нет\\!*", parse_mode="MarkdownV2")
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Ок", callback_data=f"ok_{r[0][0]}"),
        InlineKeyboardButton(text="❌ Нет", callback_data=f"no_{r[0][0]}")
    ]])
    cap = f"📦 *Заявка #{r[0][0]}*\n👤 Игрок: `{r[0][2]}`\n🏒 Эло: *{r[0][3]}*"
    await bot.send_photo(call.from_user.id, r[0][4], caption=cap, reply_markup=kb, parse_mode="MarkdownV2")
    await call.answer()

@dp.callback_query(F.data.startswith(("ok_", "no_")))
async def d_req(call: types.CallbackQuery):
    act, rid = call.data.split("_")
    d = db.query("SELECT user_id, type, name, elo FROM pending WHERE id = ?", (int(rid),), fetch=True)
    if d and act == "ok":
        if d[0][1] == "NEW": db.query("INSERT INTO users (user_id, username, elo) VALUES (?, ?, ?)", (d[0][0], d[0][2], d[0][3]))
        else: db.query("UPDATE users SET elo = ? WHERE user_id = ?", (d[0][3], d[0][0]))
        try: await bot.send_message(d[0][0], f"✅ *Заявка одобрена\\!* Эло: {d[0][3]}", parse_mode="MarkdownV2")
        except: pass
    db.query("DELETE FROM pending WHERE id = ?", (int(rid),))
    await call.message.delete(); await call.answer("Готово")

@dp.callback_query(F.data == "st_b")
async def st_b(call: types.CallbackQuery):
    u, t, p = db.query("SELECT COUNT(*) FROM all_users", fetch=True)[0][0], db.query("SELECT COUNT(*) FROM users", fetch=True)[0][0], db.query("SELECT COUNT(*) FROM pending", fetch=True)[0][0]
    await call.message.answer(f"📊 *Стата:*\nЮзеров: {u}\nВ топе: {t}\nОчередь: {p}", parse_mode="MarkdownV2")
    await call.answer()

@dp.message(Command("del"))
async def adm_del(message: types.Message):
    if message.from_user.id != OWNER_ID: return
    n = message.text.replace("/del ", "").strip()
    db.query("DELETE FROM users WHERE username = ?", (n,))
    await message.answer(f"🗑 Игрок *{n}* удален\\.", parse_mode="MarkdownV2")

# --- SERVER ---
async def h_w(r): return web.Response(text="Live")
async def main():
    app = web.Application(); app.router.add_get("/", h_w)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8080))).start()
    await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())

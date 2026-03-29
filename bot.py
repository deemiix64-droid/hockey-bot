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

# --- КОНФИГ ---
TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = 8239542728 # Твой ID

bot = Bot(token=TOKEN)
dp = Dispatcher()
logging.basicConfig(level=logging.INFO)

DISCLAIMER = "\n\n⚠️ **Отказ от ответственности:**\nБот создан фанатами. Не связан с AppQuiz/Edujoy."

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
    db_query('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, elo INTEGER)''')
    db_query('''CREATE TABLE IF NOT EXISTS all_users (user_id INTEGER PRIMARY KEY)''')
    db_query('''CREATE TABLE IF NOT EXISTS staff (user_id INTEGER PRIMARY KEY, name TEXT, role TEXT, elo INTEGER, last_seen INTEGER)''')
    db_query('''CREATE TABLE IF NOT EXISTS pending (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, type TEXT, name TEXT, elo INTEGER, photo_id TEXT)''')
    db_query("INSERT OR IGNORE INTO staff (user_id, name, role, elo, last_seen) VALUES (?, ?, ?, ?, ?)", 
             (OWNER_ID, "orbsi", "Владелец", 4002, int(time.time())))

def get_main_kb(user_id):
    is_staff = db_query("SELECT 1 FROM staff WHERE user_id = ?", (user_id,), fetch=True)
    buttons = [
        [KeyboardButton(text="➕ Добавить аккаунт"), KeyboardButton(text="🔄 Обновить эло")],
        [KeyboardButton(text="🏆 Топ 10"), KeyboardButton(text="🌟 Топ 100")],
        [KeyboardButton(text="📊 Мой рейтинг"), KeyboardButton(text="👥 Сотрудники")]
    ]
    if user_id == OWNER_ID or is_staff:
        buttons.append([KeyboardButton(text="⚙️ Админ Панель")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# --- АДМИН-КОМАНДЫ (СТРОГАЯ ПРОВЕРКА) ---

@dp.message(Command("addstaff"))
async def add_staff_cmd(message: types.Message):
    if message.from_user.id != OWNER_ID: return
    try:
        args = message.text.split()
        sid, sname, srole = int(args[1]), args[2], " ".join(args[3:])
        db_query("INSERT OR REPLACE INTO staff (user_id, name, role, elo, last_seen) VALUES (?, ?, ?, ?, ?)", 
                 (sid, sname, srole, 0, int(time.time())))
        await message.answer(f"✅ {sname} добавлен в команду!")
    except: await message.answer("Ошибка! Формат: `/addstaff ID Имя Роль`")

@dp.message(Command("staffelo"))
async def set_staff_elo(message: types.Message):
    is_staff = db_query("SELECT 1 FROM staff WHERE user_id = ?", (message.from_user.id,), fetch=True)
    if not (message.from_user.id == OWNER_ID or is_staff): return
    try:
        args = message.text.split()
        name, elo = args[1], int(args[2])
        db_query("UPDATE staff SET elo = ? WHERE name = ?", (elo, name))
        await message.answer(f"✅ Эло сотрудника {name} изменено на {elo}")
    except: await message.answer("Формат: `/staffelo Имя Число`")

@dp.message(Command("del"))
async def delete_user(message: types.Message):
    is_staff = db_query("SELECT 1 FROM staff WHERE user_id = ?", (message.from_user.id,), fetch=True)
    if not (message.from_user.id == OWNER_ID or is_staff): return
    name = message.text.replace("/del ", "").strip()
    db_query("DELETE FROM users WHERE username = ?", (name,))
    await message.answer(f"🗑 Игрок {name} удален из ТОПа.")

# --- ОСНОВНЫЕ ХЕНДЛЕРЫ ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    db_query("INSERT OR IGNORE INTO all_users (user_id) VALUES (?)", (message.from_user.id,))
    await message.answer(f"🏒 **Лидерборд Три Кота Хоккей!**{DISCLAIMER}", reply_markup=get_main_kb(message.from_user.id), parse_mode="Markdown")

@dp.message(F.text == "📊 Мой рейтинг")
async def my_rating(message: types.Message):
    user = db_query("SELECT username, elo FROM users WHERE user_id = ?", (message.from_user.id,), fetch=True)
    if not user: return await message.answer("❌ Тебя нет в рейтинге.")
    all_users = db_query("SELECT user_id FROM users ORDER BY elo DESC, id ASC", fetch=True)
    rank = next((i for i, (uid,) in enumerate(all_users, 1) if uid == message.from_user.id), "?")
    await message.answer(f"📊 **Профиль:**\nНик: `{user[0][0]}`\nЭло: `{user[0][1]}`\nМесто: **{rank}**", parse_mode="Markdown")

@dp.message(F.text == "👥 Сотрудники")
async def show_staff(message: types.Message):
    staff = db_query("SELECT name, role, elo, last_seen FROM staff", fetch=True)
    text = "👥 **Команда проекта:**\n\n"
    for s in staff:
        ago = "только что" if (int(time.time()) - s[3]) < 60 else f"{(int(time.time()) - s[3]) // 60} мин. назад"
        text += f"⚙️ **{s[0]}** ({s[2]} эло)\n└ {s[1]} | {ago}\n\n"
    await message.answer(text, parse_mode="Markdown")

# --- СИСТЕМА ЗАЯВОК ---

@dp.message(F.text == "➕ Добавить аккаунт")
async def req_add(message: types.Message, state: FSMContext):
    await message.answer("📝 Введите Ник и Эло через пробел:"); await state.set_state(RequestScore.waiting_for_data)

@dp.message(RequestScore.waiting_for_data)
async def proc_add_data(message: types.Message, state: FSMContext):
    try:
        n, e = message.text.split()[0], int(message.text.split()[1])
        await state.update_data(n=n, e=e); await message.answer("📸 Скриншот меню:"); await state.set_state(RequestScore.waiting_for_photo)
    except: await message.answer("Пример: `Guptek 7000`")

@dp.message(RequestScore.waiting_for_photo, F.photo)
async def proc_add_photo(message: types.Message, state: FSMContext):
    d = await state.get_data()
    db_query("INSERT INTO pending (user_id, type, name, elo, photo_id) VALUES (?, ?, ?, ?, ?)", (message.from_user.id, "NEW", d['n'], d['e'], message.photo[-1].file_id))
    await message.answer("⏳ Отправлено!"); await state.clear()

@dp.message(F.text == "🔄 Обновить эло")
async def req_upd(message: types.Message, state: FSMContext):
    u = db_query("SELECT username FROM users WHERE user_id = ?", (message.from_user.id,), fetch=True)
    if not u: return await message.answer("❌ Сначала добавьте аккаунт!")
    await state.update_data(n=u[0][0]); await message.answer(f"Ник: **{u[0][0]}**\n📝 Введите НОВОЕ Эло:", parse_mode="Markdown")
    await state.set_state(UpdateScore.waiting_for_elo)

@dp.message(UpdateScore.waiting_for_elo)
async def proc_upd_elo(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("Число!")
    await state.update_data(e=int(message.text)); await message.answer("📸 Скриншот:"); await state.set_state(UpdateScore.waiting_for_photo)

@dp.message(UpdateScore.waiting_for_photo, F.photo)
async def proc_upd_photo(message: types.Message, state: FSMContext):
    d = await state.get_data()
    db_query("INSERT INTO pending (user_id, type, name, elo, photo_id) VALUES (?, ?, ?, ?, ?)", (message.from_user.id, "UPDATE", d['n'], d['e'], message.photo[-1].file_id))
    await message.answer("⏳ Запрос отправлен!"); await state.clear()

# --- АДМИН ПАНЕЛЬ ---

@dp.message(F.text == "⚙️ Админ Панель")
async def adm_menu(message: types.Message):
    is_s = db_query("SELECT 1 FROM staff WHERE user_id = ?", (message.from_user.id,), fetch=True)
    if not (message.from_user.id == OWNER_ID or is_s): return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="br"), InlineKeyboardButton(text="📊 Стата", callback_data="st")],
        [InlineKeyboardButton(text="📂 Заявки", callback_data="all_r")]
    ])
    await message.answer("🛠 Админка:", reply_markup=kb)

@dp.callback_query(F.data == "all_r")
async def show_reqs(call: types.CallbackQuery):
    r = db_query("SELECT id, type, name, elo, photo_id FROM pending LIMIT 1", fetch=True)
    if not r: return await call.message.answer("Заявок нет.")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅", callback_data=f"a_{r[0][0]}"), InlineKeyboardButton(text="❌", callback_data=f"r_{r[0][0]}")]])
    await bot.send_photo(call.from_user.id, r[0][4], caption=f"📦 #{r[0][0]} | {r[0][1]}\n👤 {r[0][2]}\n🏒 {r[0][3]}", reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data.startswith(("a_", "r_")))
async def h_req(call: types.CallbackQuery):
    act, rid = call.data.split("_")
    d = db_query("SELECT user_id, type, name, elo FROM pending WHERE id = ?", (int(rid),), fetch=True)
    if d and act == "a":
        if d[0][1] == "NEW": db_query("INSERT INTO users (user_id, username, elo) VALUES (?, ?, ?)", (d[0][0], d[0][2], d[0][3]))
        else: db_query("UPDATE users SET elo = ? WHERE user_id = ?", (d[0][3], d[0][0]))
        try: await bot.send_message(d[0][0], "✅ Одобрено!")
        except: pass
    db_query("DELETE FROM pending WHERE id = ?", (int(rid),))
    await call.message.delete(); await call.answer("Готово!")

@dp.callback_query(F.data == "st")
async def st_call(call: types.CallbackQuery):
    u = db_query("SELECT COUNT(*) FROM all_users", fetch=True)[0][0]
    t = db_query("SELECT COUNT(*) FROM users", fetch=True)[0][0]
    p = db_query("SELECT COUNT(*) FROM pending", fetch=True)[0][0]
    await call.message.answer(f"📊 Юзеры: **{u}**\n🏒 В топе: **{t}**\n⏳ Ждут: **{p}**", parse_mode="Markdown"); await call.answer()

@dp.message(F.text.in_({"🏆 Топ 10", "🌟 Топ 100"}))
async def s_top(message: types.Message):
    l = 10 if "10" in message.text else 100
    u = db_query("SELECT username, elo FROM users ORDER BY elo DESC LIMIT ?", (l,), fetch=True)
    res = f"🏆 **{message.text}**\n\n" + ("Пусто." if not u else "\n".join([f"{i+1}. {x[0]} — `{x[1]}`" for i, x in enumerate(u)]))
    await message.answer(res, parse_mode="Markdown")

@dp.callback_query(F.data == "br")
async def br_s(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("Текст рассылки:"); await state.set_state(BroadcastState.waiting_for_msg)

@dp.message(BroadcastState.waiting_for_msg)
async def br_e(message: types.Message, state: FSMContext):
    us = db_query("SELECT user_id FROM all_users", fetch=True)
    for u in us:
        try: await bot.send_message(u[0], f"📢 **РАССЫЛКА**\n\n{message.text}", parse_mode="Markdown")
        except: pass
    await message.answer("✅ Готово!"); await state.clear()

@dp.message()
async def track(message: types.Message):
    db_query("UPDATE staff SET last_seen = ? WHERE user_id = ?", (int(time.time()), message.from_user.id))

async def handle_p(r): return web.Response(text="ok")
async def main():
    init_db()
    app = web.Application(); app.router.add_get("/", handle_p)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8080))).start()
    await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())

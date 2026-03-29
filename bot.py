import os
import asyncio
import sqlite3
import logging
import time
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web

# --- 1. НАСТРОЙКИ (ВСТАВЬ СВОЙ ТОКЕН) ---
TOKEN = os.getenv("BOT_TOKEN") 
OWNER_ID = 8239542728 

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

class RegState(StatesGroup): data = State(); photo = State()
class UpdState(StatesGroup): elo = State(); photo = State()
class PostState(StatesGroup): text = State()

# --- 2. БАЗА ДАННЫХ ---
DB_NAME = 'hockey_pro_v29.db'

def db_query(sql, params=(), fetch=False, fetchone=False):
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()
        c.execute(sql, params)
        if fetch: return c.fetchall()
        if fetchone: return c.fetchone()
        conn.commit()

# Инициализация таблиц
db_query("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, elo INTEGER, date TEXT)")
db_query("CREATE TABLE IF NOT EXISTS staff (user_id INTEGER PRIMARY KEY, name TEXT, role TEXT, last_seen INTEGER)")
db_query("CREATE TABLE IF NOT EXISTS queue (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, q_type TEXT, name TEXT, elo INTEGER, photo TEXT)")
db_query("INSERT OR IGNORE INTO staff VALUES (?, ?, ?, ?)", (OWNER_ID, "orbsi", "Владелец", int(time.time())))

# --- 3. КЛАВИАТУРЫ ---
def get_main_kb(uid):
    is_adm = db_query("SELECT 1 FROM staff WHERE user_id = ?", (uid,), fetchone=True)
    btns = [
        [KeyboardButton(text="➕ Добавить аккаунт"), KeyboardButton(text="🔄 Обновить эло")],
        [KeyboardButton(text="🏆 Топ 10"), KeyboardButton(text="🌟 Топ 100")],
        [KeyboardButton(text="📊 Мой рейтинг"), KeyboardButton(text="👥 Сотрудники")]
    ]
    if uid == OWNER_ID or is_adm:
        btns.append([KeyboardButton(text="⚙️ Админ-Панель")])
    return ReplyKeyboardMarkup(keyboard=btns, resize_keyboard=True)

# --- 4. ОБРАБОТЧИКИ ИГРОКОВ ---

@dp.message(CommandStart())
async def cmd_start(msg: types.Message, state: FSMContext):
    await state.clear()
    await msg.answer("🏒 Добро пожаловать! Я лидерборд Три Кота Хоккей.", reply_markup=get_main_kb(msg.from_user.id))

@dp.message(F.text == "📊 Мой рейтинг")
async def my_rating(msg: types.Message):
    u = db_query("SELECT username, elo FROM users WHERE user_id = ?", (msg.from_user.id,), fetchone=True)
    if not u: return await msg.answer("❌ Вас нет в базе! Сначала зарегистрируйтесь.")
    
    rank = db_query("SELECT COUNT(*) FROM users WHERE elo > ?", (u[1],), fetchone=True)[0] + 1
    await msg.answer(f"📊 Профиль: {u[0]}\nЭло: {u[1]}\nМесто в топе: #{rank}")

@dp.message(F.text == "🏆 Топ 10")
async def top_10(msg: types.Message):
    users = db_query("SELECT username, elo FROM users ORDER BY elo DESC LIMIT 10", fetch=True)
    txt = "🏆 ТОП 10 ИГРОКОВ:\n\n" + "\n".join([f"{i+1}. {u[0]} — {u[1]}" for i, u in enumerate(users)])
    await msg.answer(txt if users else "Топ пока пуст.")

@dp.message(F.text == "🌟 Топ 100")
async def top_100(msg: types.Message):
    users = db_query("SELECT username, elo FROM users ORDER BY elo DESC LIMIT 100", fetch=True)
    if not users: return await msg.answer("Список пуст.")
    txt = "🌟 ТОП 100 ИГРОКОВ:\n\n"
    for i, u in enumerate(users):
        txt += f"{i+1}. {u[0]} — {u[1]}\n"
        if (i+1) % 50 == 0:
            await msg.answer(txt); txt = ""
    if txt: await msg.answer(txt)

@dp.message(F.text == "👥 Сотрудники")
async def show_staff(msg: types.Message):
    data = db_query("SELECT name, role, last_seen FROM staff", fetch=True)
    txt = "👥 Команда:\n\n"
    now = int(time.time())
    for s in data:
        diff = now - s[3]
        ago = "только что" if diff < 60 else f"{diff//60} мин. назад" if diff < 3600 else f"{diff//3600} ч. назад"
        txt += f"{'🟢' if diff < 300 else '⚪'} {s[0]} ({s[1]})\n└ Активность: {ago}\n\n"
    await msg.answer(txt)

# --- 5. ЗАЯВКИ И ОБНОВЛЕНИЕ ---

@dp.message(F.text == "➕ Добавить аккаунт")
async def reg1(msg: types.Message, state: FSMContext):
    await msg.answer("📝 Введите Ник и Эло через пробел:", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Отмена")]], resize_keyboard=True))
    await state.set_state(RegState.data)

@dp.message(F.text == "🔄 Обновить эло")
async def upd1(msg: types.Message, state: FSMContext):
    u = db_query("SELECT 1 FROM users WHERE user_id = ?", (msg.from_user.id,), fetchone=True)
    if not u: return await msg.answer("❌ Сначала зарегистрируйтесь!")
    await msg.answer("🔄 Введите новое Эло (число):", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Отмена")]], resize_keyboard=True))
    await state.set_state(UpdState.elo)

@dp.message(UpdState.elo)
async def upd2(msg: types.Message, state: FSMContext):
    if not msg.text.isdigit(): return await msg.answer("Введите только число!")
    await state.update_data(e=int(msg.text))
    await msg.answer("📸 Пришлите скриншот:"); await state.set_state(UpdState.photo)

@dp.message(UpdState.photo, F.photo)
async def upd3(msg: types.Message, state: FSMContext):
    d = await state.get_data(); u = db_query("SELECT username FROM users WHERE user_id = ?", (msg.from_user.id,), fetchone=True)
    db_query("INSERT INTO queue (user_id, q_type, name, elo, photo) VALUES (?, 'UPD', ?, ?, ?)", (msg.from_user.id, u[0], d['e'], msg.photo[-1].file_id))
    await msg.answer("⏳ Заявка отправлена!", reply_markup=get_main_kb(msg.from_user.id)); await state.clear()

@dp.message(RegState.data)
async def reg2(msg: types.Message, state: FSMContext):
    try:
        p = msg.text.split(); await state.update_data(n=p[0], e=int(p[1]))
        await msg.answer("📸 Скриншот:"); await state.set_state(RegState.photo)
    except: await msg.answer("Формат: Ник Эло")

@dp.message(RegState.photo, F.photo)
async def reg3(msg: types.Message, state: FSMContext):
    d = await state.get_data()
    db_query("INSERT INTO queue (user_id, q_type, name, elo, photo) VALUES (?, 'NEW', ?, ?, ?)", (msg.from_user.id, d['n'], d['e'], msg.photo[-1].file_id))
    await msg.answer("⏳ Заявка на проверке!", reply_markup=get_main_kb(msg.from_user.id)); await state.clear()

# --- 6. АДМИНКА И РАССЫЛКА ---

@dp.message(F.text == "⚙️ Админ-Панель")
async def adm_menu(msg: types.Message):
    is_staff = db_query("SELECT 1 FROM staff WHERE user_id = ?", (msg.from_user.id,), fetchone=True)
    if msg.from_user.id != OWNER_ID and not is_staff: return
    db_query("UPDATE staff SET last_seen = ? WHERE user_id = ?", (int(time.time()), msg.from_user.id))
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📂 Заявки", callback_data="v_q"), InlineKeyboardButton(text="📢 Рассылка", callback_data="run_post")]
    ])
    await msg.answer("🛠 Админ-меню:", reply_markup=kb)

@dp.callback_query(F.data == "run_post")
async def post1(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("📢 Введите текст рассылки:"); await state.set_state(PostState.text); await call.answer()

@dp.message(PostState.text)
async def post2(msg: types.Message, state: FSMContext):
    users = db_query("SELECT user_id FROM users", fetch=True)
    count = 0
    for u in users:
        try: await bot.send_message(u[0], f"📢 Сообщение от админов:\n\n{msg.text}"); count += 1; await asyncio.sleep(0.1)
        except: pass
    await msg.answer(f"✅ Готово! Отправлено: {count}"); await state.clear()

@dp.callback_query(F.data == "v_q")
async def view_q(call: types.CallbackQuery):
    q = db_query("SELECT id, q_type, name, elo, photo FROM queue LIMIT 1", fetch=True)
    if not q: return await call.message.answer("📦 Очередь пуста.")
    btns = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data=f"y_{q[0][0]}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"n_{q[0][0]}")
    ]])
    await bot.send_photo(call.from_user.id, q[0][4], caption=f"Тип: {q[0][1]}\nНик: {q[0][2]}\nЭло: {q[0][3]}", reply_markup=btns)
    await call.answer()

@dp.callback_query(F.data.startswith(("y_", "n_")))
async def decide(call: types.CallbackQuery):
    act, rid = call.data.split("_")
    it = db_query("SELECT user_id, q_type, name, elo FROM queue WHERE id = ?", (rid,), fetchone=True)
    if it:
        if act == "y":
            db_query("INSERT OR REPLACE INTO users VALUES (?, ?, ?, ?)", (it[0], it[2], it[3], datetime.now().strftime("%d.%m")))
            await bot.send_message(it[0], "✅ Ваша заявка одобрена!")
        else: await bot.send_message(it[0], "❌ Заявка отклонена.")
    db_query("DELETE FROM queue WHERE id = ?", (rid,))
    await call.message.delete(); await view_q(call)

async def main():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="OK"))
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8080).start()
    await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())

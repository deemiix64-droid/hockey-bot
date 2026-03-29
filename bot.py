import os
import asyncio
import sqlite3
import logging
import time
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiohttp import web

# --- 1. НАСТРОЙКИ ---
TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = 8239542728 

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()

class RegState(StatesGroup): data, photo = State(), State()
class UpdState(StatesGroup): elo, photo = State(), State()

# --- 2. БАЗА ДАННЫХ ---
def db_query(sql, params=(), fetch=False):
    with sqlite3.connect('hockey_final.db') as conn:
        c = conn.cursor()
        c.execute(sql, params)
        res = c.fetchall() if fetch else None
        conn.commit()
        return res

# Инициализация таблиц
db_query("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, elo INTEGER)")
db_query("CREATE TABLE IF NOT EXISTS staff (user_id INTEGER PRIMARY KEY, name TEXT, role TEXT, last_seen INTEGER)")
db_query("CREATE TABLE IF NOT EXISTS queue (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, q_type TEXT, name TEXT, elo INTEGER, photo TEXT)")
db_query("INSERT OR IGNORE INTO staff VALUES (?, ?, ?, ?)", (OWNER_ID, "orbsi", "Владелец", int(time.time())))

# --- 3. КЛАВИАТУРЫ ---
def main_kb(uid):
    is_adm = db_query("SELECT 1 FROM staff WHERE user_id = ?", (uid,), fetch=True)
    btns = [
        [KeyboardButton(text="➕ Добавить аккаунт"), KeyboardButton(text="🔄 Обновить эло")],
        [KeyboardButton(text="🏆 Топ 10"), KeyboardButton(text="🌟 Топ 100")],
        [KeyboardButton(text="📊 Мой рейтинг"), KeyboardButton(text="👥 Сотрудники")]
    ]
    if uid == OWNER_ID or is_adm: 
        btns.append([KeyboardButton(text="⚙️ Admin Panel")])
    return ReplyKeyboardMarkup(keyboard=btns, resize_keyboard=True)

# --- 4. ОСНОВНЫЕ ОБРАБОТЧИКИ ---

@dp.message(CommandStart())
async def start(msg: types.Message, state: FSMContext):
    await state.clear()
    db_query("UPDATE staff SET last_seen = ? WHERE user_id = ?", (int(time.time()), msg.from_user.id))
    await msg.answer("🏒 Добро пожаловать в Лидерборд Три Кота Хоккей!", reply_markup=main_kb(msg.from_user.id))

@dp.message(F.text == "👥 Сотрудники")
async def show_staff(msg: types.Message):
    data = db_query("SELECT name, role, last_seen FROM staff", fetch=True)
    txt = "👥 Команда проекта:\n\n"
    now = int(time.time())
    for s in data:
        diff = now - s[2]
        if diff < 60: ago = "только что"
        elif diff < 3600: ago = f"{diff//60} мин. назад"
        else: ago = f"{diff//3600} ч. назад"
        status = "🟢" if diff < 300 else "⚪"
        txt += f"{status} {s[0]} — {s[1]}\n└ Активность: {ago}\n\n"
    await msg.answer(txt)

@dp.message(F.text == "🏆 Топ 10")
async def top10(msg: types.Message):
    res = db_query("SELECT username, elo FROM users ORDER BY elo DESC LIMIT 10", fetch=True)
    if not res: return await msg.answer("Топ пока пуст.")
    txt = "🏆 ТОП 10 ИГРОКОВ:\n\n" + "\n".join([f"{i+1}. {r[0]} — {r[1]}" for i, r in enumerate(res)])
    await msg.answer(txt)

@dp.message(F.text == "🌟 Топ 100")
async def top100(msg: types.Message):
    res = db_query("SELECT username, elo FROM users ORDER BY elo DESC LIMIT 100", fetch=True)
    if not res: return await msg.answer("Список пуст.")
    txt = "🌟 ТОП 100 ИГРОКОВ:\n\n" + "\n".join([f"{i+1}. {r[0]} — {r[1]}" for i, r in enumerate(res)])
    await msg.answer(txt)

@dp.message(F.text == "📊 Мой рейтинг")
async def my_rank(msg: types.Message):
    u = db_query("SELECT username, elo FROM users WHERE user_id = ?", (msg.from_user.id,), fetch=True)
    if not u: return await msg.answer("❌ Вас нет в базе! Нажмите 'Добавить аккаунт'.")
    all_p = db_query("SELECT user_id FROM users ORDER BY elo DESC", fetch=True)
    pos = next((i for i, (uid,) in enumerate(all_p, 1) if uid == msg.from_user.id), "?")
    await msg.answer(f"📊 Профиль: {u[0][0]}\nЭло: {u[0][1]}\nМесто: {pos}")

# --- 5. ЛОГИКА ЗАЯВОК ---

@dp.message(F.text == "➕ Добавить аккаунт")
async def reg1(msg: types.Message, state: FSMContext):
    await msg.answer("📝 Введите Ник и Эло (через пробел):", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Отмена")]], resize_keyboard=True))
    await state.set_state(RegState.data)

@dp.message(F.text == "🔄 Обновить эло")
async def upd1(msg: types.Message, state: FSMContext):
    u = db_query("SELECT 1 FROM users WHERE user_id = ?", (msg.from_user.id,), fetch=True)
    if not u: return await msg.answer("❌ Вы еще не в топе!")
    await msg.answer("🔄 Введите новое Эло:", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Отмена")]], resize_keyboard=True))
    await state.set_state(UpdState.elo)

@dp.message(UpdState.elo)
async def upd2(msg: types.Message, state: FSMContext):
    if not msg.text.isdigit(): return await msg.answer("Введите число!")
    await state.update_data(e=int(msg.text))
    await msg.answer("📸 Скриншот для подтверждения:"); await state.set_state(UpdState.photo)

@dp.message(UpdState.photo, F.photo)
async def upd3(msg: types.Message, state: FSMContext):
    d = await state.get_data(); u = db_query("SELECT username FROM users WHERE user_id = ?", (msg.from_user.id,), fetch=True)
    db_query("INSERT INTO queue (user_id, q_type, name, elo, photo) VALUES (?, 'UPD', ?, ?, ?)", (msg.from_user.id, u[0][0], d['e'], msg.photo[-1].file_id))
    await msg.answer("⏳ Заявка на обновление отправлена!", reply_markup=main_kb(msg.from_user.id)); await state.clear()

@dp.message(RegState.data)
async def reg2(msg: types.Message, state: FSMContext):
    try:
        p = msg.text.split(); await state.update_data(n=p[0], e=int(p[1]))
        await msg.answer("📸 Пришлите скриншот:"); await state.set_state(RegState.photo)
    except: await msg.answer("Формат: Ник Число")

@dp.message(RegState.photo, F.photo)
async def reg3(msg: types.Message, state: FSMContext):
    d = await state.get_data()
    db_query("INSERT INTO queue (user_id, q_type, name, elo, photo) VALUES (?, 'NEW', ?, ?, ?)", (msg.from_user.id, d['n'], d['e'], msg.photo[-1].file_id))
    await msg.answer("⏳ Заявка на вход отправлена!", reply_markup=main_kb(msg.from_user.id)); await state.clear()

@dp.message(F.text == "❌ Отмена")
async def cancel(msg: types.Message, state: FSMContext):
    await state.clear(); await msg.answer("Отменено.", reply_markup=main_kb(msg.from_user.id))

# --- 6. АДМИН-ПАНЕЛЬ ---

@dp.message(F.text == "⚙️ Admin Panel")
async def adm_menu(msg: types.Message):
    is_staff = db_query("SELECT 1 FROM staff WHERE user_id = ?", (msg.from_user.id,), fetch=True)
    if not (msg.from_user.id == OWNER_ID or is_staff): return
    db_query("UPDATE staff SET last_seen = ? WHERE user_id = ?", (int(time.time()), msg.from_user.id))
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📂 Список заявок", callback_data="view_requests")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="view_stats")]
    ])
    await msg.answer("🛠 Админ-панель модератора:", reply_markup=kb)

@dp.callback_query(F.data == "view_stats")
async def adm_stats(call: types.CallbackQuery):
    u = db_query("SELECT COUNT(*) FROM users", fetch=True)[0][0]
    q = db_query("SELECT COUNT(*) FROM queue", fetch=True)[0][0]
    await call.message.answer(f"📊 Статистика:\nИгроков в базе: {u}\nЗаявок в очереди: {q}")
    await call.answer()

@dp.callback_query(F.data == "view_requests")
async def view_queue(call: types.CallbackQuery):
    q = db_query("SELECT id, q_type, name, elo, photo FROM queue LIMIT 1", fetch=True)
    if not q: return await call.message.answer("📦 Заявок больше нет.")
    btns = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data=f"y_{q[0][0]}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"n_{q[0][0]}")
    ]])
    cap = f"Заявка #{q[0][0]} ({q[0][1]})\nНик: {q[0][2]}\nЭло: {q[0][3]}"
    try: await bot.send_photo(call.from_user.id, q[0][4], caption=cap, reply_markup=btns)
    except: await bot.send_message(call.from_user.id, f"{cap}\n⚠️ Ошибка фото!", reply_markup=btns)
    await call.answer()

@dp.callback_query(F.data.startswith(("y_", "n_")))
async def decide(call: types.CallbackQuery):
    act, rid = call.data.split("_")
    it = db_query("SELECT user_id, q_type, name, elo FROM queue WHERE id = ?", (rid,), fetch=True)
    if it:
        uid, qt, name, elo = it[0]
        if act == "y":
            db_query("INSERT OR REPLACE INTO users VALUES (?, ?, ?)", (uid, name, elo))
            msg = "✅ Ваша заявка одобрена!"
        else: msg = "❌ Ваша заявка отклонена."
        try: await bot.send_message(uid, f"{msg}\nНик: {name}, Эло: {elo}")
        except: pass
    db_query("DELETE FROM queue WHERE id = ?", (rid,))
    await call.message.delete(); await view_queue(call)

async def main():
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Bot Active"))
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8080).start()
    await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())

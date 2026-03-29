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
OWNER_ID = 8239542728 # Ваш ID

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()

class RegState(StatesGroup): data, photo = State(), State()
class UpdState(StatesGroup): elo, photo = State(), State()
class PostState(StatesGroup): text = State()

# --- 2. БАЗА ДАННЫХ ---
def db_query(sql, params=(), fetch=False):
    with sqlite3.connect('hockey_final.db') as conn:
        c = conn.cursor()
        c.execute(sql, params)
        res = c.fetchall() if fetch else None
        conn.commit()
        return res

# Инициализация
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

# --- 4. ОБРАБОТЧИКИ ---
@dp.message(CommandStart())
async def start(msg: types.Message, state: FSMContext):
    await state.clear()
    await msg.answer("🏒 Лидерборд Три Кота Хоккей готов!", reply_markup=main_kb(msg.from_user.id))

@dp.message(F.text == "👥 Сотрудники")
async def show_staff(msg: types.Message):
    data = db_query("SELECT name, role, last_seen FROM staff", fetch=True)
    txt = "👥 Команда проекта:\n\n"
    now = int(time.time())
    for s in data:
        diff = now - s[2]
        ago = "только что" if diff < 60 else f"{diff//60} мин. назад" if diff < 3600 else f"{diff//3600} ч. назад"
        status = "🟢" if diff < 300 else "⚪"
        txt += f"{status} {s[0]} — {s[1]}\n└ Был: {ago}\n\n"
    await msg.answer(txt)

@dp.message(F.text == "🏆 Топ 10")
async def top10(msg: types.Message):
    res = db_query("SELECT username, elo FROM users ORDER BY elo DESC LIMIT 10", fetch=True)
    txt = "🏆 ТОП 10:\n\n" + "\n".join([f"{i+1}. {r[0]} — {r[1]}" for i, r in enumerate(res)])
    await msg.answer(txt if res else "Топ пуст.")

# --- 5. ЛОГИКА ЗАЯВОК ---
@dp.message(F.text == "➕ Добавить аккаунт")
async def reg1(msg: types.Message, state: FSMContext):
    await msg.answer("📝 Введите Ник и Эло через пробел:", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Отмена")]], resize_keyboard=True))
    await state.set_state(RegState.data)

@dp.message(RegState.data)
async def reg2(msg: types.Message, state: FSMContext):
    try:
        p = msg.text.split(); await state.update_data(n=p[0], e=int(p[1]))
        await msg.answer("📸 Пришлите скриншот подтверждения:"); await state.set_state(RegState.photo)
    except: await msg.answer("Ошибка! Формат: Ник Эло")

@dp.message(RegState.photo, F.photo)
async def reg3(msg: types.Message, state: FSMContext):
    d = await state.get_data()
    db_query("INSERT INTO queue (user_id, q_type, name, elo, photo) VALUES (?, 'NEW', ?, ?, ?)", (msg.from_user.id, d['n'], d['e'], msg.photo[-1].file_id))
    await msg.answer("⏳ Заявка отправлена на проверку!", reply_markup=main_kb(msg.from_user.id)); await state.clear()

# --- 6. АДМИНКА И РАССЫЛКА ---
@dp.message(F.text == "⚙️ Admin Panel")
async def adm_menu(msg: types.Message):
    is_staff = db_query("SELECT 1 FROM staff WHERE user_id = ?", (msg.from_user.id,), fetch=True)
    if not (msg.from_user.id == OWNER_ID or is_staff): return
    db_query("UPDATE staff SET last_seen = ? WHERE user_id = ?", (int(time.time()), msg.from_user.id))
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📂 Заявки", callback_data="view_requests"), InlineKeyboardButton(text="📊 Статистика", callback_data="view_stats")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="make_post")]
    ])
    await msg.answer("🛠 Панель управления:", reply_markup=kb)

@dp.callback_query(F.data == "make_post")
async def post1(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("📢 Введите текст сообщения для ВСЕХ игроков:")
    await state.set_state(PostState.text); await call.answer()

@dp.message(PostState.text)
async def post2(msg: types.Message, state: FSMContext):
    users = db_query("SELECT user_id FROM users", fetch=True)
    count = 0
    for u in users:
        try: 
            await bot.send_message(u[0], f"📢 Сообщение от администрации:\n\n{msg.text}")
            count += 1
            await asyncio.sleep(0.05) # Защита от спам-фильтра Telegram
        except: pass
    await msg.answer(f"✅ Рассылка завершена!\nУспешно отправлено: {count} игрокам."); await state.clear()

@dp.callback_query(F.data == "view_requests")
async def view_queue(call: types.CallbackQuery):
    q = db_query("SELECT id, q_type, name, elo, photo FROM queue LIMIT 1", fetch=True)
    if not q: return await call.message.answer("📦 Заявок больше нет.")
    btns = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"y_{q[0][0]}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"n_{q[0][0]}")
    ]])
    await bot.send_photo(call.from_user.id, q[0][4], caption=f"Заявка #{q[0][0]}\nНик: {q[0][2]}\nЭло: {q[0][3]}", reply_markup=btns)
    await call.answer()

@dp.callback_query(F.data.startswith(("y_", "n_")))
async def decide(call: types.CallbackQuery):
    act, rid = call.data.split("_")
    it = db_query("SELECT user_id, q_type, name, elo FROM queue WHERE id = ?", (rid,), fetch=True)
    if it:
        uid, qt, name, elo = it[0]
        if act == "y":
            db_query("INSERT OR REPLACE INTO users VALUES (?, ?, ?)", (uid, name, elo))
            await bot.send_message(uid, f"✅ Ваша заявка одобрена!\nНик: {name}, Эло: {elo}")
        else:
            await bot.send_message(uid, "❌ Ваша заявка отклонена модератором.")
    db_query("DELETE FROM queue WHERE id = ?", (rid,))
    await call.message.delete(); await view_queue(call)

async def main():
    # Заглушка для хостинга
    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="Bot Running"))
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8080).start()
    await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())

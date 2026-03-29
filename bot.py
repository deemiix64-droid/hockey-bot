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
ADMINS = [8239542728] # Твой ID

bot = Bot(token=TOKEN)
dp = Dispatcher()
logging.basicConfig(level=logging.INFO)

# --- ТЕКСТЫ ---
DISCLAIMER = "\n\n⚠️ **Отказ от ответственности:**\nБот создан в развлекательных целях фанатским сообществом. Проект **не связан** с компаниями **AppQuiz** или **Edujoy**."

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
    db_query('''CREATE TABLE IF NOT EXISTS staff (user_id INTEGER PRIMARY KEY, name TEXT, role TEXT, elo INTEGER, last_seen INTEGER)''')
    db_query("INSERT OR IGNORE INTO staff (user_id, name, role, elo, last_seen) VALUES (?, ?, ?, ?, ?)", 
             (8239542728, "orbsi", "Владелец", 4002, int(time.time())))

def get_time_ago(seconds):
    diff = int(time.time()) - seconds
    if diff < 60: return "только что"
    elif diff < 3600: return f"{diff // 60} мин. назад"
    else: return f"{diff // 3600} ч. назад"

# --- КЛАВИАТУРА ---
def get_main_kb(user_id):
    is_staff = db_query("SELECT 1 FROM staff WHERE user_id = ?", (user_id,), fetch=True)
    buttons = [
        [KeyboardButton(text="➕ Добавить аккаунт"), KeyboardButton(text="🔄 Обновить эло")],
        [KeyboardButton(text="🏆 Топ 10"), KeyboardButton(text="🌟 Топ 100")],
        [KeyboardButton(text="📊 Мой рейтинг"), KeyboardButton(text="👥 Сотрудники")]
    ]
    if user_id in ADMINS or is_staff:
        buttons.append([KeyboardButton(text="⚙️ Админ Панель")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# --- ХЕНДЛЕРЫ ОБЩИЕ ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    db_query("INSERT OR IGNORE INTO all_users (user_id) VALUES (?)", (message.from_user.id,))
    await message.answer(f"🏒 **Лидерборд Три Кота Хоккей!**{DISCLAIMER}", reply_markup=get_main_kb(message.from_user.id), parse_mode="Markdown")

@dp.message(F.text == "👥 Сотрудники")
async def show_staff(message: types.Message):
    staff_list = db_query("SELECT name, role, elo, last_seen FROM staff", fetch=True)
    text = "👥 **Наша команда**\n\n"
    for s in staff_list:
        text += f"⚙️ **{s[0]}** ({s[2]} эло)\n└ {s[1]}\n🕒 Активен: {get_time_ago(s[3])}\n\n"
    text += "📌 *Независимый фанатский проект.*"
    await message.answer(text, parse_mode="Markdown")

# --- УПРАВЛЕНИЕ (ВЛАДЕЛЕЦ) ---

@dp.message(Command("addstaff"))
async def add_staff(message: types.Message):
    if message.from_user.id not in ADMINS: return
    try:
        p = message.text.split(maxsplit=3)
        db_query("INSERT OR REPLACE INTO staff VALUES (?, ?, ?, 0, ?)", (int(p[1]), p[2], p[3], int(time.time())))
        await message.answer("✅ Сотрудник добавлен!")
    except: await message.answer("`/addstaff ID Имя Роль`")

@dp.message(Command("staffelo"))
async def set_staff_elo(message: types.Message):
    if message.from_user.id not in ADMINS: return
    try:
        n, e = message.text.split()[1], int(message.text.split()[2])
        db_query("UPDATE staff SET elo = ? WHERE name = ?", (e, n))
        await message.answer(f"✅ Эло {n} теперь {e}")
    except: await message.answer("`/staffelo orbsi 5000`")

@dp.message(Command("del"))
async def delete_player(message: types.Message):
    if message.from_user.id not in ADMINS: return
    name = message.text.split()[1]
    db_query("DELETE FROM users WHERE username = ?", (name,))
    await message.answer(f"🗑 {name} удален.")

# --- ЗАЯВКИ ---

@dp.message(F.text == "➕ Добавить аккаунт")
async def req_add(message: types.Message, state: FSMContext):
    await message.answer("📝 Введите Ник и Эло через пробел:")
    await state.set_state(RequestScore.waiting_for_data)

@dp.message(RequestScore.waiting_for_data)
async def proc_add_data(message: types.Message, state: FSMContext):
    try:
        name, elo = message.text.split()[0], int(message.text.split()[1])
        await state.update_data(name=name, elo=elo)
        await message.answer("📸 Пришлите скриншот.")
        await state.set_state(RequestScore.waiting_for_photo)
    except: await message.answer("Ошибка!")

@dp.message(RequestScore.waiting_for_photo, F.photo)
async def proc_add_photo(message: types.Message, state: FSMContext):
    data = await state.get_data()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Одобрить", callback_data=f"ok_{data['name']}_{data['elo']}_{message.from_user.id}")],
        [InlineKeyboardButton(text="❌ Отказать", callback_data=f"no_{message.from_user.id}")]
    ])
    await bot.send_photo(ADMINS[0], message.photo[-1].file_id, caption=f"🆕 Новый: {data['name']} ({data['elo']})", reply_markup=kb)
    await message.answer("⏳ Отправлено!"); await state.clear()

@dp.message(F.text == "🔄 Обновить эло")
async def req_upd(message: types.Message, state: FSMContext):
    await message.answer("📝 Ник и Новое Эло:")
    await state.set_state(UpdateScore.waiting_for_elo)

@dp.message(UpdateScore.waiting_for_elo)
async def proc_upd_data(message: types.Message, state: FSMContext):
    try:
        name, elo = message.text.split()[0], int(message.text.split()[1])
        await state.update_data(name=name, elo=elo)
        await message.answer("📸 Скриншот:")
        await state.set_state(UpdateScore.waiting_for_photo)
    except: await message.answer("Ошибка!")

@dp.message(UpdateScore.waiting_for_photo, F.photo)
async def proc_upd_photo(message: types.Message, state: FSMContext):
    data = await state.get_data()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Обновить", callback_data=f"upd_{data['name']}_{data['elo']}_{message.from_user.id}")],
        [InlineKeyboardButton(text="❌ Отказать", callback_data=f"no_{message.from_user.id}")]
    ])
    await bot.send_photo(ADMINS[0], message.photo[-1].file_id, caption=f"🔄 Обновление: {data['name']} до {data['elo']}", reply_markup=kb)
    await message.answer("⏳ Запрос отправлен!"); await state.clear()

# --- CALLBACKS ---

@dp.callback_query(F.data.startswith("ok_"))
async def approve_add(call: types.CallbackQuery):
    _, name, elo, uid = call.data.split("_")
    db_query("INSERT INTO users (username, elo) VALUES (?, ?)", (name, int(elo)))
    await bot.send_message(int(uid), "✅ Ты в ТОПе!"); await call.message.edit_caption(caption=call.message.caption + "\n\n🟢 ПРИНЯТО")

@dp.callback_query(F.data.startswith("upd_"))
async def approve_upd(call: types.CallbackQuery):
    _, name, elo, uid = call.data.split("_")
    db_query("UPDATE users SET elo = ? WHERE username = ?", (int(elo), name))
    await bot.send_message(int(uid), "✅ Рейтинг обновлен!"); await call.message.edit_caption(caption=call.message.caption + "\n\n🔵 ОБНОВЛЕНО")

@dp.callback_query(F.data.startswith("no_"))
async def reject(call: types.CallbackQuery):
    await bot.send_message(int(call.data.split("_")[1]), "❌ Отклонено."); await call.message.edit_caption(caption=call.message.caption + "\n\n🔴 ОТКАЗАНО")

# --- ТОПЫ И ПАНЕЛЬ ---

@dp.message(F.text.in_({"🏆 Топ 10", "🌟 Топ 100"}))
async def show_top(message: types.Message):
    limit = 10 if "10" in message.text else 100
    users = db_query("SELECT username, elo FROM users ORDER BY elo DESC LIMIT ?", (limit,), fetch=True)
    res = f"**{message.text}**\n\n" + ("Пока пусто." if not users else "\n".join([f"{i+1}. {u[0]} — `{u[1]}`" for i, u in enumerate(users)]))
    await message.answer(res, parse_mode="Markdown")

@dp.message(F.text == "⚙️ Админ Панель")
async def adm_menu(message: types.Message):
    if not db_query("SELECT 1 FROM staff WHERE user_id = ?", (message.from_user.id,), fetch=True): return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📢 Рассылка", callback_data="broadcast"), InlineKeyboardButton(text="📊 Статистика", callback_data="stats")]])
    await message.answer("🛠 Меню:", reply_markup=kb)

@dp.callback_query(F.data == "stats")
async def stats_call(call: types.CallbackQuery):
    total = db_query("SELECT COUNT(*) FROM all_users", fetch=True)[0][0]
    in_top = db_query("SELECT COUNT(*) FROM users", fetch=True)[0][0]
    await call.message.answer(f"📊 Всего: {total}\n🏒 В топе: {in_top}"); await call.answer()

@dp.callback_query(F.data == "broadcast")
async def br_start(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("Введите сообщение:"); await state.set_state(BroadcastState.waiting_for_msg)

@dp.message(BroadcastState.waiting_for_msg)
async def br_send(message: types.Message, state: FSMContext):
    users = db_query("SELECT user_id FROM all_users", fetch=True)
    for u in users:
        try: await message.copy_to(u[0])
        except: pass
    await message.answer("✅ Готово!"); await state.clear()

# --- СИСТЕМНОЕ ---

@dp.message()
async def track_activity(message: types.Message):
    db_query("UPDATE staff SET last_seen = ? WHERE user_id = ?", (int(time.time()), message.from_user.id))

async def handle_ping(r): return web.Response(text="ok")

async def main():
    init_db()
    db_query("UPDATE staff SET last_seen = ? WHERE user_id = ?", (int(time.time()), 8239542728))
    app = web.Application(); app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8080))).start()
    await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())

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
    db_query('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, elo INTEGER)''')
    db_query('''CREATE TABLE IF NOT EXISTS all_users (user_id INTEGER PRIMARY KEY)''')
    db_query('''CREATE TABLE IF NOT EXISTS staff (user_id INTEGER PRIMARY KEY, name TEXT, role TEXT, elo INTEGER, last_seen INTEGER)''')
    db_query('''CREATE TABLE IF NOT EXISTS pending (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, type TEXT, name TEXT, elo INTEGER, photo_id TEXT)''')
    db_query("INSERT OR IGNORE INTO staff (user_id, name, role, elo, last_seen) VALUES (?, ?, ?, ?, ?)", 
             (8239542728, "orbsi", "Владелец", 4002, int(time.time())))

def get_time_ago(seconds):
    diff = int(time.time()) - seconds
    if diff < 60: return "только что"
    elif diff < 3600: return f"{diff // 60} мин. назад"
    else: return f"{diff // 3600} ч. назад"

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

# --- ХЕНДЛЕРЫ ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    db_query("INSERT OR IGNORE INTO all_users (user_id) VALUES (?)", (message.from_user.id,))
    await message.answer(f"🏒 **Лидерборд Три Кота Хоккей!**{DISCLAIMER}", reply_markup=get_main_kb(message.from_user.id), parse_mode="Markdown")

# МОЙ РЕЙТИНГ (ИСПРАВЛЕНО)
@dp.message(F.text == "📊 Мой рейтинг")
async def my_rating(message: types.Message):
    user = db_query("SELECT username, elo FROM users WHERE user_id = ?", (message.from_user.id,), fetch=True)
    if not user:
        return await message.answer("❌ Тебя еще нет в рейтинге. Нажми 'Добавить аккаунт'!")
    
    all_users = db_query("SELECT user_id FROM users ORDER BY elo DESC", fetch=True)
    rank = next((i for i, (uid,) in enumerate(all_users, 1) if uid == message.from_user.id), "?")
    
    await message.answer(f"📊 **Твой профиль:**\n👤 Ник: `{user[0][0]}`\n🏒 Эло: `{user[0][1]}`\n🏆 Место в топе: **{rank}**")

@dp.message(F.text == "👥 Сотрудники")
async def show_staff(message: types.Message):
    staff_list = db_query("SELECT name, role, elo, last_seen FROM staff", fetch=True)
    text = "👥 **Наша команда**\n\n"
    for s in staff_list:
        text += f"⚙️ **{s[0]}** ({s[2]} эло)\n└ {s[1]}\n🕒 Активен: {get_time_ago(s[3])}\n\n"
    await message.answer(text, parse_mode="Markdown")

# ЗАЯВКИ
@dp.message(F.text == "➕ Добавить аккаунт")
async def req_add(message: types.Message, state: FSMContext):
    await message.answer("📝 Введите ваш Ник и Эло через пробел:")
    await state.set_state(RequestScore.waiting_for_data)

@dp.message(RequestScore.waiting_for_data)
async def proc_add_data(message: types.Message, state: FSMContext):
    try:
        name, elo = message.text.split()[0], int(message.text.split()[1])
        await state.update_data(name=name, elo=elo)
        await message.answer("📸 Пришлите скриншот подтверждения (главное меню игры):")
        await state.set_state(RequestScore.waiting_for_photo)
    except: await message.answer("Ошибка! Пример: `Guptek 7000`")

@dp.message(RequestScore.waiting_for_photo, F.photo)
async def proc_add_photo(message: types.Message, state: FSMContext):
    data = await state.get_data()
    db_query("INSERT INTO pending (user_id, type, name, elo, photo_id) VALUES (?, ?, ?, ?, ?)", 
             (message.from_user.id, "NEW", data['name'], data['elo'], message.photo[-1].file_id))
    await message.answer("⏳ Заявка отправлена на модерацию!"); await state.clear()

# АДМИН ПАНЕЛЬ
@dp.message(F.text == "⚙️ Админ Панель")
async def adm_menu(message: types.Message):
    is_staff = db_query("SELECT 1 FROM staff WHERE user_id = ?", (message.from_user.id,), fetch=True)
    if not (message.from_user.id in ADMINS or is_staff): return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="broadcast"), InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton(text="📂 Все заявки", callback_data="all_reqs")]
    ])
    await message.answer("🛠 Меню управления:", reply_markup=kb)

@dp.callback_query(F.data == "all_reqs")
async def show_all_requests(call: types.CallbackQuery):
    reqs = db_query("SELECT id, type, name, elo FROM pending LIMIT 1", fetch=True)
    if not reqs: return await call.message.answer("Заявок пока нет."); await call.answer()
    
    r_id, r_type, r_name, r_elo = reqs[0]
    photo = db_query("SELECT photo_id FROM pending WHERE id = ?", (r_id,), fetch=True)[0][0]
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Одобрить", callback_data=f"ap_{r_id}"), InlineKeyboardButton(text="❌ Отклонить", callback_data=f"rj_{r_id}")]
    ])
    await bot.send_photo(call.from_user.id, photo, caption=f"Заявка #{r_id}\nТип: {r_type}\nНик: {r_name}\nЭло: {r_elo}", reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data.startswith(("ap_", "rj_")))
async def handle_request(call: types.CallbackQuery):
    action, r_id = call.data.split("_")
    data = db_query("SELECT user_id, type, name, elo FROM pending WHERE id = ?", (r_id,), fetch=True)
    if not data: return
    uid, r_type, name, elo = data[0]

    if action == "ap":
        if r_type == "NEW":
            db_query("INSERT INTO users (user_id, username, elo) VALUES (?, ?, ?)", (uid, name, elo))
        else:
            db_query("UPDATE users SET elo = ? WHERE user_id = ?", (elo, uid))
        await bot.send_message(uid, "✅ Твоя заявка одобрена!")
    else:
        await bot.send_message(uid, "❌ Твоя заявка отклонена.")
    
    db_query("DELETE FROM pending WHERE id = ?", (r_id,))
    await call.message.delete()
    await call.answer("Готово!")

@dp.callback_query(F.data == "stats")
async def stats_call(call: types.CallbackQuery):
    total = db_query("SELECT COUNT(*) FROM all_users", fetch=True)[0][0]
    in_top = db_query("SELECT COUNT(*) FROM users", fetch=True)[0][0]
    waiting = db_query("SELECT COUNT(*) FROM pending", fetch=True)[0][0]
    await call.message.answer(f"📊 **Статистика:**\n👥 Пользователей: {total}\n🏒 В топе: {in_top}\n⏳ Ожидают заявку: **{waiting}**"); await call.answer()

# --- СИСТЕМНОЕ ---
@dp.message(F.text == "🔄 Обновить эло")
async def req_upd(message: types.Message, state: FSMContext):
    await message.answer("📝 Введите НОВОЕ Эло:"); await state.set_state(UpdateScore.waiting_for_elo)

@dp.message(UpdateScore.waiting_for_elo)
async def proc_upd_data(message: types.Message, state: FSMContext):
    try:
        elo = int(message.text)
        await state.update_data(elo=elo)
        await message.answer("📸 Скриншот главного меню:"); await state.set_state(UpdateScore.waiting_for_photo)
    except: await message.answer("Введите число!")

@dp.message(UpdateScore.waiting_for_photo, F.photo)
async def proc_upd_photo(message: types.Message, state: FSMContext):
    data = await state.get_data()
    db_query("INSERT INTO pending (user_id, type, name, elo, photo_id) VALUES (?, ?, ?, ?, ?)", 
             (message.from_user.id, "UPDATE", "User", data['elo'], message.photo[-1].file_id))
    await message.answer("⏳ Запрос на обновление отправлен!"); await state.clear()

@dp.message(F.text.in_({"🏆 Топ 10", "🌟 Топ 100"}))
async def show_top(message: types.Message):
    limit = 10 if "10" in message.text else 100
    users = db_query("SELECT username, elo FROM users ORDER BY elo DESC LIMIT ?", (limit,), fetch=True)
    res = f"**{message.text}**\n\n" + ("Пока пусто." if not users else "\n".join([f"{i+1}. {u[0]} — `{u[1]}`" for i, u in enumerate(users)]))
    await message.answer(res, parse_mode="Markdown")

@dp.callback_query(F.data == "broadcast")
async def br_start(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("Введите текст рассылки:"); await state.set_state(BroadcastState.waiting_for_msg)

@dp.message(BroadcastState.waiting_for_msg)
async def br_send(message: types.Message, state: FSMContext):
    users = db_query("SELECT user_id FROM all_users", fetch=True)
    for u in users:
        try: await bot.send_message(u[0], f"📢 **РАССЫЛКА**\n\n{message.text}", parse_mode="Markdown")
        except: pass
    await message.answer("✅ Готово!"); await state.clear()

async def handle_ping(r): return web.Response(text="ok")
async def main():
    init_db()
    app = web.Application(); app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8080))).start()
    await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())

import os
import asyncio
import sqlite3
import logging
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiohttp import web

# --- НАСТРОЙКИ ---
TOKEN = os.getenv("BOT_TOKEN")
ADMINS = [8239542728]  # ВСТАВЬ СВОЙ ID ИЗ @userinfobot

bot = Bot(token=TOKEN)
dp = Dispatcher()
logging.basicConfig(level=logging.INFO)

# --- СОСТОЯНИЯ (FSM) ---
class RequestScore(StatesGroup):
    waiting_for_data = State()
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
    # Игроки (сортировка будет идти по полю elo DESC)
    db_query('''CREATE TABLE IF NOT EXISTS users 
                (id INTEGER PRIMARY KEY, username TEXT, elo INTEGER)''')
    # Для рассылки (храним всех ID)
    db_query('''CREATE TABLE IF NOT EXISTS all_users 
                (user_id INTEGER PRIMARY KEY)''')
    # Бан-лист
    db_query('''CREATE TABLE IF NOT EXISTS banned 
                (user_id INTEGER PRIMARY KEY)''')

# --- КЛАВИАТУРЫ ---
def get_main_kb(user_id):
    buttons = [
        [KeyboardButton(text="➕ Добавить аккаунт")],
        [KeyboardButton(text="🏆 Топ 10"), KeyboardButton(text="🌟 Топ 100")],
        [KeyboardButton(text="📊 Мой рейтинг"), KeyboardButton(text="👥 Сотрудники")]
    ]
    if user_id in ADMINS:
        buttons.append([KeyboardButton(text="⚙️ Админ Панель")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# --- ЛОГИКА БОТА ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    db_query("INSERT OR IGNORE INTO all_users (user_id) VALUES (?)", (user_id,))
    if db_query("SELECT 1 FROM banned WHERE user_id = ?", (user_id,), fetch=True): return
    await message.answer("🏒 Добро пожаловать! Используйте меню для навигации.", 
                         reply_markup=get_main_kb(user_id))

# ЗАЯВКА НА ДОБАВЛЕНИЕ
@dp.message(F.text == "➕ Добавить аккаунт")
async def start_req(message: types.Message, state: FSMContext):
    if db_query("SELECT 1 FROM banned WHERE user_id = ?", (message.from_user.id,), fetch=True): return
    await message.answer("📝 Введите ваш Ник и Эло через пробел.\nПример: `Guptek 7009`")
    await state.set_state(RequestScore.waiting_for_data)

@dp.message(RequestScore.waiting_for_data)
async def proc_data(message: types.Message, state: FSMContext):
    try:
        name, elo = message.text.split()[0], int(message.text.split()[1])
        await state.update_data(name=name, elo=elo)
        await message.answer("📸 Теперь отправьте скриншот профиля для подтверждения.")
        await state.set_state(RequestScore.waiting_for_photo)
    except:
        await message.answer("❌ Ошибка! Напишите ник и число. Пример: `Ivan 1200`")

@dp.message(RequestScore.waiting_for_photo, F.photo)
async def proc_photo(message: types.Message, state: FSMContext):
    data = await state.get_data()
    pid = message.photo[-1].file_id
    uid = message.from_user.id
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Одобрить", callback_data=f"ok_{data['name']}_{data['elo']}_{uid}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"no_{uid}")]
    ])
    
    for adm in ADMINS:
        await bot.send_photo(adm, pid, caption=f"🔔 Заявка!\nНик: {data['name']}\nЭло: {data['elo']}\nID: `{uid}`", reply_markup=kb, parse_mode="Markdown")
    await message.answer("⏳ Заявка отправлена! Ждите решения админа.")
    await state.clear()

# ОБРАБОТКА РЕШЕНИЙ АДМИНА
@dp.callback_query(F.data.startswith("ok_"))
async def approve(call: types.CallbackQuery):
    _, name, elo, uid = call.data.split("_")
    db_query("INSERT INTO users (username, elo) VALUES (?, ?)", (name, int(elo)))
    await bot.send_message(int(uid), f"✅ Ваша заявка одобрена! Вы добавлены в топ с {elo} эло.")
    await call.message.edit_caption(caption=call.message.caption + "\n\n🟢 ПРИНЯТО")

@dp.callback_query(F.data.startswith("no_"))
async def reject(call: types.CallbackQuery):
    uid = call.data.split("_")[1]
    await bot.send_message(int(uid), "❌ Ваша заявка была отклонена администрацией.")
    await call.message.edit_caption(caption=call.message.caption + "\n\n🔴 ОТКЛОНЕНО")

# ТОПЫ С АВТО-СОРТИРОВКОЙ
@dp.message(F.text.in_({"🏆 Топ 10", "🌟 Топ 100"}))
async def show_top(message: types.Message):
    limit = 10 if "10" in message.text else 100
    users = db_query("SELECT username, elo FROM users ORDER BY elo DESC LIMIT ?", (limit,), fetch=True)
    if not users: return await message.answer("Топ пока пуст.")
    
    res = f"{message.text}\n" + "—"*15 + "\n"
    for i, (name, elo) in enumerate(users, 1):
        res += f"{i}. {name} — **{elo}**\n"
    await message.answer(res, parse_mode="Markdown")

# РАССЫЛКА
@dp.message(F.text == "⚙️ Админ Панель")
async def adm_panel(message: types.Message):
    if message.from_user.id not in ADMINS: return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Сделать рассылку", callback_data="broadcast")],
        [InlineKeyboardButton(text="🚫 Бан по ID", callback_data="ban_hint")]
    ])
    await message.answer("🛠 Панель управления", reply_markup=kb)

@dp.callback_query(F.data == "broadcast")
async def start_br(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("📝 Отправьте текст сообщения для рассылки (можно с фото).")
    await state.set_state(BroadcastState.waiting_for_msg)

@dp.message(BroadcastState.waiting_for_msg)
async def run_br(message: types.Message, state: FSMContext):
    users = db_query("SELECT user_id FROM all_users", fetch=True)
    count = 0
    for u in users:
        try:
            await message.copy_to(u[0])
            count += 1
            await asyncio.sleep(0.05) # Защита от спам-фильтра ТГ
        except: pass
    await message.answer(f"✅ Рассылка завершена! Получили: {count} чел.")
    await state.clear()

# ОСТАЛЬНЫЕ КНОПКИ
@dp.message(F.text == "👥 Сотрудники")
async def show_staff(message: types.Message):
    await message.answer("👥 **Команда:**\n👑 HyperXHyper (Владелец)\n⚙️ Viperr (Админ)\n⚙️ Lenzy (Админ)", parse_mode="Markdown")

@dp.message(Command("ban"))
async def ban(message: types.Message):
    if message.from_user.id not in ADMINS: return
    uid = message.text.split()[1]
    db_query("INSERT OR IGNORE INTO banned (user_id) VALUES (?)", (int(uid),))
    await message.answer(f"🚫 Пользователь {uid} забанен.")

# ЗАПУСК
async def handle_ping(r): return web.Response(text="ok")
async def main():
    init_db()
    app = web.Application(); app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8080))).start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

import os
import asyncio
import sqlite3
import logging
import time
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import CommandStart, Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiohttp import web

# --- НАСТРОЙКА ЛОГИРОВАНИЯ ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("HockeyBot")

# --- КОНФИГУРАЦИЯ ---
TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = 8239542728 

bot = Bot(token=TOKEN)
dp = Dispatcher()

# ТЕКСТ ПРИВЕТСТВИЯ С ИДЕАЛЬНЫМ ЭКРАНИРОВАНИЕМ
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

# --- КЛАСС БАЗЫ ДАННЫХ (SQLITE3) ---
class Database:
    def __init__(self, db_path='hockey_elo.db'):
        self.db_path = db_path
        self._setup_db()

    def _setup_db(self):
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.cursor()
            cur.execute('''CREATE TABLE IF NOT EXISTS users 
                (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, elo INTEGER)''')
            cur.execute('''CREATE TABLE IF NOT EXISTS all_users (user_id INTEGER PRIMARY KEY)''')
            cur.execute('''CREATE TABLE IF NOT EXISTS staff 
                (user_id INTEGER PRIMARY KEY, name TEXT, role TEXT, elo INTEGER, last_seen INTEGER)''')
            cur.execute('''CREATE TABLE IF NOT EXISTS pending 
                (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, type TEXT, name TEXT, elo INTEGER, photo_id TEXT)''')
            
            # Авто-регистрация владельца
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

# --- MIDDLEWARE ДЛЯ ОБНОВЛЕНИЯ ОНЛАЙНА ---
class OnlineMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user:
            # Обновляем активность сотрудников при любом действии
            db.query("UPDATE staff SET last_seen = ? WHERE user_id = ?", (int(time.time()), user.id))
        return await handler(event, data)

dp.update.outer_middleware(OnlineMiddleware())

# --- ГЕНЕРАТОРЫ КЛАВИАТУР ---
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

# --- ОБРАБОТЧИКИ СООБЩЕНИЙ ---

@dp.message(CommandStart())
async def start_handler(message: types.Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    db.query("INSERT OR IGNORE INTO all_users (user_id) VALUES (?)", (uid,))
    await message.answer(WELCOME_TEXT, reply_markup=get_main_kb(uid), parse_mode="MarkdownV2")

@dp.message(F.text == "❌ Отменить действие")
async def cancel_handler(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("🛑 *Действие прервано\\.*", reply_markup=get_main_kb(message.from_user.id), parse_mode="MarkdownV2")

@dp.message(F.text == "👥 Сотрудники")
async def staff_list_handler(message: types.Message):
    staff_data = db.query("SELECT name, role, elo, last_seen FROM staff", fetch=True)
    response = "👥 *Команда проекта:*\n\n"
    now = int(time.time())
    
    for s in staff_data:
        name, role, elo, l_seen = s[0], s[1], s[2], (s[3] if s[3] else 0)
        diff = now - l_seen
        status = "🟢 онлайн" if diff < 300 else f"⚪ был {diff // 60} мин\\. назад"
        response += f"⚙️ *{name}* \\({elo} эло\\)\n└ {role} | {status}\n\n"
    
    await message.answer(response, parse_mode="MarkdownV2")

@dp.message(F.text == "📊 Мой рейтинг")
async def profile_handler(message: types.Message):
    u = db.query("SELECT username, elo FROM users WHERE user_id = ?", (message.from_user.id,), fetch=True)
    if not u:
        return await message.answer("❌ *Вас нет в базе данных\\.*", parse_mode="MarkdownV2")
    
    all_p = db.query("SELECT user_id FROM users ORDER BY elo DESC, id ASC", fetch=True)
    rank = next((i for i, (uid,) in enumerate(all_p, 1) if uid == message.from_user.id), "?")
    
    text = f"📊 *Ваш профиль:*\n\n👤 Ник: `{u[0][0]}`\n🏒 Эло: `{u[0][1]}`\n🏆 Место: *{rank}*"
    await message.answer(text, parse_mode="MarkdownV2")

# --- СИСТЕМА ЛИДЕРБОРДОВ ---

@dp.message(F.text == "🏆 Топ 10")
async def top10_handler(message: types.Message):
    data = db.query("SELECT username, elo FROM users ORDER BY elo DESC LIMIT 10", fetch=True)
    msg = "🏆 *Рейтинг Топ 10 игроков:*\n\n"
    if not data: msg += "_Список пуст\\._"
    else:
        for i, (n, e) in enumerate(data, 1):
            msg += f"{i}\\. `{n}` — *{e}*\n"
    await message.answer(msg, parse_mode="MarkdownV2")

@dp.message(F.text == "🌟 Топ 100")
async def top100_handler(message: types.Message):
    data = db.query("SELECT username, elo FROM users ORDER BY elo DESC LIMIT 100", fetch=True)
    msg = "🌟 *Лидеры Топ 100:*\n\n"
    if not data: msg += "_Список пуст\\._"
    else:
        for i, (n, e) in enumerate(data, 1):
            msg += f"{i}\\. `{n}` — *{e}*\n"
    await message.answer(msg, parse_mode="MarkdownV2")

# --- РЕГИСТРАЦИЯ И ОБНОВЛЕНИЕ ---

@dp.message(F.text == "➕ Добавить аккаунт")
async def add_acc_1(message: types.Message, state: FSMContext):
    await message.answer("📝 *Шаг 1 из 2*\nВведите Ник и Эло через пробел\\.\n\nНапр: `Guptek 5000`", 
                         reply_markup=get_cancel_kb(), parse_mode="MarkdownV2")
    await state.set_state(RegistrationState.input_data)

@dp.message(RegistrationState.input_data)
async def add_acc_2(message: types.Message, state: FSMContext):
    if message.text == "❌ Отменить действие": return
    try:
        parts = message.text.split()
        await state.update_data(n=parts[0], e=int(parts[1]))
        await message.answer("📸 *Шаг 2 из 2*\nПришлите скриншот для подтверждения\\.", parse_mode="MarkdownV2")
        await state.set_state(RegistrationState.input_photo)
    except:
        await message.answer("⚠️ *Ошибка\\!* Напишите: `Ник Число` через пробел\\.")

@dp.message(RegistrationState.input_photo, F.photo)
async def add_acc_3(message: types.Message, state: FSMContext):
    d = await state.get_data()
    db.query("INSERT INTO pending (user_id, type, name, elo, photo_id) VALUES (?, ?, ?, ?, ?)",
             (message.from_user.id, "NEW", d['n'], d['e'], message.photo[-1].file_id))
    await message.answer("⏳ *Заявка создана\\!* Ожидайте проверки\\.", 
                         reply_markup=get_main_kb(message.from_user.id), parse_mode="MarkdownV2")
    await state.clear()

@dp.message(F.text == "🔄 Обновить эло")
async def upd_elo_1(message: types.Message, state: FSMContext):
    u = db.query("SELECT username FROM users WHERE user_id = ?", (message.from_user.id,), fetch=True)
    if not u: return await message.answer("❌ *Вас нет в базе\\!*", parse_mode="MarkdownV2")
    await state.update_data(n=u[0][0])
    await message.answer(f"👤 Ник: *{u[0][0]}*\n\nВведите новое значение Эло:", 
                         reply_markup=get_cancel_kb(), parse_mode="MarkdownV2")
    await state.set_state(UpdateEloState.input_elo)

@dp.message(UpdateEloState.input_elo)
async def upd_elo_2(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("⚠️ *Введите только число\\!*")
    await state.update_data(e=int(message.text))
    await message.answer("📸 Пришлите новый скриншот подтверждения\\.", parse_mode="MarkdownV2")
    await state.set_state(UpdateEloState.input_photo)

@dp.message(UpdateEloState.input_photo, F.photo)
async def upd_elo_3(message: types.Message, state: FSMContext):
    d = await state.get_data()
    db.query("INSERT INTO pending (user_id, type, name, elo, photo_id) VALUES (?, ?, ?, ?, ?)",
             (message.from_user.id, "UPDATE", d['n'], d['e'], message.photo[-1].file_id))
    await message.answer("⏳ *Запрос на обновление отправлен\\!*", 
                         reply_markup=get_main_kb(message.from_user.id), parse_mode="MarkdownV2")
    await state.clear()

# --- ПАНЕЛЬ АДМИНИСТРАТОРА ---

@dp.message(F.text == "⚙️ Admin Panel")
async def admin_menu_handler(message: types.Message):
    is_s = db.query("SELECT 1 FROM staff WHERE user_id = ?", (message.from_user.id,), fetch=True)
    if not (message.from_user.id == OWNER_ID or is_s): return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📂 Посмотреть заявки", callback_data="v_req")],
        [InlineKeyboardButton(text="📊 Статистика бота", callback_data="v_stat")]
    ])
    await message.answer("🛠 *Панель администратора:*", reply_markup=kb, parse_mode="MarkdownV2")

@dp.callback_query(F.data == "v_req")
async def admin_view_requests(call: types.CallbackQuery):
    r = db.query("SELECT id, type, name, elo, photo_id FROM pending LIMIT 1", fetch=True)
    if not r: return await call.message.answer("🎉 *Заявок больше нет\\!*", parse_mode="MarkdownV2")
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"ok_{r[0][0]}"),
        InlineKeyboardButton(text="❌ Отказать", callback_data=f"no_{r[0][0]}")
    ]])
    caption = f"📦 *Заявка #{r[0][0]}* \\({r[0][1]}\\)\n👤 Ник: `{r[0][2]}`\n🏒 Эло: *{r[0][3]}*"
    await bot.send_photo(call.from_user.id, r[0][4], caption=caption, reply_markup=kb, parse_mode="MarkdownV2")
    await call.answer()

@dp.callback_query(F.data.startswith(("ok_", "no_")))
async def admin_process_request(call: types.CallbackQuery):
    act, rid = call.data.split("_")
    d = db.query("SELECT user_id, type, name, elo FROM pending WHERE id = ?", (int(rid),), fetch=True)
    if d and act == "ok":
        if d[0][1] == "NEW": db.query("INSERT INTO users (user_id, username, elo) VALUES (?, ?, ?)", (d[0][0], d[0][2], d[0][3]))
        else: db.query("UPDATE users SET elo = ? WHERE user_id = ?", (d[0][3], d[0][0]))
        try: await bot.send_message(d[0][0], f"✅ *Ваша заявка на {d[0][3]} Эло одобрена\\!*", parse_mode="MarkdownV2")
        except: pass
    db.query("DELETE FROM pending WHERE id = ?", (int(rid),))
    await call.message.delete()
    await call.answer("Выполнено!")

@dp.callback_query(F.data == "v_stat")
async def admin_show_stats(call: types.CallbackQuery):
    u = db.query("SELECT COUNT(*) FROM all_users", fetch=True)[0][0]
    t = db.query("SELECT COUNT(*) FROM users", fetch=True)[0][0]
    p = db.query("SELECT COUNT(*) FROM pending", fetch=True)[0][0]
    await call.message.answer(f"📊 *Статистика:*\n\nВсего юзеров: {u}\nВ рейтинге: {t}\nОчередь заявок: {p}", parse_mode="MarkdownV2")
    await call.answer()

@dp.message(Command("del"))
async def admin_delete_player(message: types.Message):
    if message.from_user.id != OWNER_ID: return
    name = message.text.replace("/del ", "").strip()
    db.query("DELETE FROM users WHERE username = ?", (name,))
    await message.answer(f"🗑 Игрок *{name}* успешно удален\\.", parse_mode="MarkdownV2")

# --- ЗАПУСК ВЕБ-СЕРВЕРА ДЛЯ RAILWAY ---
async def ping_handler(request): return web.Response(text="Bot is Alive")
async def main():
    app = web.Application(); app.router.add_get("/", ping_handler)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8080))).start()
    logger.info("Polling started...")
    await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())

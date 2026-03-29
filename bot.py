import os
import asyncio
import sqlite3
import logging
import time
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web

# --- 1. CONFIGURATION ---
TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = 8239542728 

# Настройка подробного логирования в консоль
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)
logger = logging.getLogger("HockeyBot")

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- 2. STATES (FSM) ---
class RegState(StatesGroup):
    input_data = State()
    upload_photo = State()

class UpdState(StatesGroup):
    input_elo = State()
    upload_photo = State()

class PostState(StatesGroup):
    input_text = State()

# --- 3. DATABASE ENGINE ---
DB_NAME = 'hockey_final_v30.db'

def db_execute(sql, params=(), fetch=False, fetchone=False):
    """Универсальный исполнитель запросов к БД"""
    try:
        with sqlite3.connect(DB_NAME) as conn:
            c = conn.cursor()
            c.execute(sql, params)
            if fetch: return c.fetchall()
            if fetchone: return c.fetchone()
            conn.commit()
    except Exception as e:
        logger.error(f"Database Error: {e}")
        return None

def init_db():
    """Создание структуры таблиц"""
    db_execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, elo INTEGER, reg_date TEXT)")
    db_execute("CREATE TABLE IF NOT EXISTS staff (user_id INTEGER PRIMARY KEY, name TEXT, role TEXT, last_seen INTEGER)")
    db_execute("CREATE TABLE IF NOT EXISTS queue (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, q_type TEXT, name TEXT, elo INTEGER, photo TEXT, created_at TEXT)")
    # Регистрация владельца
    db_execute("INSERT OR IGNORE INTO staff VALUES (?, ?, ?, ?)", (OWNER_ID, "orbsi", "Владелец", int(time.time())))
    logger.info("Database initialized successfully.")

# --- 4. KEYBOARDS ---
def get_main_kb(uid):
    """Главное меню"""
    is_adm = db_execute("SELECT 1 FROM staff WHERE user_id = ?", (uid,), fetchone=True)
    
    buttons = [
        [KeyboardButton(text="➕ Добавить аккаунт"), KeyboardButton(text="🔄 Обновить эло")],
        [KeyboardButton(text="🏆 Топ 10"), KeyboardButton(text="🌟 Топ 100")],
        [KeyboardButton(text="📊 Мой рейтинг"), KeyboardButton(text="👥 Сотрудники")]
    ]
    
    if uid == OWNER_ID or is_adm:
        buttons.append([KeyboardButton(text="⚙️ Админ-Панель")])
        
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

def get_cancel_kb():
    """Клавиатура отмены"""
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Отмена")]], resize_keyboard=True)

# --- 5. GLOBAL HANDLERS ---

@dp.message(F.text.casefold() == "❌ отмена")
async def global_cancel(message: types.Message, state: FSMContext):
    """Универсальная кнопка отмены для всех состояний"""
    current_state = await state.get_state()
    if current_state is not None:
        await state.clear()
        logger.info(f"User {message.from_user.id} cancelled state {current_state}")
    
    await message.answer(
        "🛑 Действие отменено. Возвращаюсь в главное меню.",
        reply_markup=get_main_kb(message.from_user.id)
    )

# --- 6. USER HANDLERS ---

@dp.message(CommandStart())
async def cmd_start(msg: types.Message, state: FSMContext):
    await state.clear()
    # Обновляем время захода админа
    db_execute("UPDATE staff SET last_seen = ? WHERE user_id = ?", (int(time.time()), msg.from_user.id))
    
    welcome_text = (
        "🏒 **Добро пожаловать в Лидерборд Три Кота Хоккей!**\n\n"
        "Я помогу тебе следить за рейтингом и соревноваться с лучшими игроками."
    )
    await msg.answer(welcome_text, reply_markup=get_main_kb(msg.from_user.id), parse_mode="Markdown")

@dp.message(F.text == "👥 Сотрудники")
async def cmd_staff(msg: types.Message):
    """Вывод списка администрации проекта"""
    logger.info(f"User {msg.from_user.id} requested staff list")
    data = db_execute("SELECT name, role, last_seen FROM staff", fetch=True)
    
    if not data:
        return await msg.answer("Список сотрудников пуст.")
        
    response = "👥 **Команда проекта Три Кота:**\n\n"
    now = int(time.time())
    
    for member in data:
        name, role, last_ts = member
        diff = now - last_ts
        
        # Красивое форматирование времени
        if diff < 60: ago = "только что"
        elif diff < 3600: ago = f"{diff // 60} мин. назад"
        elif diff < 86400: ago = f"{diff // 3600} ч. назад"
        else: ago = "давно"
            
        status_emoji = "🟢" if diff < 600 else "⚪"
        response += f"{status_emoji} **{name}** — {role}\n└ _Активность: {ago}_\n\n"
        
    await msg.answer(response, parse_mode="Markdown")

@dp.message(F.text == "🏆 Топ 10")
async def cmd_top10(msg: types.Message):
    users = db_execute("SELECT username, elo FROM users ORDER BY elo DESC LIMIT 10", fetch=True)
    if not users:
        return await msg.answer("Рейтинг пока пуст. Будь первым!")
    
    text = "🏆 **ТОП 10 ИГРОКОВ:**\n\n"
    for i, user in enumerate(users, 1):
        text += f"{i}. `{user[0]}` — **{user[1]}**\n"
    await msg.answer(text, parse_mode="Markdown")

@dp.message(F.text == "🌟 Топ 100")
async def cmd_top100(msg: types.Message):
    users = db_execute("SELECT username, elo FROM users ORDER BY elo DESC LIMIT 100", fetch=True)
    if not users:
        return await msg.answer("Рейтинг пока пуст.")
        
    text = "🌟 **ТОП 100 ИГРОКОВ:**\n\n"
    for i, user in enumerate(users, 1):
        text += f"{i}. `{user[0]}` — **{user[1]}**\n"
        # Чтобы не превысить лимит сообщения в 4096 символов
        if i % 50 == 0:
            await msg.answer(text, parse_mode="Markdown")
            text = ""
    if text:
        await msg.answer(text, parse_mode="Markdown")

@dp.message(F.text == "📊 Мой рейтинг")
async def cmd_my_rank(msg: types.Message):
    user = db_execute("SELECT username, elo FROM users WHERE user_id = ?", (msg.from_user.id,), fetchone=True)
    if not user:
        return await msg.answer("❌ Вы не зарегистрированы. Нажмите '➕ Добавить аккаунт'.")
        
    # Считаем место (количество людей, у которых эло больше)
    rank = db_execute("SELECT COUNT(*) FROM users WHERE elo > ?", (user[1],), fetchone=True)[0] + 1
    
    await msg.answer(
        f"📊 **Ваш профиль:**\n\n👤 Ник: `{user[0]}`\n🏒 Эло: **{user[1]}**\n🏆 Место в топе: **#{rank}**",
        parse_mode="Markdown"
    )

# --- 7. REGISTRATION LOGIC ---

@dp.message(F.text == "➕ Добавить аккаунт")
async def reg_step1(msg: types.Message, state: FSMContext):
    await msg.answer("📝 Введите ваш Ник и текущее Эло через пробел:\n(Например: `Cat_Pro 1500`)", 
                     reply_markup=get_cancel_kb(), parse_mode="Markdown")
    await state.set_state(RegState.input_data)

@dp.message(RegState.input_data)
async def reg_step2(msg: types.Message, state: FSMContext):
    try:
        parts = msg.text.split()
        if len(parts) < 2: raise ValueError
        nick, elo = parts[0], int(parts[1])
        
        await state.update_data(n=nick, e=elo)
        await msg.answer("📸 Отлично! Теперь пришлите скриншот вашего профиля для подтверждения:")
        await state.set_state(RegState.upload_photo)
    except:
        await msg.answer("⚠️ Неверный формат! Введите: `Ник Число` (через пробел).")

@dp.message(RegState.upload_photo, F.photo)
async def reg_step3(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    db_execute(
        "INSERT INTO queue (user_id, q_type, name, elo, photo, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (msg.from_user.id, "NEW", data['n'], data['e'], msg.photo[-1].file_id, now)
    )
    
    await msg.answer("⏳ Ваша заявка отправлена на проверку модераторам!", reply_markup=get_main_kb(msg.from_user.id))
    await state.clear()

# --- 8. UPDATE ELO LOGIC ---

@dp.message(F.text == "🔄 Обновить эло")
async def upd_step1(msg: types.Message, state: FSMContext):
    user = db_execute("SELECT 1 FROM users WHERE user_id = ?", (msg.from_user.id,), fetchone=True)
    if not user:
        return await msg.answer("❌ Сначала нужно зарегистрироваться!")
        
    await msg.answer("🔄 Введите ваше новое значение Эло (только число):", reply_markup=get_cancel_kb())
    await state.set_state(UpdState.input_elo)

@dp.message(UpdState.input_elo)
async def upd_step2(msg: types.Message, state: FSMContext):
    if not msg.text.isdigit():
        return await msg.answer("Пожалуйста, введите целое число.")
        
    await state.update_data(new_e=int(msg.text))
    await msg.answer("📸 Теперь пришлите скриншот подтверждения:")
    await state.set_state(UpdState.upload_photo)

@dp.message(UpdState.upload_photo, F.photo)
async def upd_step3(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    user_info = db_execute("SELECT username FROM users WHERE user_id = ?", (msg.from_user.id,), fetchone=True)
    
    db_execute(
        "INSERT INTO queue (user_id, q_type, name, elo, photo, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (msg.from_user.id, "UPDATE", user_info[0], data['new_e'], msg.photo[-1].file_id, datetime.now().strftime("%H:%M"))
    )
    
    await msg.answer("✅ Заявка на обновление Эло передана админам!", reply_markup=get_main_kb(msg.from_user.id))
    await state.clear()

# --- 9. ADMIN FUNCTIONS ---

@dp.message(F.text == "⚙️ Админ-Панель")
async def admin_main(msg: types.Message):
    is_adm = db_execute("SELECT 1 FROM staff WHERE user_id = ?", (msg.from_user.id,), fetchone=True)
    if msg.from_user.id != OWNER_ID and not is_adm: return
    
    # Обновляем активность
    db_execute("UPDATE staff SET last_seen = ? WHERE user_id = ?", (int(time.time()), msg.from_user.id))
    
    q_count = db_execute("SELECT COUNT(*) FROM queue", fetchone=True)[0]
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📂 Заявки ({q_count})", callback_data="admin_view_q")],
        [InlineKeyboardButton(text="📢 Сделать рассылку", callback_data="admin_broadcast")]
    ])
    
    await msg.answer("🛠 **Панель модератора**\nВыберите действие:", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "admin_broadcast")
async def admin_post_start(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("📢 Введите текст сообщения для рассылки всем игрокам:", reply_markup=get_cancel_kb())
    await state.set_state(PostState.input_text)
    await call.answer()

@dp.message(PostState.input_text)
async def admin_post_run(msg: types.Message, state: FSMContext):
    # Берем всех, кто есть в базе
    all_users = db_execute("SELECT user_id FROM users", fetch=True)
    if not all_users:
        await msg.answer("Ошибка: в базе данных 0 игроков.")
        return await state.clear()

    sent = 0
    await msg.answer(f"🚀 Начинаю рассылку на {len(all_users)} чел...")
    
    for u in all_users:
        try:
            await bot.send_message(u[0], f"📢 **Сообщение от администрации:**\n\n{msg.text}", parse_mode="Markdown")
            sent += 1
            await asyncio.sleep(0.05) # Пауза для избежания бана
        except: continue
        
    await msg.answer(f"🏁 Рассылка завершена! Доставлено: {sent} чел.", reply_markup=get_main_kb(msg.from_user.id))
    await state.clear()

@dp.callback_query(F.data == "admin_view_q")
async def admin_view_requests(call: types.CallbackQuery):
    req = db_execute("SELECT id, q_type, name, elo, photo FROM queue LIMIT 1", fetch=True)
    if not req:
        return await call.message.answer("📦 Заявок нет.")
    
    r_id, r_type, r_name, r_elo, r_photo = req[0]
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data=f"adm_y_{r_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"adm_n_{r_id}")
    ]])
    
    cap = f"🆔 Заявка #{r_id}\n👤 Ник: {r_name}\n🏒 Эло: {r_elo}\n📝 Тип: {r_type}"
    
    try:
        await bot.send_photo(call.from_user.id, r_photo, caption=cap, reply_markup=kb)
    except:
        await bot.send_message(call.from_user.id, f"{cap}\n⚠️ Ошибка отображения фото", reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data.startswith("adm_"))
async def admin_decision(call: types.CallbackQuery):
    _, action, req_id = call.data.split("_")
    
    data = db_execute("SELECT user_id, q_type, name, elo FROM queue WHERE id = ?", (req_id,), fetchone=True)
    if data:
        uid, qtype, name, elo = data
        if action == "y":
            db_execute("INSERT OR REPLACE INTO users (user_id, username, elo, reg_date) VALUES (?, ?, ?, ?)", 
                       (uid, name, elo, datetime.now().strftime("%d.%m.%Y")))
            await bot.send_message(uid, f"✅ Ваша заявка одобрена!\nНик: {name}, Эло: {elo}")
        else:
            await bot.send_message(uid, "❌ К сожалению, ваша заявка была отклонена модератором.")
            
    db_execute("DELETE FROM queue WHERE id = ?", (req_id,))
    await call.message.delete()
    await admin_view_requests(call)

# --- 10. BOT RUNTIME ---

async def handle_ping(request):
    return web.Response(text="Bot is running!")

async def main():
    init_db()
    # Фоновый сервер для Render/Heroku
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8080).start()
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot execution interrupted.")

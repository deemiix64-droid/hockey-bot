import os
import asyncio
import sqlite3
import logging
import time
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    ReplyKeyboardRemove
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web

# --- 1. CONFIGURATION ---
TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = 8239542728 

# Настройка логирования для отладки
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s"
)
logger = logging.getLogger("HockeyBot")

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# --- MiddleWare для отслеживания активности ---
@dp.message.outer_middleware()
async def track_staff_activity(handler, event: types.Message, data: dict):
    """Обновляет время последнего визита, если пишет сотрудник или владелец"""
    db_execute("UPDATE staff SET last_seen = ? WHERE user_id = ?", (int(time.time()), event.from_user.id))
    return await handler(event, data)

# --- 2. FSM STATES ---
class RegState(StatesGroup):
    input_data = State()
    upload_photo = State()

class UpdState(StatesGroup):
    input_elo = State()
    upload_photo = State()

class PostState(StatesGroup):
    input_text = State()

# --- 3. DATABASE ENGINE ---
DB_NAME = 'hockey_final_v33.db'

def db_execute(sql, params=(), fetch=False, fetchone=False):
    """Универсальная функция для работы с БД с обработкой исключений"""
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
    """Создание всех необходимых таблиц при запуске"""
    db_execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, 
        username TEXT, 
        elo INTEGER, 
        reg_date TEXT
    )""")
    db_execute("""CREATE TABLE IF NOT EXISTS staff (
        user_id INTEGER PRIMARY KEY, 
        name TEXT, 
        role TEXT, 
        last_seen INTEGER
    )""")
    db_execute("""CREATE TABLE IF NOT EXISTS queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT, 
        user_id INTEGER, 
        q_type TEXT, 
        name TEXT, 
        elo INTEGER, 
        photo TEXT, 
        created_at TEXT
    )""")
    # Авто-регистрация владельца
    db_execute("INSERT OR IGNORE INTO staff VALUES (?, ?, ?, ?)", (OWNER_ID, "orbsi", "Владелец", int(time.time())))
    logger.info("Database initialized successfully.")

# --- НОВАЯ ФУНКЦИЯ: УВЕДОМЛЕНИЕ АДМИНОВ ---
async def notify_admins_about_request(text):
    """Функция рассылки уведомления всем админам"""
    staff_members = db_execute("SELECT user_id FROM staff", fetch=True)
    if staff_members:
        # Кнопка для быстрого перехода к списку заявок
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📂 Посмотреть заявки", callback_data="adm_requests")]
        ])
        for staff in staff_members:
            try:
                await bot.send_message(
                    staff[0], 
                    f"🔔 **Новая заявка в очереди!**\n\n{text}", 
                    parse_mode="Markdown",
                    reply_markup=kb
                )
            except Exception as e:
                logger.error(f"Не удалось отправить уведомление админу {staff[0]}: {e}")

# --- 4. KEYBOARDS ---
def get_main_kb(uid):
    """Динамическая клавиатура: кнопки админа видны только персоналу"""
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
    """Клавиатура для выхода из любого состояния"""
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Отмена")]], resize_keyboard=True)

# --- 5. GLOBAL COMMANDS (/del & /add_staff) ---

@dp.message(Command("del"))
async def cmd_delete_player(msg: types.Message):
    """Удаление игрока: /del [Ник или ID]"""
    if msg.from_user.id != OWNER_ID:
        return
    
    args = msg.text.split()
    if len(args) < 2:
        return await msg.answer("⚠️ Использование: `/del Ник` или `/del 12345`", parse_mode="Markdown")
    
    target = args[1]
    
    # Проверка наличия игрока перед удалением
    exists = None
    if target.isdigit():
        exists = db_execute("SELECT username FROM users WHERE user_id = ?", (int(target),), fetchone=True)
    else:
        exists = db_execute("SELECT user_id FROM users WHERE username = ?", (target,), fetchone=True)
        
    if not exists:
        return await msg.answer(f"❌ Игрок **{target}** не найден в базе.", parse_mode="Markdown")

    if target.isdigit():
        db_execute("DELETE FROM users WHERE user_id = ?", (int(target),))
    else:
        db_execute("DELETE FROM users WHERE username = ?", (target,))
    
    logger.info(f"Owner deleted user: {target}")
    await msg.answer(f"🗑 Игрок **{target}** полностью удален из базы.", parse_mode="Markdown")

@dp.message(Command("add_staff"))
async def cmd_add_staff(msg: types.Message):
    """Назначение админа: /add_staff [ID] [Имя] [Роль]"""
    if msg.from_user.id != OWNER_ID:
        return
    
    try:
        args = msg.text.split(maxsplit=3)
        user_id = int(args[1])
        name = args[2]
        role = args[3]
        
        db_execute("INSERT OR REPLACE INTO staff VALUES (?, ?, ?, ?)", (user_id, name, role, int(time.time())))
        await msg.answer(f"✅ Пользователь `{user_id}` назначен: **{name}** ({role})", parse_mode="Markdown")
    except Exception as e:
        await msg.answer("⚠️ Формат: `/add_staff ID Имя Роль`")

# --- 6. CORE HANDLERS ---

@dp.message(F.text.casefold() == "❌ отмена")
async def process_cancel(message: types.Message, state: FSMContext):
    """Сброс любого состояния FSM"""
    await state.clear()
    await message.answer("🛑 Действие прервано.", reply_markup=get_main_kb(message.from_user.id))

@dp.message(CommandStart())
async def process_start(msg: types.Message, state: FSMContext):
    await state.clear()
    
    text = "🏒 **Лидерборд Три Кота Хоккей**\n\nВыбирай действие на кнопках ниже:"
    await msg.answer(text, reply_markup=get_main_kb(msg.from_user.id), parse_mode="Markdown")

@dp.message(F.text == "👥 Сотрудники")
async def process_staff_list(msg: types.Message):
    """Вывод администрации с проверкой онлайна"""
    data = db_execute("SELECT name, role, last_seen FROM staff", fetch=True)
    if not data: return await msg.answer("Список пуст.")
    
    output = "👥 **Сотрудники этого бота:**\n\n"
    now = int(time.time())
    
    for member in data:
        name, role, ts = member
        diff = now - ts
        if diff < 60: ago = "только что"
        elif diff < 3600: ago = f"{diff//60} мин. назад"
        elif diff < 86400: ago = f"{diff//3600} ч. назад"
        else: ago = "давно"
            
        status = "🟢" if diff < 600 else "⚪"
        output += f"{status} **{name}** — {role}\n└ _Активность: {ago}_\n\n"
        
    await msg.answer(output, parse_mode="Markdown")

@dp.message(F.text == "🏆 Топ 10")
async def process_top10(msg: types.Message):
    users = db_execute("SELECT username, elo FROM users ORDER BY elo DESC LIMIT 10", fetch=True)
    if not users: return await msg.answer("Рейтинг пуст.")
    
    res = "🏆 **ТОП 10 ИГРОКОВ:**\n\n"
    for i, u in enumerate(users, 1):
        res += f"{i}. `{u[0]}` — **{u[1]}**\n"
    await msg.answer(res, parse_mode="Markdown")

@dp.message(F.text == "🌟 Топ 100")
async def process_top100(msg: types.Message):
    users = db_execute("SELECT username, elo FROM users ORDER BY elo DESC LIMIT 100", fetch=True)
    if not users: return await msg.answer("Топ пока пуст.")
    
    res = "🌟 **ТОП 100 ИГРОКОВ:**\n\n"
    for i, u in enumerate(users, 1):
        res += f"{i}. `{u[0]}` — {u[1]}\n"
        if i % 50 == 0:
            await msg.answer(res, parse_mode="Markdown")
            res = ""
    if res: await msg.answer(res, parse_mode="Markdown")

@dp.message(F.text == "📊 Мой рейтинг")
async def process_my_rank(msg: types.Message):
    u = db_execute("SELECT username, elo FROM users WHERE user_id = ?", (msg.from_user.id,), fetchone=True)
    if not u: return await msg.answer("❌ Вы не в базе. Пройдите регистрацию.")
    
    rank = db_execute("SELECT COUNT(*) FROM users WHERE elo > ?", (u[1],), fetchone=True)[0] + 1
    await msg.answer(f"📊 **Ваш профиль:**\n\nНик: `{u[0]}`\nЭло: **{u[1]}**\nМесто: **#{rank}**", parse_mode="Markdown")

# --- 7. LOGIC: ADD ACCOUNT ---

@dp.message(F.text == "➕ Добавить аккаунт")
async def reg_1(msg: types.Message, state: FSMContext):
    await msg.answer(
        "📝 **Введите ваш Ник и Эло через пробел.**\n\n"
        "Например: `Gamer 1200`\n", 
        reply_markup=get_cancel_kb(), 
        parse_mode="Markdown"
    )
    await state.set_state(RegState.input_data)

@dp.message(RegState.input_data)
async def reg_2(msg: types.Message, state: FSMContext):
    try:
        data = msg.text.split()
        if len(data) < 2: raise ValueError
        nick, elo = data[0], int(data[1])
        await state.update_data(n=nick, e=elo)
        await msg.answer("📸 Теперь отправьте скриншот вашего профиля:")
        await state.set_state(RegState.upload_photo)
    except:
        await msg.answer("⚠️ Неверный формат! Нужно: `Ник Число`.")

@dp.message(RegState.upload_photo, F.photo)
async def reg_3(msg: types.Message, state: FSMContext):
    d = await state.get_data()
    db_execute("INSERT INTO queue (user_id, q_type, name, elo, photo, created_at) VALUES (?, 'NEW', ?, ?, ?, ?)",
               (msg.from_user.id, d['n'], d['e'], msg.photo[-1].file_id, datetime.now().strftime("%d.%m %H:%M")))
    
    await msg.answer("⏳ Заявка на проверке модератором!", reply_markup=get_main_kb(msg.from_user.id))
    
    # ОТПРАВЛЯЕМ УВЕДОМЛЕНИЕ
    await notify_admins_about_request(f"Тип: 🆕 РЕГИСТРАЦИЯ\nНик: `{d['n']}`\nЭло: `{d['e']}`")
    
    await state.clear()

# --- 8. LOGIC: UPDATE ELO ---

@dp.message(F.text == "🔄 Обновить эло")
async def upd_1(msg: types.Message, state: FSMContext):
    if not db_execute("SELECT 1 FROM users WHERE user_id = ?", (msg.from_user.id,), fetchone=True):
        return await msg.answer("❌ Сначала зарегистрируйтесь!")
    await msg.answer("🔄 Введите новое значение Эло:", reply_markup=get_cancel_kb())
    await state.set_state(UpdState.input_elo)

@dp.message(UpdState.input_elo)
async def upd_2(msg: types.Message, state: FSMContext):
    if not msg.text.isdigit(): return await msg.answer("Введите только число!")
    await state.update_data(new_e=int(msg.text))
    await msg.answer("📸 Скриншот для подтверждения нового Эло:"); await state.set_state(UpdState.upload_photo)

@dp.message(UpdState.upload_photo, F.photo)
async def upd_3(msg: types.Message, state: FSMContext):
    d = await state.get_data()
    u = db_execute("SELECT username FROM users WHERE user_id = ?", (msg.from_user.id,), fetchone=True)
    db_execute("INSERT INTO queue (user_id, q_type, name, elo, photo, created_at) VALUES (?, 'UPDATE', ?, ?, ?, ?)",
               (msg.from_user.id, u[0], d['new_e'], msg.photo[-1].file_id, "UPD"))
    
    await msg.answer("✅ Заявка на обновление отправлена!", reply_markup=get_main_kb(msg.from_user.id))
    
    # ОТПРАВЛЯЕМ УВЕДОМЛЕНИЕ
    await notify_admins_about_request(f"Тип: 🔄 ОБНОВЛЕНИЕ\nНик: `{u[0]}`\nНовое Эло: `{d['new_e']}`")
    
    await state.clear()

# --- 9. ADMIN PANEL & BROADCAST ---

@dp.message(F.text == "⚙️ Админ-Панель")
async def admin_menu(msg: types.Message):
    is_staff = db_execute("SELECT 1 FROM staff WHERE user_id = ?", (msg.from_user.id,), fetchone=True)
    if msg.from_user.id != OWNER_ID and not is_staff: return
    
    q_count = db_execute("SELECT COUNT(*) FROM queue", fetchone=True)[0]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"📂 Заявки ({q_count})", callback_data="adm_requests")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="adm_post")]
    ])
    await msg.answer("🛠 **Панель управления**", reply_markup=kb, parse_mode="Markdown")

@dp.callback_query(F.data == "adm_post")
async def broadcast_1(call: types.CallbackQuery, state: FSMContext):
    await call.message.answer("📢 Введите текст сообщения для всех игроков:", reply_markup=get_cancel_kb())
    await state.set_state(PostState.input_text); await call.answer()

@dp.message(PostState.input_text)
async def broadcast_2(msg: types.Message, state: FSMContext):
    players = db_execute("SELECT user_id FROM users", fetch=True)
    if not players: return await msg.answer("База игроков пуста.")
    
    count = 0
    await msg.answer(f"🚀 Рассылка на {len(players)} чел. начата...")
    for p in players:
        try:
            await bot.send_message(p[0], f"📢 **Сообщение от администрации:**\n\n{msg.text}", parse_mode="Markdown")
            count += 1; await asyncio.sleep(0.05)
        except: continue
    await msg.answer(f"✅ Рассылка завершена. Получили: {count} игроков.", reply_markup=get_main_kb(msg.from_user.id))
    await state.clear()

@dp.callback_query(F.data == "adm_requests")
async def view_requests(call: types.CallbackQuery):
    req = db_execute("SELECT id, q_type, name, elo, photo FROM queue LIMIT 1", fetch=True)
    if not req: return await call.message.answer("📦 Очередь заявок пуста.")
    
    r_id, r_type, r_name, r_elo, r_photo = req[0]
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data=f"res_y_{r_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"res_n_{r_id}")
    ]])
    caption = f"Заявка #{r_id}\n👤 Ник: {r_name}\n🏒 Эло: {r_elo}\n📝 Тип: {r_type}"
    try: await bot.send_photo(call.from_user.id, r_photo, caption=caption, reply_markup=kb)
    except: await bot.send_message(call.from_user.id, caption + "\n⚠️ Ошибка фото", reply_markup=kb)
    await call.answer()

@dp.callback_query(F.data.startswith("res_"))
async def handle_decision(call: types.CallbackQuery):
    _, action, req_id = call.data.split("_")
    data = db_execute("SELECT user_id, q_type, name, elo FROM queue WHERE id = ?", (req_id,), fetchone=True)
    
    if data:
        uid, qtype, name, elo = data
        if action == "y":
            db_execute("INSERT OR REPLACE INTO users VALUES (?, ?, ?, ?)", 
                       (uid, name, elo, datetime.now().strftime("%d.%m.%Y")))
            await bot.send_message(uid, f"✅ Ваша заявка одобрена!\nНик: {name}, Эло: {elo}")
        else:
            await bot.send_message(uid, "❌ Ваша заявка была отклонена.")
            
    db_execute("DELETE FROM queue WHERE id = ?", (req_id,))
    await call.message.delete()
    await view_requests(call)

# --- 10. RUNTIME ---
async def handle_web(request):
    return web.Response(text="Bot is Active")

async def main():
    init_db()
    # Сервер для поддержки активности на хостингах
    app = web.Application()
    app.router.add_get("/", handle_web)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8080).start()
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")

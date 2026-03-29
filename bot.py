import os
import asyncio
import sqlite3
import logging
import time
import shutil
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import CommandStart, Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiohttp import web

# --- 1. ГЛОБАЛЬНОЕ ЛОГИРОВАНИЕ ---
# Мы пишем всё в файл hockey_system.log, чтобы при любой ошибке ты знал причину.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(name)s: %(message)s",
    handlers=[logging.FileHandler("hockey_system.log"), logging.StreamHandler()]
)
logger = logging.getLogger("HockeySystem")

# --- 2. ИНИЦИАЛИЗАЦИЯ ---
TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = 8239542728 # Твой ID владельца

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Текстовые константы
WELCOME_TEXT = (
    "🏒 *Добро пожаловать в Лидерборд Три Кота Хоккей\\!*\n\n"
    "Следи за своим рейтингом и стань лучшим игроком\\!\n\n"
    "⚠️ *Отказ от ответственности:*\n"
    "Бот создан фанатами\\. Данные обновляются после проверки\\."
)

# --- 3. СОСТОЯНИЯ (FSM) ---
class RegState(StatesGroup):
    """Цепочка состояний для регистрации нового игрока"""
    data = State()   # Ожидание Ника и Эло
    photo = State()  # Ожидание скриншота

class UpdState(StatesGroup):
    """Цепочка состояний для обновления существующего Эло"""
    elo = State()    # Ожидание нового числа
    photo = State()  # Ожидание нового скриншота

# --- 4. ENGINE БАЗЫ ДАННЫХ ---
class DatabaseManager:
    """Класс для управления всеми операциями с БД SQLite3"""
    def __init__(self, db_path='hockey_leader.db'):
        self.db_path = db_path
        self._initialize_db()
        self._create_backup()

    def _initialize_db(self):
        """Создает таблицы и регистрирует владельца при первом запуске"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                c = conn.cursor()
                # Основная таблица лидеров
                c.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, elo INTEGER)")
                # Общая статистика заходов
                c.execute("CREATE TABLE IF NOT EXISTS stats_all (user_id INTEGER PRIMARY KEY)")
                # Список администрации (Staff)
                c.execute("CREATE TABLE IF NOT EXISTS staff (user_id INTEGER PRIMARY KEY, name TEXT, role TEXT, elo INTEGER, last_seen INTEGER)")
                # Очередь заявок на модерацию
                c.execute("CREATE TABLE IF NOT EXISTS queue (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, q_type TEXT, name TEXT, elo INTEGER, photo TEXT)")
                
                # Регистрация Владельца (тебя)
                c.execute("INSERT OR IGNORE INTO staff (user_id, name, role, elo, last_seen) VALUES (?, ?, ?, ?, ?)", 
                          (OWNER_ID, "orbsi", "Владелец", 4002, int(time.time())))
                conn.commit()
                logger.info("Database core successfully initialized.")
        except sqlite3.Error as e:
            logger.error(f"Failed to initialize database: {e}")

    def _create_backup(self):
        """Создает резервную копию базы данных для защиты данных"""
        if os.path.exists(self.db_path):
            shutil.copy2(self.db_path, f"{self.db_path}.backup")
            logger.info("Daily backup file created.")

    def run_query(self, sql, params=(), is_fetch=False):
        """Безопасное выполнение SQL запросов с автоматическим закрытием соединения"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(sql, params)
                data = cursor.fetchall() if is_fetch else None
                conn.commit()
                return data
        except Exception as err:
            logger.error(f"Database error on query [{sql}]: {err}")
            return []

db = DatabaseManager()

# --- 5. КЛАВИАТУРЫ ---
def get_main_menu(uid):
    """Генерирует адаптивное меню в зависимости от прав доступа юзера"""
    is_staff = db.run_query("SELECT 1 FROM staff WHERE user_id = ?", (uid,), is_fetch=True)
    rows = [
        [KeyboardButton(text="➕ Добавить аккаунт"), KeyboardButton(text="🔄 Обновить эло")],
        [KeyboardButton(text="🏆 Топ 10"), KeyboardButton(text="🌟 Топ 100")],
        [KeyboardButton(text="📊 Мой рейтинг"), KeyboardButton(text="👥 Сотрудники")]
    ]
    if uid == OWNER_ID or is_staff:
        rows.append([KeyboardButton(text="⚙️ Admin Panel")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

def get_cancel_menu():
    """Кнопка для прерывания любого процесса ввода данных"""
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Отменить действие")]], resize_keyboard=True)

# --- 6. МОНИТОРИНГ ОНЛАЙНА (MIDDLEWARE) ---
class ActivityTracker(BaseMiddleware):
    """Обновляет время последнего визита персонала при каждом сообщении"""
    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user:
            db.run_query("UPDATE staff SET last_seen = ? WHERE user_id = ?", (int(time.time()), user.id))
        return await handler(event, data)

dp.update.outer_middleware(ActivityTracker())

# --- 7. ПУБЛИЧНЫЕ ХЕНДЛЕРЫ ---

@dp.message(CommandStart())
async def start_handler(msg: types.Message, state: FSMContext):
    await state.clear()
    db.run_query("INSERT OR IGNORE INTO stats_all (user_id) VALUES (?)", (msg.from_user.id,))
    await msg.answer(WELCOME_TEXT, reply_markup=get_main_menu(msg.from_user.id), parse_mode="MarkdownV2")

@dp.message(F.text.contains("Отменить"))
async def cancel_handler(msg: types.Message, state: FSMContext):
    await state.clear()
    await msg.answer("🛑 *Операция отменена административно\\.*", reply_markup=get_main_menu(msg.from_user.id), parse_mode="MarkdownV2")

@dp.message(F.text.contains("Сотрудники"))
async def staff_list_handler(msg: types.Message):
    staff_data = db.run_query("SELECT name, role, elo, last_seen FROM staff", is_fetch=True)
    text = "👥 *Команда Лидерборда:*\n\n"
    now = int(time.time())
    for s in staff_data:
        # Условие онлайна: активность за последние 5 минут
        status = "🟢 онлайн" if (now - (s[3] or 0)) < 300 else "⚪ оффлайн"
        text += f"⚙️ *{s[0]}* — {s[1]}\n└ {status} | {s[2]} эло\n\n"
    await msg.answer(text, parse_mode="MarkdownV2")

@dp.message(F.text.contains("Мой рейтинг"))
async def personal_stats_handler(msg: types.Message):
    u = db.run_query("SELECT username, elo FROM users WHERE user_id = ?", (msg.from_user.id,), is_fetch=True)
    if not u: return await msg.answer("❌ *Вас нет в базе\\. Зарегистрируйтесь\\!*", parse_mode="MarkdownV2")
    
    # Считаем позицию через сортировку всей базы
    all_users = db.run_query("SELECT user_id FROM users ORDER BY elo DESC, user_id ASC", is_fetch=True)
    rank = next((i for i, (uid,) in enumerate(all_users, 1) if uid == msg.from_user.id), "?")
    
    await msg.answer(f"📊 *Ваша статистика:*\n\nНик: `{u[0][0]}`\nЭло: `{u[0][1]}`\nМесто в ТОПе: *{rank}*", parse_mode="MarkdownV2")

@dp.message(F.text.contains("Топ 100"))
async def top100_handler(msg: types.Message):
    data = db.run_query("SELECT username, elo FROM users ORDER BY elo DESC LIMIT 100", is_fetch=True)
    out = "🌟 *Топ 100 лучших игроков:*\n\n"
    if not data: out += "_Список пока пуст\\._"
    else:
        for i, (name, elo) in enumerate(data, 1):
            out += f"{i}\\. `{name}` — *{elo}*\n"
    await msg.answer(out, parse_mode="MarkdownV2")

# --- 8. ЛОГИКА ЗАЯВОК ---

@dp.message(F.text.contains("Добавить аккаунт"))
async def process_reg_1(msg: types.Message, state: FSMContext):
    await msg.answer("📝 Введите Ваш Ник и Эло через пробел (Пример: Gamer 1500):", reply_markup=get_cancel_menu())
    await state.set_state(RegState.data)

@dp.message(RegState.data)
async def process_reg_2(msg: types.Message, state: FSMContext):
    try:
        parts = msg.text.split()
        if len(parts) < 2: raise ValueError
        await state.update_data(n=parts[0], e=int(parts[1]))
        await msg.answer("📸 Теперь пришлите скриншот Вашего результата в игре:")
        await state.set_state(RegState.photo)
    except:
        await msg.answer("⚠️ Ошибка! Напишите данные правильно: `Ник Число`.")

@dp.message(RegState.photo, F.photo)
async def process_reg_3(msg: types.Message, state: FSMContext):
    d = await state.get_data()
    db.run_query("INSERT INTO queue (user_id, q_type, name, elo, photo) VALUES (?, 'NEW', ?, ?, ?)", 
                 (msg.from_user.id, d['n'], d['e'], msg.photo[-1].file_id))
    await msg.answer("⏳ Заявка на регистрацию создана! Ждите проверки.", reply_markup=get_main_menu(msg.from_user.id))
    await state.clear()

@dp.message(F.text.contains("Обновить эло"))
async def process_upd_1(msg: types.Message, state: FSMContext):
    u = db.run_query("SELECT username FROM users WHERE user_id = ?", (msg.from_user.id,), is_fetch=True)
    if not u: return await msg.answer("❌ Вы еще не зарегистрированы в системе!")
    await state.update_data(n=u[0][0])
    await msg.answer(f"👤 Ваш ник: {u[0][0]}\nВведите Ваше новое количество Эло:", reply_markup=get_cancel_menu())
    await state.set_state(UpdState.elo)

@dp.message(UpdState.elo)
async def process_upd_2(msg: types.Message, state: FSMContext):
    if not msg.text.isdigit(): return await msg.answer("⚠️ Пожалуйста, введите только число.")
    await state.update_data(e=int(msg.text))
    await msg.answer("📸 Пришлите новый скриншот для подтверждения:")
    await state.set_state(UpdState.photo)

@dp.message(UpdState.photo, F.photo)
async def process_upd_3(msg: types.Message, state: FSMContext):
    d = await state.get_data()
    db.run_query("INSERT INTO queue (user_id, q_type, name, elo, photo) VALUES (?, 'UPD', ?, ?, ?)", 
                 (msg.from_user.id, d['n'], d['e'], msg.photo[-1].file_id))
    await msg.answer("⏳ Запрос на обновление данных отправлен!", reply_markup=get_main_menu(msg.from_user.id))
    await state.clear()

# --- 9. ADMIN PANEL & MODERATION ---

@dp.message(F.text.contains("Admin Panel"))
async def admin_menu_handler(msg: types.Message):
    is_staff = db.run_query("SELECT 1 FROM staff WHERE user_id = ?", (msg.from_user.id,), is_fetch=True)
    if not (msg.from_user.id == OWNER_ID or is_staff): return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📂 Показать заявку", callback_data="admin_view")],
        [InlineKeyboardButton(text="📊 Статистика бота", callback_data="admin_stats")]
    ])
    await msg.answer("🛠 *Панель управления администратора:*", reply_markup=kb, parse_mode="MarkdownV2")

@dp.callback_query(F.data == "admin_view")
async def admin_view_queue(call: types.CallbackQuery):
    """Извлекает первую заявку из очереди и выводит её админу"""
    q = db.run_query("SELECT id, q_type, name, elo, photo FROM queue LIMIT 1", is_fetch=True)
    if not q: return await call.message.answer("🎉 *Заявки закончились\\! Очередь пуста\\.*", parse_mode="MarkdownV2")
    
    btns = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"adm_y_{q[0][0]}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"adm_n_{q[0][0]}")
    ]])
    
    caption = f"📦 *Заявка #{q[0][0]}* ({q[0][1]})\n👤 Ник: `{q[0][2]}`\n🏒 Эло: *{q[0][3]}*"
    try:
        if q[0][4] and q[0][4] != "None":
            await bot.send_photo(call.from_user.id, q[0][4], caption=caption, reply_markup=btns, parse_mode="MarkdownV2")
        else:
            await call.message.answer(f"{caption}\n\n⚠️ _Скриншот отсутствует_", reply_markup=btns, parse_mode="MarkdownV2")
    except Exception:
        await call.message.answer(f"⚠️ Ошибка вывода фото для заявки #{q[0][0]}", reply_markup=btns)
    await call.answer()

@dp.callback_query(F.data.startswith(("adm_y_", "adm_n_")))
async def admin_decision_handler(call: types.CallbackQuery):
    """Обрабатывает решение админа: сохранение в базу или отклонение"""
    action, _, req_id = call.data.split("_")
    data = db.run_query("SELECT user_id, q_type, name, elo FROM queue WHERE id = ?", (int(req_id),), is_fetch=True)
    
    if data and action == "y":
        # INSERT OR REPLACE предотвращает дублирование юзеров
        db.run_query("INSERT OR REPLACE INTO users (user_id, username, elo) VALUES (?, ?, ?)", (data[0][0], data[0][2], data[0][3]))
        try: await bot.send_message(data[0][0], f"✅ *Ваша заявка одобрена\\!* Ваш текущий Эло: *{data[0][3]}*", parse_mode="MarkdownV2")
        except: pass
    
    db.run_query("DELETE FROM queue WHERE id = ?", (int(req_id),))
    await call.message.delete()
    # Автоматически переходим к следующей заявке
    await admin_view_queue(call)

@dp.callback_query(F.data == "admin_stats")
async def admin_stats_handler(call: types.CallbackQuery):
    all_u = db.run_query("SELECT COUNT(*) FROM stats_all", is_fetch=True)[0][0]
    top_u = db.run_query("SELECT COUNT(*) FROM users", is_fetch=True)[0][0]
    queue_u = db.run_query("SELECT COUNT(*) FROM queue", is_fetch=True)[0][0]
    await call.message.answer(f"📊 *Статистика проекта:*\n\nПользователей всего: {all_u}\nВ таблице лидеров: {top_u}\nЖдут проверки: {queue_u}")
    await call.answer()

# --- 10. ЗАПУСК ПРИЛОЖЕНИЯ ---
async def handle_ping(request):
    """Веб-хендлер для мониторинга активности хостингом"""
    return web.Response(text="Bot is running smoothly.")

async def start_main_engine():
    logger.info("Bot is warming up...")
    # Настройка веб-сервера (для Render/Amvera)
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8080))).start()
    
    # Запуск поллинга
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(start_main_engine())
    except Exception as fatal:
        logger.critical(f"System crash: {fatal}")

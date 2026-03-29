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

# --- 1. СИСТЕМА ЛОГИРОВАНИЯ ---
# Настройка логирования для отслеживания всех действий и ошибок бота.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(name)s: %(message)s",
    handlers=[logging.FileHandler("bot_technical.log"), logging.StreamHandler()]
)
logger = logging.getLogger("HockeyLeaderBot")

# --- 2. КОНФИГУРАЦИЯ ---
TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = 8239542728 

bot = Bot(token=TOKEN)
dp = Dispatcher()

# Приветственное сообщение с использованием MarkdownV2
WELCOME_TEXT = (
    "🏒 *Добро пожаловать в Лидерборд Три Кота Хоккей\\!*\n\n"
    "Здесь ты можешь отслеживать свой прогресс и соревноваться с лучшими\\.\n\n"
    "⚠️ *Важное примечание:*\n"
    "Данные обновляются после проверки модераторами вручную\\."
)

# --- 3. СОСТОЯНИЯ (FSM) ---
class Registration(StatesGroup):
    """Состояния для процесса регистрации нового игрока"""
    input_data = State()
    upload_photo = State()

class UpdateElo(StatesGroup):
    """Состояния для процесса обновления текущего рейтинга"""
    input_elo = State()
    upload_proof = State()

# --- 4. РАБОТА С БАЗОЙ ДАННЫХ ---
class DataManager:
    """Класс для управления всеми операциями с SQLite3"""
    def __init__(self, db_path='hockey_system.db'):
        self.db_path = db_path
        self.initialize_db()

    def initialize_db(self):
        """Создание необходимых таблиц при первом запуске"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                # Таблица игроков в топе
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY, 
                        username TEXT, 
                        elo INTEGER
                    )
                """)
                # Таблица персонала (админов)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS staff (
                        user_id INTEGER PRIMARY KEY, 
                        name TEXT, 
                        role TEXT, 
                        elo INTEGER, 
                        last_seen INTEGER
                    )
                """)
                # Таблица очереди заявок
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS queue (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, 
                        user_id INTEGER, 
                        q_type TEXT, 
                        name TEXT, 
                        elo INTEGER, 
                        photo_id TEXT
                    )
                """)
                # Регистрация владельца как главного админа
                cursor.execute("""
                    INSERT OR IGNORE INTO staff (user_id, name, role, elo, last_seen) 
                    VALUES (?, ?, ?, ?, ?)
                """, (OWNER_ID, "orbsi", "Владелец", 4002, int(time.time())))
                conn.commit()
                logger.info("База данных успешно инициализирована.")
        except Exception as error:
            logger.error(f"Ошибка инициализации БД: {error}")

    def execute(self, query, params=(), is_select=False):
        """Универсальный метод для выполнения SQL-запросов"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                if is_select:
                    return cursor.fetchall()
                conn.commit()
        except Exception as error:
            logger.error(f"SQL Error ({query}): {error}")
            return []

db = DataManager()

# --- 5. ГЕНЕРАЦИЯ КЛАВИАТУР ---
def main_keyboard(user_id):
    """Создает главное меню в зависимости от прав доступа пользователя"""
    is_admin = db.execute("SELECT 1 FROM staff WHERE user_id = ?", (user_id,), True)
    
    layout = [
        [KeyboardButton(text="➕ Добавить аккаунт"), KeyboardButton(text="🔄 Обновить эло")],
        [KeyboardButton(text="🏆 Топ 10"), KeyboardButton(text="🌟 Топ 100")],
        [KeyboardButton(text="📊 Мой рейтинг"), KeyboardButton(text="👥 Сотрудники")]
    ]
    
    if user_id == OWNER_ID or is_admin:
        layout.append([KeyboardButton(text="⚙️ Admin Panel")])
        
    return ReplyKeyboardMarkup(keyboard=layout, resize_keyboard=True)

def cancel_keyboard():
    """Кнопка для отмены текущей операции FSM"""
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Отмена")]], resize_keyboard=True)

# --- 6. ОСНОВНЫЕ ОБРАБОТЧИКИ ---

@dp.message(CommandStart())
async def start_command(message: types.Message, state: FSMContext):
    """Обработка команды /start"""
    await state.clear()
    # Обновляем время активности, если это админ
    db.execute("UPDATE staff SET last_seen = ? WHERE user_id = ?", (int(time.time()), message.from_user.id))
    await message.answer(
        WELCOME_TEXT, 
        reply_markup=main_keyboard(message.from_user.id), 
        parse_mode="MarkdownV2"
    )

@dp.message(F.text == "❌ Отмена")
async def cancel_action(message: types.Message, state: FSMContext):
    """Сброс любого состояния FSM"""
    await state.clear()
    await message.answer("🛑 Действие было отменено.", reply_markup=main_keyboard(message.from_user.id))

@dp.message(F.text == "👥 Сотрудники")
async def list_staff_members(message: types.Message):
    """Отображение списка администрации и их статуса"""
    members = db.execute("SELECT name, role, last_seen FROM staff", is_select=True)
    text = "👥 *Актуальный состав команды:*\n\n"
    now = int(time.time())
    
    for m in members:
        # Если последняя активность была менее 5 минут назад — онлайн
        status_icon = "🟢" if (now - m[2]) < 300 else "⚪"
        text += f"{status_icon} *{m[0]}* — {m[1]}\n"
        
    await message.answer(text, parse_mode="MarkdownV2")

@dp.message(F.text == "📊 Мой рейтинг")
async def show_user_stats(message: types.Message):
    """Показывает позицию текущего пользователя в общем зачете"""
    user_data = db.execute("SELECT username, elo FROM users WHERE user_id = ?", (message.from_user.id,), True)
    
    if not user_data:
        return await message.answer("❌ Вы еще не зарегистрированы в системе рейтингов!")
        
    # Считаем место в топе
    all_players = db.execute("SELECT user_id FROM users ORDER BY elo DESC", is_select=True)
    position = next((i for i, (uid,) in enumerate(all_players, 1) if uid == message.from_user.id), "не определено")
    
    await message.answer(
        f"📊 *Ваш профиль:*\n\n👤 Ник: `{user_data[0][0]}`\n🏒 Рейтинг: *{user_data[0][1]}*\n🏆 Место: {position}",
        parse_mode="MarkdownV2"
    )

@dp.message(F.text == "🌟 Топ 100")
async def show_top_100(message: types.Message):
    """Вывод 100 лучших игроков сервера"""
    top_list = db.execute("SELECT username, elo FROM users ORDER BY elo DESC LIMIT 100", is_select=True)
    
    if not top_list:
        return await message.answer("🌟 Список лидеров в данный момент пуст.")
        
    response_text = "🌟 *Топ 100 игроков:*\n\n"
    for idx, (name, val) in enumerate(top_list, 1):
        response_text += f"{idx}\\. `{name}` — *{val}*\n"
        
    await message.answer(response_text, parse_mode="MarkdownV2")

# --- 7. ПРОЦЕСС РЕГИСТРАЦИИ (FSM) ---

@dp.message(F.text == "➕ Добавить аккаунт")
async def start_registration(message: types.Message, state: FSMContext):
    """Начало подачи заявки на добавление в топ"""
    await message.answer("📝 Введите Ваш Ник и текущее Эло через пробел:\n_(Пример: Player1 1200)_", reply_markup=cancel_keyboard())
    await state.set_state(Registration.input_data)

@dp.message(Registration.input_data)
async def process_reg_data(message: types.Message, state: FSMContext):
    """Валидация введенных текстовых данных"""
    try:
        data_parts = message.text.split()
        if len(data_parts) < 2:
            raise ValueError("Недостаточно данных")
            
        nickname = data_parts[0]
        elo_points = int(data_parts[1])
        
        await state.update_data(reg_name=nickname, reg_elo=elo_points)
        await message.answer("📸 Теперь отправьте скриншот Вашего профиля из игры для подтверждения:")
        await state.set_state(Registration.upload_photo)
    except Exception:
        await message.answer("⚠️ Ошибка! Пожалуйста, введите данные в формате: `Ник Число`.")

@dp.message(Registration.upload_photo, F.photo)
async def process_reg_photo(message: types.Message, state: FSMContext):
    """Сохранение заявки в базу данных"""
    user_state_data = await state.get_data()
    
    db.execute(
        "INSERT INTO queue (user_id, q_type, name, elo, photo_id) VALUES (?, ?, ?, ?, ?)",
        (message.from_user.id, "NEW", user_state_data['reg_name'], user_state_data['reg_elo'], message.photo[-1].file_id)
    )
    
    await message.answer("⏳ Ваша заявка успешно создана и ожидает проверки модератором!", reply_markup=main_keyboard(message.from_user.id))
    await state.clear()

# --- 8. АДМИН-ПАНЕЛЬ И ОБРАБОТКА ЗАЯВОК ---

@dp.message(F.text == "⚙️ Admin Panel")
async def open_admin_panel(message: types.Message):
    """Проверка прав и открытие меню администратора"""
    is_staff = db.execute("SELECT 1 FROM staff WHERE user_id = ?", (message.from_user.id,), True)
    if not (message.from_user.id == OWNER_ID or is_staff):
        return
        
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📂 Список заявок", callback_data="admin_view_requests")],
        [InlineKeyboardButton(text="📊 Общая статистика", callback_data="admin_get_stats")]
    ])
    await message.answer("🛠 *Панель управления модератора:*", reply_markup=admin_kb, parse_mode="MarkdownV2")

@dp.callback_query(F.data == "admin_view_requests")
async def render_request_card(call: types.CallbackQuery):
    """Отображение одной заявки из очереди с защитой от ошибок"""
    request = db.execute("SELECT id, q_type, name, elo, photo_id FROM queue LIMIT 1", is_select=True)
    
    if not request:
        return await call.message.answer("📦 Очередь заявок пуста.")
        
    req_id, req_type, p_name, p_elo, file_id = request[0]
    
    decision_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_{req_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{req_id}")
    ]])
    
    caption_info = f"📦 *Заявка #{req_id}* ({req_type})\n👤 Ник: `{p_name}`\n🏒 Эло: *{p_elo}*"
    
    try:
        # Пытаемся отправить фото из заявки
        await bot.send_photo(call.from_user.id, photo=file_id, caption=caption_info, reply_markup=decision_kb, parse_mode="MarkdownV2")
    except Exception as photo_error:
        # Если фото недоступно, отправляем текстовую карточку
        logger.warning(f"Ошибка отправки фото: {photo_error}")
        await bot.send_message(call.from_user.id, f"{caption_info}\n\n⚠️ _Изображение недоступно\\._", reply_markup=decision_kb, parse_mode="MarkdownV2")
        
    await call.answer()

@dp.callback_query(F.data.startswith(("approve_", "reject_")))
async def handle_admin_decision(call: types.CallbackQuery):
    """Логика принятия решения по заявке и уведомление игрока"""
    action, r_id = call.data.split("_")
    
    # Получаем данные о заявке перед ее удалением
    req_data = db.execute("SELECT user_id, q_type, name, elo FROM queue WHERE id = ?", (int(r_id),), True)
    
    if req_data:
        target_id, q_type, p_name, p_elo = req_data[0]
        
        if action == "approve":
            # Сохраняем игрока в таблицу лидеров
            db.execute("INSERT OR REPLACE INTO users (user_id, username, elo) VALUES (?, ?, ?)", (target_id, p_name, p_elo))
            result_msg = "✅ Ваша заявка успешно одобрена модератором! Теперь Вы в топе."
        else:
            # Просто уведомляем об отказе
            result_msg = "❌ Ваша заявка была отклонена модератором после проверки."
            
        try:
            # Отправка уведомления пользователю
            await bot.send_message(target_id, f"{result_msg}\nНик: {p_name}, Эло: {p_elo}")
        except Exception:
            logger.error(f"Не удалось отправить уведомление пользователю {target_id}")

    # Удаляем заявку из очереди и обновляем интерфейс админа
    db.execute("DELETE FROM queue WHERE id = ?", (int(r_id),))
    await call.message.delete()
    await render_request_card(call)

# --- 9. ЗАПУСК ВЕБ-СЕРВЕРА (Keep-Alive) ---
async def start_background_server():
    """Запуск простейшего сервера для предотвращения сна на хостингах"""
    webapp = web.Application()
    webapp.router.add_get("/", lambda request: web.Response(text="Бот в сети и работает."))
    site_runner = web.AppRunner(webapp)
    await site_runner.setup()
    # Стандартный порт 8080 для большинства облачных провайдеров
    await web.TCPSite(site_runner, "0.0.0.0", int(os.getenv("PORT", 8080))).start()
    logger.info("Веб-сервер мониторинга запущен.")
    # Запуск поллинга Telegram
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(start_background_server())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен пользователем.")
    except Exception as critical_error:
        logger.critical(f"Критическая ошибка при запуске: {critical_error}")

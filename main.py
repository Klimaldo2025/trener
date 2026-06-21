import os
import asyncio
import logging
import sqlite3
import random
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler

BOT_TOKEN = os.environ.get("BOT_TOKEN")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler()

# --- БАЗА ДАННЫХ (Переделана под нормальную структуру) ---
def init_db():
    conn = sqlite3.connect("bot_memory.db")
    cursor = conn.cursor()
    # Таблица пользователей
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            role TEXT,          -- 'trainer' или 'friend'
            name TEXT,
            time_hours INTEGER,
            time_minutes INTEGER,
            streak_days INTEGER DEFAULT 0,
            missed_days INTEGER DEFAULT 0,
            last_workout_done INTEGER DEFAULT 1
        )
    """)
    # Таблица глобальных настроек (чтобы хранить план тренировок)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY DEFAULT 1,
            workout_plan TEXT DEFAULT 'План пока не составлен. Жди указаний тренера!'
        )
    """)
    # Таблица лимитов сообщений
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS friend_notes_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("INSERT OR IGNORE INTO settings (id) VALUES (1)")
    conn.commit()
    conn.close()

# Получить данные конкретного юзера
def get_user(user_id):
    conn = sqlite3.connect("bot_memory.db")
    cursor = conn.cursor()
    cursor.execute("SELECT role, name, time_hours, time_minutes, streak_days, missed_days, last_workout_done FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"role": row[0], "name": row[1], "time_hours": row[2], "time_minutes": row[3], "streak_days": row[4], "missed_days": row[5], "last_workout_done": row[6]}
    return None

# Получить ID тренера и друга (для рассылок)
def get_roles_ids():
    conn = sqlite3.connect("bot_memory.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE role='trainer'")
    t_row = cursor.fetchone()
    cursor.execute("SELECT user_id FROM users WHERE role='friend'")
    f_row = cursor.fetchone()
    conn.close()
    return {
        "trainer_id": t_row[0] if t_row else None,
        "friend_id": f_row[0] if f_row else None
    }

def get_workout_plan():
    conn = sqlite3.connect("bot_memory.db")
    cursor = conn.cursor()
    cursor.execute("SELECT workout_plan FROM settings WHERE id=1")
    plan = cursor.fetchone()[0]
    conn.close()
    return plan

def update_user(user_id, field, value):
    conn = sqlite3.connect("bot_memory.db")
    cursor = conn.cursor()
    cursor.execute(f"INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    cursor.execute(f"UPDATE users SET {field} = ? WHERE user_id = ?", (value, user_id))
    conn.commit()
    conn.close()

def update_workout_plan(new_plan):
    conn = sqlite3.connect("bot_memory.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE settings SET workout_plan = ? WHERE id=1", (new_plan,))
    conn.commit()
    conn.close()

def check_friend_limit(user_id):
    conn = sqlite3.connect("bot_memory.db")
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM friend_notes_log WHERE user_id=? AND timestamp > datetime('now', '-1 hour')", (user_id,))
    count = cursor.fetchone()[0]
    conn.close()
    return count < 5

def log_friend_message(user_id):
    conn = sqlite3.connect("bot_memory.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO friend_notes_log (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()


# --- СОСТОЯНИЯ (FSM) ---
class BotStates(StatesGroup):
    WAITING_FOR_NAME = State()
    WAITING_FOR_TRAINER_NAME = State()
    WAITING_FOR_HOURS = State()
    WAITING_FOR_MINUTES = State()
    TRAINER_SEND_NOTE = State()
    TRAINER_CHANGE_PLAN = State()
    FRIEND_SEND_MESSAGE_TO_TRAINER = State()

def get_workout_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="Выполнено! ✅", callback_data="workout_done")
    return builder.as_markup()

# Ежедневное напоминание (вызывается планировщиком)
async def send_reminder(chat_id):
    user_data = get_user(chat_id)
    ids = get_roles_ids()
    plan = get_workout_plan()
    
    if user_data and user_data["role"] == "friend":
        if user_data["last_workout_done"] == 0:
            new_missed = user_data["missed_days"] + 1
            update_user(chat_id, "missed_days", new_missed)
            update_user(chat_id, "streak_days", 0)
            if ids["trainer_id"]:
                await bot.send_message(ids["trainer_id"], f"⚠️ Пропуск! {user_data['name']} не нажал кнопку за прошлую тренировку.")

        update_user(chat_id, "last_workout_done", 0)
        await bot.send_message(
            chat_id, 
            f"Привет, {user_data['name']}! Время тренировки! 💪\n\n**Твой план:**\n{plan}\n\nВыполнишь — жми кнопку!",
            reply_markup=get_workout_keyboard()
        )

@dp.callback_query(F.data == "workout_done")
async def workout_done_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user_data = get_user(user_id)
    ids = get_roles_ids()

    if not user_data or user_data["role"] != "friend":
        await callback.answer("Кнопка предназначена только для друга!", show_alert=True)
        return
        
    if user_data["last_workout_done"] == 1:
        await callback.answer("Ты уже отметился за сегодня!", show_alert=True)
        return

    new_streak = user_data["streak_days"] + 1
    update_user(user_id, "streak_days", new_streak)
    update_user(user_id, "last_workout_done", 1)

    await callback.message.edit_text(f"🔥 Красава, {user_data['name']}! Тренировка засчитана. Серия: {new_streak} дн.")
    if ids["trainer_id"]:
        await bot.send_message(ids["trainer_id"], f"💪 {user_data['name']} потренировался! Серия: {new_streak} дней.")
    await callback.answer()


# --- КОМАНДЫ СТАРТА И СМЕНЫ РЕЖИМОВ ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Привет! Введи, пожалуйста, свое имя:")
    await state.set_state(BotStates.WAITING_FOR_NAME)

@dp.message(Command("swap"))
async def cmd_swap(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    user_data = get_user(user_id)

    if user_data and user_data["role"] == "trainer":
        # Полностью превращаем аккаунт в друга
        update_user(user_id, "role", "friend")
        update_user(user_id, "name", "Тест-Друг")
        await message.answer("🔄 **Режим переключен:** Теперь бот видит твой аккаунт как **ДРУГА**. Напиши 'справка' для просмотра его функций.")
    else:
        # Полностью превращаем аккаунт в тренера
        update_user(user_id, "role", "trainer")
        update_user(user_id, "name", "Тренер")
        await message.answer("🔄 **Режим переключен:** Ты снова **ТРЕНЕР**. Тебе открыт секретный админ-отсек.")


@dp.message(BotStates.WAITING_FOR_NAME)
async def process_name(message: types.Message, state: FSMContext):
    user_input = message.text.strip()
    user_id = message.from_user.id
    
    if user_input == "Я ебаный пидорас":
        update_user(user_id, "role", "trainer")
        update_user(user_id, "name", "Тренер")
        await state.clear()
        await message.answer("Секретный отсек тренера активирован! 😎\nНапиши слово **'справка'**, чтобы узнать свои текстовые команды.")
    else:
        update_user(user_id, "role", "friend")
        update_user(user_id, "name", user_input)
        await message.answer(f"Принято, {user_input}! Теперь напиши час (0-23), в который тебе присылать напоминалку:")
        await state.set_state(BotStates.WAITING_FOR_HOURS)

@dp.message(BotStates.WAITING_FOR_TRAINER_NAME)
async def process_trainer_name(message: types.Message, state: FSMContext):
    user_input = message.text.strip()
    update_user(message.from_user.id, "name", user_input)
    await message.answer(f"Шеф, твой ник успешно изменен на: **{user_input}** 😎")
    await state.clear()

@dp.message(BotStates.WAITING_FOR_HOURS)
async def process_hours(message: types.Message, state: FSMContext):
    try:
        hours = int(message.text)
        if 0 <= hours <= 23:
            update_user(message.from_user.id, "time_hours", hours)
            await message.answer("Отлично! Теперь введи минуты (0-59):")
            await state.set_state(BotStates.WAITING_FOR_MINUTES)
        else:
            await message.answer("Введи час от 0 до 23.")
    except ValueError:
        await message.answer("Нужно ввести число!")

@dp.message(BotStates.WAITING_FOR_MINUTES)
async def process_minutes(message: types.Message, state: FSMContext):
    try:
        minutes = int(message.text)
        user_id = message.from_user.id
        if 0 <= minutes <= 59:
            update_user(user_id, "time_minutes", minutes)
            await state.clear()
            
            user_data = get_user(user_id)
            await message.answer(f"Всё сохранил! Время напоминаний: {user_data['time_hours']:02d}:{user_data['time_minutes']:02d}.\nНапиши слово **'справка'**, чтобы увидеть команды.")
            
            scheduler.add_job(
                send_reminder, trigger="cron", 
                hour=user_data["time_hours"], minute=user_data["time_minutes"], 
                args=[user_id], id=f"reminder_{user_id}", replace_existing=True
            )
        else:
            await message.answer("Введи минуты от 0 до 59.")
    except ValueError:
        await message.answer("Нужно ввести число!")


# --- ОБРАБОТКА ТЕКСТОВЫХ ОТВЕТОВ ИЗ СОСТОЯНИЙ ---
@dp.message(BotStates.TRAINER_CHANGE_PLAN)
async def trainer_confirm_plan(message: types.Message, state: FSMContext):
    update_workout_plan(message.text)
    await message.answer("Новый план успешно сохранен!")
    
    ids = get_roles_ids()
    if ids["friend_id"]:
        await bot.send_message(
            ids["friend_id"], 
            f"🔔 **Тренер изменил план тренировок!**\n\n**Новый план:**\n{message.text}"
        )
    await state.clear()

@dp.message(BotStates.TRAINER_SEND_NOTE)
async def trainer_confirm_note(message: types.Message, state: FSMContext):
    user_data = get_user(message.from_user.id)
    ids = get_roles_ids()
    
    if ids["friend_id"]:
        await bot.send_message(ids["friend_id"], f"📩 **Заметка от тренера ({user_data['name']}):**\n\n{message.text}")
        await message.answer("Заметка мгновенно доставлена другу!")
    else:
        await message.answer("Друг еще не зарегистрирован в боте.")
    await state.clear()

@dp.message(BotStates.FRIEND_SEND_MESSAGE_TO_TRAINER)
async def friend_confirm_to_trainer(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user_data = get_user(user_id)
    ids = get_roles_ids()
    
    if ids["trainer_id"]:
        await bot.send_message(ids["trainer_id"], f"✉️ **Сообщение от друга ({user_data['name']}):**\n\n{message.text}")
        await message.answer("Я передал твою заметку тренеру! 🫡")
    else:
        await message.answer("Тренер еще не зашел в бота.")
    await state.clear()


# --- ГЛАВНЫЙ УМНЫЙ ПАРСЕР ТЕКСТА ---
@dp.message(F.text)
async def main_text_parser(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user_data = get_user(user_id)
    text = message.text.lower()

    if not user_data:
        await message.answer("Пожалуйста, введи команду /start для регистрации.")
        return

    # --- ЛОГИКА ДЛЯ ТРЕНЕРА ---
    if user_data["role"] == "trainer":
        trainer_triggers = {
            "help": ["справка", "помощь", "инфо", "команд"],
            "status": ["статистика", "прогресс", "отчет", "состояни"],
            "plan": ["план", "упражнен"],
            "note": ["заметка", "сообщен", "написать"],
            "change_name": ["ник", "имя", "назвать"]
        }

        found = [cmd for cmd, keywords in trainer_triggers.items() if any(k in text for k in keywords)]
        
        if len(found) > 1:
            await message.answer("📋 Шеф, слишком много команд в одном сообщении. Напиши что-то одно!")
            return
        if not found:
            await message.answer("Я не понял команду, Шеф. Напиши слово **'справка'**, чтобы посмотреть список действий.")
            return

        cmd = found[0]
        if cmd == "help":
            await message.answer(
                f"📋 **Пульт управления тренера ({user_data['name']}):**\n\n"
                f"🔹 **'статистика'** — полный отчет по другу.\n"
                f"🔹 **'план'** — изменить упражнения (улетят другу с уведомлением).\n"
                f"🔹 **'заметка'** — переслать свой следующий текст другу.\n"
                f"🔹 **'ник'** — изменить твое имя тренера в боте.\n"
                f"ℹ️ *Переключить режим для теста: /swap*"
            )
        elif cmd == "status":
            # Ищем, есть ли друг в базе
            conn = sqlite3.connect("bot_memory.db")
            cursor = conn.cursor()
            cursor.execute("SELECT name, time_hours, time_minutes, streak_days, missed_days, last_workout_done FROM users WHERE role='friend'")
            f_row = cursor.fetchone()
            conn.close()
            
            if f_row:
                time_str = f"{f_row[1]:02d}:{f_row[2]:02d}" if f_row[1] is not None else "Не настроено"
                status_today = "Выполнил! ✅" if f_row[5] == 1 else "Еще филонит ❌"
                await message.answer(
                    f"📊 **Текущий отчет подопечного:**\n\n"
                    f"👤 **Ник/Имя:** {f_row[0]}\n"
                    f"⏰ **Время напоминалки:** {time_str}\n"
                    f"🔥 **Серия дней:** {f_row[3]}\n"
                    f"⚠️ **Количество пропусков:** {f_row[4]}\n"
                    f"💪 **Статус на сегодня:** {status_today}\n\n"
                    f"📝 **Текущий план в базе:** {get_workout_plan()}"
                )
            else:
                await message.answer("Друг еще не зарегистрирован в боте.")
        elif cmd == "plan":
            await message.answer("Напиши текст с упражнениями. Я обновлю план и сразу уведомлю твоего друга:")
            await state.set_state(BotStates.TRAINER_CHANGE_PLAN)
        elif cmd == "note":
            await message.answer("Напиши текст сообщения, которое нужно передать другу:")
            await state.set_state(BotStates.TRAINER_SEND_NOTE)
        elif cmd == "change_name":
            await message.answer("Шеф, какое имя тебе поставить?")
            await state.set_state(BotStates.WAITING_FOR_TRAINER_NAME)
        return

    # --- ЛОГИКА ДЛЯ ДРУГА ---
    if user_data["role"] == "friend":
        friend_triggers = {
            "change_name": ["ник", "имя", "назвать"],
            "change_time": ["время", "часы", "минут"],
            "view_plan": ["план", "упражнен"],
            "to_trainer": ["тренер", "заметка", "сообщен"],
            "help": ["помощь", "справка", "инфо", "команд"],
            "progress": ["прогресс", "статистика", "дни", "пропуск"],
            "motivation": ["мотивация", "поддержи", "пинок"]
        }

        found = [cmd for cmd, keywords in friend_triggers.items() if any(k in text for k in keywords)]
        
        if len(found) > 1:
            await message.answer("🤖 Ого, сколько задач! Давай по одной. Напиши четко одно слово.")
            return
        if not found:
            await message.answer("Я тебя не совсем понял. Напиши слово **'справка'**, чтобы глянуть, что я умею.")
            return

        cmd = found[0]
        if cmd == "help":
            await message.answer(
                f"📋 **Вот что ты можешь сделать, {user_data['name']}:**\n\n"
                f"🔹 **'план'** — посмотреть свои текущие упражнения.\n"
                f"🔹 **'ник'** — поменять имя в боте.\n"
                f"🔹 **'время'** — настроить часы для напоминаний.\n"
                f"🔹 **'прогресс'** — твоя серия дней и пропуски.\n"
                f"🔹 **'тренер'** — отправить сообщение тренеру.\n"
                f"🔹 **'мотивация'** — получить пинок к действию!"
            )
        elif cmd == "change_name":
            await message.answer("Без проблем! Какое имя тебе поставить?")
            await state.set_state(BotStates.WAITING_FOR_NAME)
        elif cmd == "change_time":
            await message.answer("Хорошо, давай перенастроим время. В какой час (0-23) тебе удобно тренироваться?")
            await state.set_state(BotStates.WAITING_FOR_HOURS)
        elif cmd == "view_plan":
            await message.answer(f"📋 **Твой план тренировок:**\n\n{get_workout_plan()}")
        elif cmd == "progress":
            status_workout = "✅ Выполнена!" if user_data["last_workout_done"] == 1 else "❌ Ждет отметки кнопкой!"
            await message.answer(
                f"📊 **Твои успехи:**\n\n"
                f"🔥 Серия дней: **{user_data['streak_days']}**\n"
                f"⚠️ Пропущено дней: **{user_data['missed_days']}**\n"
                f"💪 Сегодняшний статус: {status_workout}"
            )
        elif cmd == "to_trainer":
            if not check_friend_limit(user_id):
                await message.answer("⚠️ Ты исчерпал лимит заметок тренеру (максимум 5 сообщений в час). Отдохни немного!")
                return
            log_friend_message(user_id)
            await message.answer("Напиши сообщение для тренера в следующем ответе, я сразу перешлю:")
            await state.set_state(BotStates.FRIEND_SEND_MESSAGE_TO_TRAINER)
        elif cmd == "motivation":
            phrases = [
                "Каждый пропущенный день отдаляет тебя от идеальной формы на неделю! Вставай! 🦾",
                "Твое тело может всё. Это твой мозг нужно убедить. Погнали!",
                "Сегодняшняя тренировка — это вклад в твое будущее. Не подводи тренера!",
                "Дисциплина — это решение делать то, чего очень не хочется, чтобы достичь того, чего очень хочется! 🔥"
            ]
            await message.answer(f"💪 {random.choice(phrases)}")


# --- ЗАПУСК ---
async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    
    # Восстановление таймеров для всех друзей в базе при перезапуске сервера Railway
    conn = sqlite3.connect("bot_memory.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, time_hours, time_minutes FROM users WHERE role='friend'")
    rows = cursor.fetchall()
    conn.close()
    
    for row in rows:
        if row[1] is not None and row[2] is not None:
            scheduler.add_job(
                send_reminder, trigger="cron", 
                hour=row[1], minute=row[2], 
                args=[row[0]], id=f"reminder_{row[0]}", replace_existing=True
            )
            
    scheduler.start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

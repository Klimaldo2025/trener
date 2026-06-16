import os
import asyncio
import logging
import sqlite3
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Получаем токен из настроек Railway (безопасный подход)
BOT_TOKEN = os.environ.get("BOT_TOKEN")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler()

# --- РАБОТА С БАЗОЙ ДАННЫХ (SQLite) ---
def init_db():
    conn = sqlite3.connect("bot_memory.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bot_data (
            id INTEGER PRIMARY KEY DEFAULT 1,
            trainer_id INTEGER,
            friend_id INTEGER,
            friend_name TEXT DEFAULT 'Друг',
            time_hours INTEGER,
            time_minutes INTEGER,
            workout_plan TEXT DEFAULT 'План пока не составлен. Жди указаний тренера!'
        )
    """)
    cursor.execute("INSERT OR IGNORE INTO bot_data (id) VALUES (1)")
    conn.commit()
    conn.close()

def get_db():
    conn = sqlite3.connect("bot_memory.db")
    cursor = conn.cursor()
    cursor.execute("SELECT trainer_id, friend_id, friend_name, time_hours, time_minutes, workout_plan FROM bot_data WHERE id=1")
    row = cursor.fetchone()
    conn.close()
    return {
        "trainer_id": row[0],
        "friend_id": row[1],
        "friend_name": row[2],
        "time_hours": row[3],
        "time_minutes": row[4],
        "workout_plan": row[5]
    }

def update_db(field, value):
    conn = sqlite3.connect("bot_memory.db")
    cursor = conn.cursor()
    cursor.execute(f"UPDATE bot_data SET {field} = ? WHERE id=1", (value,))
    conn.commit()
    conn.close()


class BotStates(StatesGroup):
    WAITING_FOR_NAME = State()
    WAITING_FOR_HOURS = State()
    WAITING_FOR_MINUTES = State()
    TRAINER_SEND_NOTE = State()
    TRAINER_CHANGE_PLAN = State()

async def send_reminder(chat_id):
    data = get_db()
    if data["friend_id"]:
        await bot.send_message(
            chat_id, 
            f"Привет, {data['friend_name']}! Время потренироваться! 💪\n\nТвой план на сегодня:\n{data['workout_plan']}"
        )

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await message.answer("Привет! Пожалуйста, введи свое имя:")
    await state.set_state(BotStates.WAITING_FOR_NAME)

@dp.message(BotStates.WAITING_FOR_NAME)
async def process_name(message: types.Message, state: FSMContext):
    user_input = message.text.strip()
    
    if user_input == "Я жирное чмо":
        update_db("trainer_id", message.from_user.id)
        await state.clear()
        await message.answer(
            "Добро пожаловать, Тренер! 😎\n"
            "Доступные команды:\n"
            "/note - Отправить заметку другу\n"
            "/setplan - Изменить план тренировок\n"
            "/status - Посмотреть прогресс друга"
        )
    else:
        update_db("friend_id", message.from_user.id)
        update_db("friend_name", user_input)
        await message.answer(f"Принято, буду звать тебя {user_input}! Теперь укажи, в какое время (часы) тебе удобно тренироваться? (0-23)")
        await state.set_state(BotStates.WAITING_FOR_HOURS)

@dp.message(BotStates.WAITING_FOR_HOURS)
async def process_hours(message: types.Message, state: FSMContext):
    try:
        hours = int(message.text)
        if 0 <= hours <= 23:
            update_db("time_hours", hours)
            await message.answer("Отлично! А теперь напиши минуты (0-59):")
            await state.set_state(BotStates.WAITING_FOR_MINUTES)
        else:
            await message.answer("Пожалуйста, введи корректный час (от 0 до 23).")
    except ValueError:
        await message.answer("Введи время цифрами!")

@dp.message(BotStates.WAITING_FOR_MINUTES)
async def process_minutes(message: types.Message, state: FSMContext):
    try:
        minutes = int(message.text)
        if 0 <= minutes <= 59:
            update_db("time_minutes", minutes)
            await state.clear()
            
            data = get_db()
            await message.answer(f"Все настроено, {data['friend_name']}! Я буду напоминать тебе о тренировках каждый день в {data['time_hours']:02d}:{data['time_minutes']:02d}.")
            
            scheduler.add_job(
                send_reminder, 
                trigger="cron", 
                hour=data["time_hours"], 
                minute=data["time_minutes"], 
                args=[message.chat.id],
                id="daily_workout_reminder",
                replace_existing=True
            )
        else:
            await message.answer("Пожалуйста, введи корректные минуты (от 0 до 59).")
    except ValueError:
        await message.answer("Введи минуты цифрами!")

@dp.message(F.text.lower().contains("хочу сменить имя"))
async def change_name_request(message: types.Message, state: FSMContext):
    data = get_db()
    if message.from_user.id == data["friend_id"]:
        await message.answer("Как тебя теперь называть?")
        await state.set_state(BotStates.WAITING_FOR_NAME)

# --- БЛОК ТРЕНЕРА ---
@dp.message(Command("note"))
async def trainer_note_cmd(message: types.Message, state: FSMContext):
    data = get_db()
    if message.from_user.id == data["trainer_id"]:
        await message.answer("Напиши текст заметки, которую хочешь отправить другу:")
        await state.set_state(BotStates.TRAINER_SEND_NOTE)

@dp.message(BotStates.TRAINER_SEND_NOTE)
async def trainer_send_note(message: types.Message, state: FSMContext):
    data = get_db()
    if data["friend_id"]:
        await bot.send_message(data["friend_id"], f"📋 **Заметка от тренера:**\n\n{message.text}")
        await message.answer("Заметка успешно отправлена другу!")
    else:
        await message.answer("Друг еще не зарегистрировался в боте.")
    await state.clear()

@dp.message(Command("setplan"))
async def trainer_plan_cmd(message: types.Message, state: FSMContext):
    data = get_db()
    if message.from_user.id == data["trainer_id"]:
        await message.answer("Введите новый ежедневный план тренировок для друга:")
        await state.set_state(BotStates.TRAINER_CHANGE_PLAN)

@dp.message(BotStates.TRAINER_CHANGE_PLAN)
async def trainer_change_plan(message: types.Message, state: FSMContext):
    update_db("workout_plan", message.text)
    await message.answer("План тренировок успешно обновлен!")
    data = get_db()
    if data["friend_id"]:
        await bot.send_message(data["friend_id"], f"🔔 **Твой план тренировок был обновлен тренером!**\n\nНовый план:\n{message.text}")
    await state.clear()

@dp.message(Command("status"))
async def trainer_status(message: types.Message):
    data = get_db()
    if message.from_user.id == data["trainer_id"]:
        await message.answer(
            f"📊 **Отчет по другу:**\n"
            f"Имя: {data['friend_name']}\n"
            f"Время тренировки: {data['time_hours']}:{data['time_minutes']}\n"
            f"Текущий план: {data['workout_plan']}"
        )

# Функция запуска
async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    
    # Восстанавливаем напоминание из базы данных при перезапуске сервера
    data = get_db()
    if data["friend_id"] and data["time_hours"] is not None and data["time_minutes"] is not None:
        scheduler.add_job(
            send_reminder, 
            trigger="cron", 
            hour=data["time_hours"], 
            minute=data["time_minutes"], 
            args=[data["friend_id"]],
            id="daily_workout_reminder",
            replace_existing=True
        )
    
    scheduler.start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

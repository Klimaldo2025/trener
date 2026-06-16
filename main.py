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

# --- БАЗА ДАННЫХ (Расширенная) ---
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
            workout_plan TEXT DEFAULT 'План пока не составлен. Жди указаний тренера!',
            streak_days INTEGER DEFAULT 0,
            missed_days INTEGER DEFAULT 0,
            last_workout_done INTEGER DEFAULT 1  -- 1 = выполнено или новый день, 0 = ждет отметки
        )
    """)
    cursor.execute("INSERT OR IGNORE INTO bot_data (id) VALUES (1)")
    conn.commit()
    conn.close()

def get_db():
    conn = sqlite3.connect("bot_memory.db")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT trainer_id, friend_id, friend_name, time_hours, time_minutes, 
               workout_plan, streak_days, missed_days, last_workout_done 
        FROM bot_data WHERE id=1
    """)
    row = cursor.fetchone()
    conn.close()
    return {
        "trainer_id": row[0],
        "friend_id": row[1],
        "friend_name": row[2],
        "time_hours": row[3],
        "time_minutes": row[4],
        "workout_plan": row[5],
        "streak_days": row[6],
        "missed_days": row[7],
        "last_workout_done": row[8]
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
    FRIEND_SEND_MESSAGE_TO_TRAINER = State()

# --- КНОПКА ДЛЯ ТРЕНИРОВКИ ---
def get_workout_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="Выполнено! ✅", callback_data="workout_done")
    return builder.as_markup()

# Ежедневное напоминание
async def send_reminder(chat_id):
    data = get_db()
    if data["friend_id"]:
        # Проверяем, выполнил ли он ПРЕДЫДУЩУЮ тренировку
        if data["last_workout_done"] == 0:
            new_missed = data["missed_days"] + 1
            update_db("missed_days", new_missed)
            update_db("streak_days", 0) # Сбрасываем серию дней
            if data["trainer_id"]:
                await bot.send_message(data["trainer_id"], f"⚠️ Пропуск! {data['friend_name']} не отметился за прошлую тренировку.")

        # Ставим флаг, что сегодняшняя тренировка еще не сделана
        update_db("last_workout_done", 0)

        await bot.send_message(
            chat_id, 
            f"Привет, {data['friend_name']}! Настало время качаться! 🏋️‍♂️\n\n**Твой план:**\n{data['workout_plan']}\n\nКак закончишь, обязательно нажми кнопку ниже, чтобы я зачитал прогресс!",
            reply_markup=get_workout_keyboard()
        )

# Обработка нажатия кнопки "Выполнено"
@dp.callback_query(F.data == "workout_done")
async def workout_done_callback(callback: types.CallbackQuery):
    data = get_db()
    if callback.from_user.id != data["friend_id"]:
        await callback.answer("Это кнопка не для тебя!", show_alert=True)
        return

    if data["last_workout_done"] == 1:
        await callback.answer("Ты уже отметился за сегодня! Отдыхай! 🔥", show_alert=True)
        return

    new_streak = data["streak_days"] + 1
    update_db("streak_days", new_streak)
    update_db("last_workout_done", 1)

    await callback.message.edit_text(f"🔥 Отличная работа, {data['friend_name']}!\nТренировка засчитана. Твоя текущая серия: {new_streak} дн. подрят!")
    
    # Уведомляем тренера
    if data["trainer_id"]:
        await bot.send_message(data["trainer_id"], f"💪 {data['friend_name']} выполнил сегодняшнюю тренировку! Серия: {new_streak} дней.")
    await callback.answer()

# --- СТАРТ ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await message.answer("Привет! Добро пожаловать в тренировочный бот. Напиши, как мне тебя называть? (Введи свое имя/ник)")
    await state.set_state(BotStates.WAITING_FOR_NAME)

@dp.message(BotStates.WAITING_FOR_NAME)
async def process_name(message: types.Message, state: FSMContext):
    user_input = message.text.strip()
    
    if user_input == "Я жирное чмо":
        update_db("trainer_id", message.from_user.id)
        await state.clear()
        await message.answer(
            "Приветствую, Шеф! 😎 Твой секретный отсек активирован.\n"
            "Команды тренера:\n"
            "/note - Отправить заметку/сообщение другу\n"
            "/setplan - Изменить план тренировок\n"
            "/status - Посмотреть статистику и прогресс друга"
        )
    else:
        update_db("friend_id", message.from_user.id)
        update_db("friend_name", user_input)
        await message.answer(f"Принято, буду звать тебя **{user_input}**! Теперь укажи, в какое время (в часах) тебе удобно тренироваться? (Напиши число от 0 до 23)")
        await state.set_state(BotStates.WAITING_FOR_HOURS)

@dp.message(BotStates.WAITING_FOR_HOURS)
async def process_hours(message: types.Message, state: FSMContext):
    try:
        hours = int(message.text)
        if 0 <= hours <= 23:
            update_db("time_hours", hours)
            await message.answer("Отлично! А теперь напиши минуты (от 0 до 59):")
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
            await message.answer(f"Все настроено, {data['friend_name']}! Напоминания будут приходить каждый день в {data['time_hours']:02d}:{data['time_minutes']:02d}.\n\nНапиши слово 'справка' или 'помощь', если захочешь узнать, что я умею!")
            
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


# --- УМНЫЙ ПАРСЕР СООБЩЕНИЙ ДЛЯ ДРУГА ---
@dp.message(F.text)
async def smart_message_handler(message: types.Message, state: FSMContext):
    data = get_db()
    
    # Если это пишет тренер — игнорируем текстовый разбор обычных команд
    if message.from_user.id == data["trainer_id"]:
        await message.answer("Шеф, используй команды: /note, /setplan или /status.")
        return

    text = message.text.lower()
    
    # Словари ключевых слов (триггеры)
    triggers = {
        "change_name": ["сменить имя", "изменить имя", "сменить ник", "изменить ник", "поменять имя"],
        "view_plan": ["покажи план", "какой план", "план тренировок", "выдай план", "посмотреть план"],
        "to_trainer": ["написать тренеру", "связаться с тренером", "сообщение тренеру", "позови тренера"],
        "help": ["помощь", "справка", "инфо", "информация", "что ты умеешь", "команды"],
        "progress": ["прогресс", "статистика", "мои дни", "пропуски", "какой счет"],
        "fun": ["анекдот", "шутка", "мотивация", "скучно", "поддержи"]
    }
    
    # Проверяем, какие команды найдены в сообщении
    found_commands = []
    for cmd, keywords in triggers.items():
        if any(keyword in text for keyword in keywords):
            found_commands.append(cmd)
            
    # Защита от кучи команд в одном сообщении
    if len(found_commands) > 1:
        await message.answer("🤖 Ого, сколько запросов сразу! Давай по очереди. Выбери какую-то одну команду, и я всё сделаю!")
        return
    
    if len(found_commands) == 0:
        await message.answer("Я тебя понял, но не знаю такой команды. Напиши слово **'справка'**, чтобы посмотреть, что я умею!")
        return

    # Выполнение конкретной команды
    command = found_commands[0]
    
    if command == "help":
        await message.answer(
            f"📋 **Вот что я умею, {data['friend_name']}:**\n\n"
            f"🔹 Напиши фразы типа *'какой план'* или *'план тренировок'* — чтобы увидеть свои упражнения.\n"
            f"🔹 Напиши *'хочу сменить ник'* или *'поменять имя'* — чтобы изменить свое имя в боте.\n"
            f"🔹 Напиши *'написать тренеру'* — чтобы отправить ему весточку.\n"
            f"🔹 Напиши *'прогресс'* или *'статистика'* — чтобы узнать свою серию тренировок и пропуски.\n"
            f"🔹 Напиши *'анекдот'* или *'мотивация'* — если нужен пинок для рывка!"
        )
        
    elif command == "change_name":
        await message.answer("Без проблем! Как тебя теперь называть?")
        await state.set_state(BotStates.WAITING_FOR_NAME)
        
    elif command == "view_plan":
        await message.answer(f"📋 **Твой текущий план тренировок от тренера:**\n\n{data['workout_plan']}")
        
    elif command == "progress":
        status_workout = "✅ Уже выполнена!" if data["last_workout_done"] == 1 else "❌ Еще не отмечена кнопкой!"
        await message.answer(
            f"📊 **Твоя статистика, {data['friend_name']}:**\n\n"
            f"🔥 Текущая серия дней: **{data['streak_days']}**\n"
            f"⚠️ Пропущено тренировок: **{data['missed_days']}**\n"
            f"💪 Сегодняшняя тренировка: {status_workout}"
        )
        
    elif command == "to_trainer":
        await message.answer("Хорошо, напиши своё сообщение для тренера в следующем ответе, а я слово в слово передам ему!")
        await state.set_state(BotStates.FRIEND_SEND_MESSAGE_TO_TRAINER)

    elif command == "fun":
        phrases = [
            "Каждый пропущенный день отдаляет тебя от кубиков пресса на неделю! Вставай! 🦾",
            "Тяжело только первые 10 лет, потом привыкнешь. Иди делай базу! 😂",
            "Твое тело может всё. Это твой мозг нужно убедить. Погнали!",
            "Сегодняшняя тренировка — это вклад в твое здоровое будущее. Не подводи тренера!"
        ]
        await message.answer(f"💡 {random.choice(phrases)}")


# --- ДРУГ ОТПРАВЛЯЕТ СООБЩЕНИЕ ТРЕНЕРУ ---
@dp.message(BotStates.FRIEND_SEND_MESSAGE_TO_TRAINER)
async def friend_to_trainer_state(message: types.Message, state: FSMContext):
    data = get_db()
    if data["trainer_id"]:
        await bot.send_message(
            data["trainer_id"], 
            f"📩 **Сообщение от друга ({data['friend_name']}):**\n\n{message.text}"
        )
        await message.answer("Отлично, я доставил твое сообщение тренеру! 🫡")
    else:
        await message.answer("К сожалению, твой тренер еще не зарегистрировался в боте.")
    await state.clear()


# --- КОМАНДЫ ТРЕНЕРА (Через слэш) ---
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
        await message.answer("Друг еще не зарегистрировался.")
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
        status_today = "Выполнил! ✅" if data["last_workout_done"] == 1 else "Еще филонит ❌"
        await message.answer(
            f"📊 **Отчет по твоему подопечному:**\n\n"
            f"👤 Имя: {data['friend_name']}\n"
            f"⏰ Время напоминалки: {data['time_hours']:02d}:{data['time_minutes']:02d}\n"
            f"🔥 Серия дней: {data['streak_days']}\n"
            f"⚠️ Пропусков: {data['missed_days']}\n"
            f"💪 Статус сегодня: {status_today}\n\n"
            f"📝 Текущий план: {data['workout_plan']}"
        )

# --- ЗАПУСК ---
async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    
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

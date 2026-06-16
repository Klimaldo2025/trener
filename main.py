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

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect("bot_memory.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bot_data (
            id INTEGER PRIMARY KEY DEFAULT 1,
            trainer_id INTEGER,
            trainer_name TEXT DEFAULT 'Тренер',
            friend_id INTEGER,
            friend_name TEXT DEFAULT 'Друг',
            time_hours INTEGER,
            time_minutes INTEGER,
            workout_plan TEXT DEFAULT 'План пока не составлен. Жди указаний тренера!',
            streak_days INTEGER DEFAULT 0,
            missed_days INTEGER DEFAULT 0,
            last_workout_done INTEGER DEFAULT 1
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS friend_notes_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("INSERT OR IGNORE INTO bot_data (id) VALUES (1)")
    conn.commit()
    conn.close()

def get_db():
    conn = sqlite3.connect("bot_memory.db")
    cursor = conn.cursor()
    cursor.execute("""
        SELECT trainer_id, trainer_name, friend_id, friend_name, time_hours, time_minutes, 
               workout_plan, streak_days, missed_days, last_workout_done 
        FROM bot_data WHERE id=1
    """)
    row = cursor.fetchone()
    conn.close()
    return {
        "trainer_id": row[0],
        "trainer_name": row[1],
        "friend_id": row[2],
        "friend_name": row[3],
        "time_hours": row[4],
        "time_minutes": row[5],
        "workout_plan": row[6],
        "streak_days": row[7],
        "missed_days": row[8],
        "last_workout_done": row[9]
    }

def update_db(field, value):
    conn = sqlite3.connect("bot_memory.db")
    cursor = conn.cursor()
    cursor.execute(f"UPDATE bot_data SET {field} = ? WHERE id=1", (value,))
    conn.commit()
    conn.close()

def check_friend_limit():
    conn = sqlite3.connect("bot_memory.db")
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM friend_notes_log WHERE timestamp > datetime('now', '-1 hour')")
    count = cursor.fetchone()[0]
    conn.close()
    return count < 5

def log_friend_message():
    conn = sqlite3.connect("bot_memory.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO friend_notes_log (timestamp) VALUES (datetime('now'))")
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

async def send_reminder(chat_id):
    data = get_db()
    if data["friend_id"]:
        if data["last_workout_done"] == 0:
            new_missed = data["missed_days"] + 1
            update_db("missed_days", new_missed)
            update_db("streak_days", 0)
            if data["trainer_id"]:
                await bot.send_message(data["trainer_id"], f"⚠️ Пропуск! {data['friend_name']} не отметился за прошлую тренировку.")

        update_db("last_workout_done", 0)
        await bot.send_message(
            chat_id, 
            f"Привет, {data['friend_name']}! Время тренировки! 💪\n\n**Твой план:**\n{data['workout_plan']}\n\nВыполнишь — жми кнопку!",
            reply_markup=get_workout_keyboard()
        )

@dp.callback_query(F.data == "workout_done")
async def workout_done_callback(callback: types.CallbackQuery):
    data = get_db()
    if callback.from_user.id != data["friend_id"]:
        await callback.answer("Кнопка только для твоего друга!", show_alert=True)
        return
    if data["last_workout_done"] == 1:
        await callback.answer("Уже отмечено!", show_alert=True)
        return

    new_streak = data["streak_days"] + 1
    update_db("streak_days", new_streak)
    update_db("last_workout_done", 1)

    await callback.message.edit_text(f"🔥 Красава, {data['friend_name']}! Тренировка засчитана. Серия: {new_streak} дн.")
    if data["trainer_id"]:
        await bot.send_message(data["trainer_id"], f"💪 {data['friend_name']} потренировался! Серия: {new_streak} дней.")
    await callback.answer()


# --- ОБРАБОТКА СТАРТА И ВВОДА ДАННЫХ ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await message.answer("Привет! Введи, пожалуйста, свое имя:")
    await state.set_state(BotStates.WAITING_FOR_NAME)

@dp.message(BotStates.WAITING_FOR_NAME)
async def process_name(message: types.Message, state: FSMContext):
    user_input = message.text.strip()
    if user_input == "Я жирное чмо":
        update_db("trainer_id", message.from_user.id)
        await state.clear()
        await message.answer("Секретный отсек тренера активирован! 😎\nНапиши слово **'справка'**, чтобы узнать свои текстовые команды.")
    else:
        update_db("friend_id", message.from_user.id)
        update_db("friend_name", user_input)
        await message.answer(f"Принято, {user_input}! Теперь напиши час (0-23), в который тебе присылать напоминалку:")
        await state.set_state(BotStates.WAITING_FOR_HOURS)

@dp.message(BotStates.WAITING_FOR_TRAINER_NAME)
async def process_trainer_name(message: types.Message, state: FSMContext):
    user_input = message.text.strip()
    update_db("trainer_name", user_input)
    await message.answer(f"Шеф, твой ник успешно изменен на: **{user_input}** 😎")
    await state.clear()

@dp.message(BotStates.WAITING_FOR_HOURS)
async def process_hours(message: types.Message, state: FSMContext):
    try:
        hours = int(message.text)
        if 0 <= hours <= 23:
            update_db("time_hours", hours)
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
        if 0 <= minutes <= 59:
            update_db("time_minutes", minutes)
            await state.clear()
            
            data = get_db()
            await message.answer(f"Всё сохранил! Время напоминаний: {data['time_hours']:02d}:{data['time_minutes']:02d}.\nНапиши слово **'справка'**, чтобы увидеть команды.")
            
            scheduler.add_job(
                send_reminder, trigger="cron", 
                hour=data["time_hours"], minute=data["time_minutes"], 
                args=[message.chat.id], id="daily_workout_reminder", replace_existing=True
            )
        else:
            await message.answer("Введи минуты от 0 до 59.")
    except ValueError:
        await message.answer("Нужно ввести число!")


# --- ОБРАБОТКА ДЕЙСТВИЙ ИЗ СОСТОЯНИЙ ОЖИДАНИЯ ТЕКСТА ---
@dp.message(BotStates.TRAINER_CHANGE_PLAN)
async def trainer_confirm_plan(message: types.Message, state: FSMContext):
    update_db("workout_plan", message.text)
    await message.answer("Новый план успешно сохранен!")
    data = get_db()
    if data["friend_id"]:
        await bot.send_message(data["friend_id"], f"📋 **Новый план тренировок от тренера:**\n\n{message.text}")
    await state.clear()

@dp.message(BotStates.TRAINER_SEND_NOTE)
async def trainer_confirm_note(message: types.Message, state: FSMContext):
    data = get_db()
    if data["friend_id"]:
        await bot.send_message(data["friend_id"], f"📩 **Заметка от тренера ({data['trainer_name']}):**\n\n{message.text}")
        await message.answer("Заметка мгновенно доставлена другу!")
    else:
        await message.answer("Друг еще не заходил в бота.")
    await state.clear()

@dp.message(BotStates.FRIEND_SEND_MESSAGE_TO_TRAINER)
async def friend_confirm_to_trainer(message: types.Message, state: FSMContext):
    data = get_db()
    if data["trainer_id"]:
        await bot.send_message(data["trainer_id"], f"✉️ **Сообщение от друга ({data['friend_name']}):**\n\n{message.text}")
        await message.answer("Я передал твою заметку тренеру! 🫡")
    else:
        await message.answer("Твой тренер еще не зашел в бота.")
    await state.clear()


# --- ГЛАВНЫЙ УМНЫЙ ПАРСЕР ТЕКСТА ---
@dp.message(F.text)
async def main_text_parser(message: types.Message, state: FSMContext):
    data = get_db()
    text = message.text.lower()
    user_id = message.from_user.id

    # --- ЛОГИКА ДЛЯ ТРЕНЕРА ---
    if user_id == data["trainer_id"]:
        # Сверхкороткие триггеры (поиск по ключевым корням слов)
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
                f"📋 **Твой пульт управления, {data['trainer_name']}:**\n\n"
                f"🔹 **'статистика'** / **'прогресс'** — полный отчет по другу.\n"
                f"🔹 **'план'** — изменить упражнения (улетят другу автоматом).\n"
                f"🔹 **'заметка'** / **'сообщение'** — переслать свой следующий текст другу.\n"
                f"🔹 **'ник'** / **'имя'** — изменить твое имя тренера в боте."
            )
        elif cmd == "status":
            time_str = f"{data['time_hours']:02d}:{data['time_minutes']:02d}" if data["time_hours"] is not None else "Не настроено"
            status_today = "Выполнил! ✅" if data["last_workout_done"] == 1 else "Еще филонит ❌"
            await message.answer(
                f"📊 **Текущий отчет подопечного:**\n\n"
                f"👤 **Ник/Имя:** {data['friend_name']}\n"
                f"⏰ **Время тренировок:** {time_str}\n"
                f"🔥 **Серия дней:** {data['streak_days']}\n"
                f"⚠️ **Количество пропусков:** {data['missed_days']}\n"
                f"💪 **Статус на сегодня:** {status_today}\n\n"
                f"📝 **План:** {data['workout_plan']}"
            )
        elif cmd == "plan":
            await message.answer("Напиши текст с упражнениями. Я обновлю план и сразу скину его твоему другу:")
            await state.set_state(BotStates.TRAINER_CHANGE_PLAN)
        elif cmd == "note":
            await message.answer("Напиши текст сообщения, которое нужно немедленно передать другу:")
            await state.set_state(BotStates.TRAINER_SEND_NOTE)
        elif cmd == "change_name":
            await message.answer("Шеф, какое имя тебе поставить?")
            await state.set_state(BotStates.WAITING_FOR_TRAINER_NAME)
        return

    # --- ЛОГИКА ДЛЯ ДРУГА ---
    if user_id == data["friend_id"]:
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
                f"📋 **Вот что ты можешь сделать, {data['friend_name']}:**\n\n"
                f"🔹 **'план'** — посмотреть текущие упражнения.\n"
                f"🔹 **'ник'** / **'имя'** — поменять имя в боте.\n"
                f"🔹 **'время'** — настроить часы для напоминаний.\n"
                f"🔹 **'прогресс'** / **'статистика'** — твоя серия дней и пропуски.\n"
                f"🔹 **'тренер'** — отправить сообщение тренеру (Лимит: 5 в час).\n"
                f"🔹 **'мотивация'** — получить пинок к действию!"
            )
        elif cmd == "change_name":
            await message.answer("Без проблем! Какое имя тебе поставить?")
            await state.set_state(BotStates.WAITING_FOR_NAME)
        elif cmd == "change_time":
            await message.answer("Хорошо, давай перенастроим время. В какой час (0-23) тебе удобно тренироваться?")
            await state.set_state(BotStates.WAITING_FOR_HOURS)
        elif cmd == "view_plan":
            await message.answer(f"📋 **Твой план тренировок:**\n\n{data['workout_plan']}")
        elif cmd == "progress":
            status_workout = "✅ Выполнена!" if data["last_workout_done"] == 1 else "❌ Ждет отметки кнопкой!"
            await message.answer(
                f"📊 **Твои успехи:**\n\n"
                f"🔥 Серия дней: **{data['streak_days']}**\n"
                f"⚠️ Пропущено дней: **{data['missed_days']}**\n"
                f"💪 Сегодняшний статус: {status_workout}"
            )
        elif cmd == "to_trainer":
            if not check_friend_limit():
                await message.answer("⚠️ Ты исчерпал лимит заметок тренеру (максимум 5 сообщений в час). Отдохни немного!")
                return
            log_friend_message()
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
    
    data = get_db()
    if data["friend_id"] and data["time_hours"] is not None and data["time_minutes"] is not None:
        scheduler.add_job(
            send_reminder, trigger="cron", 
            hour=data["time_hours"], minute=data["time_minutes"], 
            args=[data["friend_id"]], id="daily_workout_reminder", replace_existing=True
        )
    
    scheduler.start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

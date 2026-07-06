import telebot
import sqlite3
import threading
import random
import schedule
import time
from datetime import datetime
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

TOKEN = "8912217606:AAFAxQKalVqR1RoDNB0zNP41LfZHJn0XXzU"
bot = telebot.TeleBot(TOKEN)

DB_LOCK = threading.Lock()


def get_db_connection():
    return sqlite3.connect('tamagochi.db', timeout=10)


def init_db():
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pets (
                pet_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER,
                name       TEXT    DEFAULT 'Питомец',
                hunger     INTEGER DEFAULT 50,
                happiness  INTEGER DEFAULT 50,
                energy     INTEGER DEFAULT 100,
                is_alive   BOOLEAN DEFAULT 1,
                balance    INTEGER DEFAULT 1000,
                last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute('PRAGMA journal_mode=WAL')
        conn.commit()
        conn.close()
        print("✅ База данных готова!")


init_db()


def get_active_pet(user_id):
    """Получить данные активного питомца"""
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT user_id, name, hunger, happiness, energy, is_alive, balance, last_activity
            FROM pets
            WHERE user_id = ? AND is_alive = 1
        ''', (user_id,))
        data = cursor.fetchone()
        conn.close()
        return data


def create_pet(user_id, name):
    """Создать нового питомца"""
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO pets (user_id, name, hunger, happiness, energy, is_alive, balance, last_activity)
            VALUES (?, ?, 80, 50, 100, 1, 1000, CURRENT_TIMESTAMP)
        ''', (user_id, name))
        conn.commit()
        conn.close()


def update_pet_stats(user_id, **kwargs):
    """Универсальное обновление любых полей питомца"""
    if not kwargs:
        return

    # Автоматически обновляем время последней активности
    kwargs['last_activity'] = datetime.now().isoformat()

    set_clause = ", ".join([f"{key} = ?" for key in kwargs.keys()])
    values = list(kwargs.values())
    values.append(user_id)

    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(f'''
            UPDATE pets 
            SET {set_clause} 
            WHERE user_id = ? AND is_alive = 1
        ''', values)
        conn.commit()
        conn.close()


def change_balance(user_id, amount):
    """Изменить баланс питомца на amount (может быть отрицательным)"""
    pet = get_active_pet(user_id)
    if pet:
        user_id, name, hunger, happiness, energy, is_alive, balance, last_activity = pet
        new_balance = balance + amount
        update_pet_stats(user_id, balance=new_balance)
        return new_balance
    return None


def bot_send_status(message):
    """Отправить статус питомца"""
    user_id = message.from_user.id
    pet = get_active_pet(user_id)

    if pet is None:
        bot.send_message(message.chat.id, "❌ У тебя нет активного питомца! Используй /start, чтобы завести.")
        return

    user_id, name, hunger, happiness, energy, is_alive, balance, last_activity = pet

    # Определяем состояние голода
    hunger_status = ""
    if hunger >= 80:
        hunger_status = "🍔 Сыт"
    elif hunger >= 50:
        hunger_status = "😐 Нормально"
    elif hunger >= 20:
        hunger_status = "😟 Хочет есть"
    else:
        hunger_status = "⚠️ Голодный! Срочно покорми!"

    status_text = f"""
🐾 **Статус питомца**

Имя: {name}
❤️ Статус: Жив и здоров!

📊 Параметры:
🍖 Голод: {hunger}/100 ({hunger_status})
😊 Счастье: {happiness}/100
⚡ Энергия: {energy}/100
💰 Баланс: {balance} монет

🎮 Игры:
/guess - Угадай число

⚠️ Голод уменьшается каждые 10 минут!
    """
    bot.send_message(message.chat.id, status_text, parse_mode="Markdown")


# ============ СИСТЕМА ГОЛОДА ============

def decrease_hunger_all_pets():
    """Уменьшает голод у всех живых питомцев"""
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Получаем всех живых питомцев
        cursor.execute('''
            SELECT user_id, hunger, name, is_alive
            FROM pets 
            WHERE is_alive = 1
        ''')
        pets = cursor.fetchall()

        for user_id, hunger, name, is_alive in pets:
            # Уменьшаем голод на 5 (но не меньше 0)
            new_hunger = max(0, hunger - 5)

            cursor.execute('''
                UPDATE pets SET hunger = ? WHERE user_id = ?
            ''', (new_hunger, user_id))

            # Если голод достиг 0, питомец может умереть (опционально)
            if new_hunger == 0:
                print(f"⚠️ {name} голоден! Покормите его!")

        conn.commit()
        conn.close()
        print(f"✅ Обновлён голод у {len(pets)} питомцев в {datetime.now().strftime('%H:%M:%S')}")


def start_hunger_scheduler():
    """Запускает фоновый поток для обновления голода"""

    def schedule_loop():
        while True:
            schedule.run_pending()
            time.sleep(1)

    # Запускаем каждые 10 минут
    schedule.every(10).minutes.do(decrease_hunger_all_pets)

    # Запускаем в отдельном потоке
    thread = threading.Thread(target=schedule_loop, daemon=True)
    thread.start()
    print("🔄 Система голода запущена! (обновление каждые 10 минут)")


# Запускаем систему голода
start_hunger_scheduler()


# ============ КОМАНДЫ ============

@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    pet = get_active_pet(user_id)

    if pet is None:
        msg = bot.send_message(
            message.chat.id,
            "🐱 Привет! У тебя сейчас нет живых питомцев.\n**Как назовём твоего нового друга?**",
            parse_mode="Markdown"
        )
        bot.register_next_step_handler(msg, process_name_step)
    else:
        user_id, name, hunger, happiness, energy, is_alive, balance, last_activity = pet
        bot.send_message(
            message.chat.id,
            f"🐾 У тебя уже есть живой питомец **{name}**!\n"
            f"💰 Баланс: {balance} монет\n"
            f"🍖 Голод: {hunger}/100\n\n"
            f"Используй /status для полной информации.",
            parse_mode="Markdown"
        )


def process_name_step(message):
    user_id = message.from_user.id
    pet_name = message.text.strip()

    if pet_name.startswith('/'):
        bot.send_message(message.chat.id, "❌ Имя не должно начинаться с команды! Введи нормальное имя.")
        return

    if len(pet_name) > 20:
        msg = bot.send_message(message.chat.id, "⚠️ Слишком длинное имя! Давай покороче (до 20 символов):")
        bot.register_next_step_handler(msg, process_name_step)
        return

    create_pet(user_id, pet_name)

    bot.send_message(
        message.chat.id,
        f"🎉 Поздравляем! Ты завёл питомца по имени **{pet_name}**!\n"
        f"💰 Начальный баланс: 1000 монет\n"
        f"🍖 Голод: 80/100\n\n"
        f"🎮 Сыграй в /guess и заработай ещё монет!",
        parse_mode="Markdown"
    )


@bot.message_handler(commands=['status'])
def status(message):
    bot_send_status(message)


@bot.message_handler(commands=['feed'])
def feed(message):
    user_id = message.from_user.id
    pet = get_active_pet(user_id)

    if pet is None:
        bot.send_message(message.chat.id, "❌ У тебя нет активного питомца! Используй /start, чтобы завести.")
        return

    user_id, name, hunger, happiness, energy, is_alive, balance, last_activity = pet

    if hunger >= 100:
        bot.send_message(message.chat.id, f"🍔 {name} уже сыт! Не перекармливай.")
        return

    new_hunger = min(100, hunger + 25)
    new_happiness = min(100, happiness + 5)

    update_pet_stats(user_id, hunger=new_hunger, happiness=new_happiness)

    new_balance = change_balance(user_id, -10)

    bot.send_message(
        message.chat.id,
        f"🍖 Ты покормил {name}!\n"
        f"🍖 Голод: {new_hunger}/100\n"
        f"😊 Счастье: {new_happiness}/100\n"
        f"💰 Потрачено 10 монет, новый баланс: {new_balance} монет"
    )


# ============ ИГРА "УГАДАЙ ЧИСЛО" ============

# Храним активные игры: {user_id: secret_number}
active_games = {}


@bot.message_handler(commands=['guess'])
def start_guess_game(message):
    """Начать игру 'Угадай число'"""
    user_id = message.from_user.id
    pet = get_active_pet(user_id)

    if pet is None:
        bot.send_message(message.chat.id, "❌ У тебя нет питомца! Используй /start")
        return

    # Проверяем, не играет ли уже пользователь
    if user_id in active_games:
        bot.send_message(message.chat.id, "⚠️ Ты уже играешь! Заверши игру или угадай число.")
        return

    # Загадываем число от 1 до 10
    secret = random.randint(1, 10)
    active_games[user_id] = secret

    # Создаём клавиатуру с числами
    markup = InlineKeyboardMarkup(row_width=5)
    buttons = []
    for i in range(1, 11):
        buttons.append(InlineKeyboardButton(str(i), callback_data=f"guess_{i}"))
    markup.add(*buttons)

    # Добавляем кнопку "Сдаться"
    markup.add(InlineKeyboardButton("🏳️ Сдаться", callback_data="guess_giveup"))

    bot.send_message(
        message.chat.id,
        f"🎯 **Угадай число!**\n\n"
        f"Я загадал число от 1 до 10.\n"
        f"💰 Награда за победу: **20 монет**\n"
        f"📉 За каждую неудачную попытку: -5 монет\n\n"
        f"Выбери число:",
        parse_mode="Markdown",
        reply_markup=markup
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith('guess_'))
def handle_guess(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id

    # Проверяем, есть ли игра
    if user_id not in active_games:
        bot.answer_callback_query(call.id, "❌ Игра не найдена! Начни новую /guess", show_alert=True)
        return

    secret = active_games[user_id]

    # Обработка кнопки "Сдаться"
    if call.data == "guess_giveup":
        del active_games[user_id]
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=f"🏳️ Ты сдался!\n\nЗагаданное число было: **{secret}**\n\nИспользуй /guess чтобы начать заново.",
            parse_mode="Markdown"
        )
        bot.answer_callback_query(call.id, "Ты сдался!")
        return

    # Получаем число из callback_data
    guess = int(call.data.split('_')[1])

    # Проверяем
    if guess == secret:
        # Победа
        del active_games[user_id]
        new_balance = change_balance(user_id, 20)

        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=f"🎉 **ПОЗДРАВЛЯЮ!**\n\n"
                 f"Ты угадал число **{secret}**!\n"
                 f"💰 Ты получил **20 монет**!\n"
                 f"💰 Новый баланс: **{new_balance}** монет\n\n"
                 f"Используй /guess чтобы сыграть ещё раз.",
            parse_mode="Markdown"
        )
        bot.answer_callback_query(call.id, f"🎉 Угадал! +20 монет!")

    else:
        # Обновляем сообщение
        hint = "больше" if guess < secret else "меньше"
        new_balance = change_balance(user_id, -5)

        # Оставляем игру активной и создаём новую клавиатуру
        markup = InlineKeyboardMarkup(row_width=5)
        buttons = []
        for i in range(1, 11):
            buttons.append(InlineKeyboardButton(str(i), callback_data=f"guess_{i}"))
        markup.add(*buttons)
        markup.add(InlineKeyboardButton("🏳️ Сдаться", callback_data="guess_giveup"))

        # Обновляем сообщение
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=f"❌ Число **{guess}** не верно!\n"
                 f"📈 Загаданное число **{hint}** чем {guess}\n"
                 f"💰 -5 монет за попытку\n"
                 f"💰 Новый баланс: **{new_balance}** монет\n\n"
                 f"Попробуй снова! Выбери число:",
            parse_mode="Markdown",
            reply_markup=markup
        )
        bot.answer_callback_query(call.id, f"❌ Не угадал! -5 монет. Попробуй ещё!")


@bot.message_handler(commands=['guess_stats'])
def guess_stats(message):
    """Показать статистику игры"""
    user_id = message.from_user.id
    pet = get_active_pet(user_id)

    if pet is None:
        bot.send_message(message.chat.id, "❌ У тебя нет питомца!")
        return

    user_id, name, hunger, happiness, energy, is_alive, balance, last_activity = pet

    bot.send_message(
        message.chat.id,
        f"📊 **Статистика игры 'Угадай число'**\n\n"
        f"🐾 Питомец: {name}\n"
        f"💰 Баланс: {balance} монет\n"
        f"🍖 Голод: {hunger}/100\n\n"
        f"📝 Чтобы сыграть, используй /guess",
        parse_mode="Markdown"
    )


if __name__ == "__main__":
    print("🤖 Бот-тамагочи запущен!")
    print("🎮 Игра 'Угадай число' активна!")
    print("🔄 Голод уменьшается каждые 10 минут!")
    print("📊 Доступные команды: /start, /status, /feed, /guess, /guess_stats")
    bot.infinity_polling()
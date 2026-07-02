import telebot
import sqlite3
import threading

TOKEN = ""
bot = telebot.TeleBot(TOKEN)

# ============ БЛОКИРОВКА И НАСТРОЙКА БД ============
DB_LOCK = threading.Lock()


def get_db_connection():
    return sqlite3.connect('tamagochi.db', timeout=10)


def init_db():
    """Создаёт таблицу питомцев с автоинкрементом ID (сохраняет историю)"""
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
                       CREATE TABLE IF NOT EXISTS pets
                       (
                           pet_id
                           INTEGER
                           PRIMARY
                           KEY
                           AUTOINCREMENT,
                           user_id
                           INTEGER,
                           name
                           TEXT
                           DEFAULT
                           'Питомец',
                           hunger
                           INTEGER
                           DEFAULT
                           50,
                           happiness
                           INTEGER
                           DEFAULT
                           50,
                           energy
                           INTEGER
                           DEFAULT
                           100,
                           is_alive
                           BOOLEAN
                           DEFAULT
                           1
                       )
                       ''')
        cursor.execute('PRAGMA journal_mode=WAL')
        conn.commit()
        conn.close()
        print("✅ База данных готова!")


# Инициализируем БД при запуске
init_db()


# ============ УНИВЕРСАЛЬНЫЕ ФУНКЦИИ РАБОТЫ С БД ============

def get_active_pet(user_id):
    """Ищет ТОЛЬКО ЖИВОГО питомца конкретного пользователя"""
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
                       SELECT user_id, name, hunger, happiness, energy, is_alive
                       FROM pets
                       WHERE user_id = ?
                         AND is_alive = 1
                       ''', (user_id,))
        data = cursor.fetchone()
        conn.close()
        return data


def create_pet(user_id, name):
    """Создает абсолютно новую запись питомца (старые мертвые не перезаписываются)"""
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
                       INSERT INTO pets (user_id, name, hunger, happiness, energy, is_alive)
                       VALUES (?, ?, 50, 50, 100, 1)
                       ''', (user_id, name))
        conn.commit()
        conn.close()


def update_pet_stats(user_id, **kwargs):
    """
    Универсальная функция обновления любых параметров живого питомца за один запрос.
    Пример: update_pet_stats(user_id, hunger=70, happiness=60)
    """
    if not kwargs:
        return

    # Динамически собираем SQL-запрос под переданные параметры
    set_clause = ", ".join([f"{key} = ?" for key in kwargs.keys()])
    values = list(kwargs.values())
    values.append(user_id)  # Для условия WHERE user_id = ?

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


# ============ КОМАНДЫ БОТА ============

@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    pet = get_active_pet(user_id)

    # Если живого питомца нет — предлагаем ввести имя и завести нового
    if pet is None:
        msg = bot.send_message(
            message.chat.id,
            "🐱 Привет! У тебя сейчас нет живых питомцев.\n**Как назовём твоего нового друга?**",
            parse_mode="Markdown"
        )
        bot.register_next_step_handler(msg, process_name_step)
    else:
        user_id, name, hunger, happiness, energy, is_alive = pet
        bot.send_message(
            message.chat.id,
            f"🐾 У тебя уже есть живой питомец {name}!\n"
            f"🍖 Голод: {hunger}/100\n"
            f"😊 Счастье: {happiness}/100\n"
            f"⚡ Энергия: {energy}/100"
        )


def process_name_step(message):
    """Перехватывает текстовый ответ пользователя и сохраняет имя"""
    user_id = message.from_user.id
    pet_name = message.text.strip()

    # Проверка на случай, если пользователь вместо имени ввёл другую команду
    if pet_name.startswith('/'):
        bot.send_message(message.chat.id, "❌ Имя не должно начинаться с команды! Введи нормальное имя через /start.")
        return

    # Валидация длины
    if len(pet_name) > 20:
        msg = bot.send_message(message.chat.id, "⚠️ Слишком длинное имя! Давай покороче (до 20 символов):")
        bot.register_next_step_handler(msg, process_name_step)
        return

    # Создаем питомца в БД (старые мертвые остаются в базе)
    create_pet(user_id, pet_name)

    bot.send_message(
        message.chat.id,
        f"🎉 Поздравляем! Ты завёл питомца по имени **{pet_name}**!\n"
        f"Используй /status, чтобы следить за ним, и /feed, чтобы кормить.",
        parse_mode="Markdown"
    )


@bot.message_handler(commands=['feed'])
def feed(message):
    user_id = message.from_user.id
    pet = get_active_pet(user_id)

    if pet is None:
        bot.send_message(message.chat.id, "❌ У тебя нет активного питомца! Используй /start, чтобы завести.")
        return

    user_id, name, hunger, happiness, energy, is_alive = pet

    # Проверяем лимит сытости
    if hunger >= 100:
        bot.send_message(message.chat.id, f"🍔 {name} уже сыт! Не перекармливай.")
        return

    new_hunger = min(100, hunger + 20)
    new_happiness = min(100, happiness + 5)

    # Обновляем все статы ОДНИМ вызовом универсальной функции
    update_pet_stats(user_id, hunger=new_hunger, happiness=new_happiness)

    bot.send_message(
        message.chat.id,
        f"🍖 Ты покормил {name}!\n"
        f"Голод: {new_hunger}/100\n"
        f"Счастье: {new_happiness}/100"
    )


@bot.message_handler(commands=['status'])
def status(message):
    user_id = message.from_user.id
    pet = get_active_pet(user_id)

    if pet is None:
        bot.send_message(message.chat.id, "❌ У тебя нет активного питомца! Используй /start")
        return

    user_id, name, hunger, happiness, energy, is_alive = pet

    status_text = f"""
🐾 **Статус питомца**

Имя: {name}
❤️ Статус: Жив и здоров!

📊 Параметры:
🍖 Голод: {hunger}/100
😊 Счастье: {happiness}/100
⚡ Энергия: {energy}/100
    """
    bot.send_message(message.chat.id, status_text, parse_mode="Markdown")


# Запуск бота
if __name__ == "__main__":
    print("🤖 Бот запущен...")
    bot.infinity_polling()
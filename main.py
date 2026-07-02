import telebot
import sqlite3
import threading
from datetime import datetime

TOKEN = ""
bot = telebot.TeleBot(TOKEN)

# ============ БЛОКИРОВКА ДЛЯ БД ============
DB_LOCK = threading.Lock()

def get_db_connection():
    """Создаёт соединение с БД с таймаутом"""
    return sqlite3.connect('tamagochi.db', timeout=10)

# ============ СОЗДАНИЕ ТАБЛИЦЫ ============

def init_db():
    """Создаёт таблицу питомцев если её нет"""
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pets (
                user_id INTEGER PRIMARY KEY,
                name TEXT DEFAULT 'Питомец',
                hunger INTEGER DEFAULT 50,
                happiness INTEGER DEFAULT 50,
                energy INTEGER DEFAULT 100,
                is_alive BOOLEAN DEFAULT 1
            )
        ''')

        # Включаем WAL-режим для лучшей работы с многопоточностью
        cursor.execute('PRAGMA journal_mode=WAL')

        conn.commit()
        conn.close()
        print("✅ База данных готова!")


# Вызываем при запуске
init_db()


# ============ ФУНКЦИИ РАБОТЫ С БД ============

def get_pet(user_id):
    """Получить данные питомца из БД"""
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM pets WHERE user_id = ?', (user_id,))
        data = cursor.fetchone()
        conn.close()
        return data


def create_pet(user_id, name="Питомец"):
    """Создать нового питомца"""
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR IGNORE INTO pets (user_id, name)
            VALUES (?, ?)
        ''', (user_id, name))
        conn.commit()
        conn.close()


def update_hunger(user_id, new_value):
    """Обновить уровень голода"""
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE pets SET hunger = ? WHERE user_id = ?
        ''', (new_value, user_id))
        conn.commit()
        conn.close()


def update_happiness(user_id, new_value):
    """Обновить уровень счастья"""
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE pets SET happiness = ? WHERE user_id = ?
        ''', (new_value, user_id))
        conn.commit()
        conn.close()


# ============ КОМАНДЫ БОТА ============

@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id

    # Проверяем, есть ли питомец
    pet = get_pet(user_id)

    if pet is None:
        # Создаём нового питомца
        create_pet(user_id, "Барсик")
        bot.send_message(message.chat.id, "🐱 Ты завёл питомца Барсик!\nИспользуй /feed чтобы покормить.")
    else:
        # Показываем статус
        user_id, name, hunger, happiness, energy, is_alive = pet
        bot.send_message(
            message.chat.id,
            f"🐾 У тебя уже есть питомец {name}!\n"
            f"🍖 Голод: {hunger}/100\n"
            f"😊 Счастье: {happiness}/100\n"
            f"⚡ Энергия: {energy}/100"
        )


@bot.message_handler(commands=['feed'])
def feed(message):
    user_id = message.from_user.id

    # Получаем питомца
    pet = get_pet(user_id)

    if pet is None:
        bot.send_message(message.chat.id, "❌ У тебя нет питомца! Используй /start")
        return

    user_id, name, hunger, happiness, energy, is_alive = pet

    # Проверяем жив ли
    if not is_alive:
        bot.send_message(message.chat.id, "💀 Твой питомец умер... Используй /start для нового.")
        return

    # Проверяем не перекормили ли
    if hunger >= 100:
        bot.send_message(message.chat.id, "🍔 Питомец уже сыт! Не перекармливай.")
        return

    # Кормим (увеличиваем сытость на 20, но не больше 100)
    new_hunger = min(100, hunger + 20)
    new_happiness = min(100, happiness + 5)

    update_hunger(user_id, new_hunger)
    update_happiness(user_id, new_happiness)

    bot.send_message(
        message.chat.id,
        f"🍖 Ты покормил {name}!\n"
        f"Голод: {new_hunger}/100\n"
        f"Счастье: {new_happiness}/100"
    )


@bot.message_handler(commands=['status'])
def status(message):
    user_id = message.from_user.id

    pet = get_pet(user_id)

    if pet is None:
        bot.send_message(message.chat.id, "❌ У тебя нет питомца! Используй /start")
        return

    user_id, name, hunger, happiness, energy, is_alive = pet

    status_text = f"""
🐾 **Статус питомца**

Имя: {name}
❤️ Жив: {'Да' if is_alive else 'Нет'}

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

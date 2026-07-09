import telebot
import sqlite3
import threading
import random
import schedule
import time
from datetime import datetime, timedelta
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

TOKEN = "8726735511:AAFkQFb4qLIJQGyJ2IPl8MAJUbeLm32lcus"
bot = telebot.TeleBot(TOKEN)

DB_LOCK = threading.Lock()

# Настройка интервалов для бесплатных действий (в секундах)
FREE_FEED_INTERVAL = 6 * 3600  # 6 часов
FREE_SLEEP_INTERVAL = 4 * 3600  # 4 часа
FREE_PLAY_INTERVAL = 3 * 3600  # 3 часа


def get_db_connection():
    return sqlite3.connect('tamagochi.db', timeout=10)

def init_db():
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 1. Таблица питомцев (Добавлены поля для новой механики сна)
        cursor.execute('''
                       CREATE TABLE IF NOT EXISTS pets
                       (
                           pet_id INTEGER PRIMARY KEY AUTOINCREMENT,
                           user_id INTEGER,
                           name TEXT DEFAULT 'Питомец',
                           hunger INTEGER DEFAULT 50,
                           happiness INTEGER DEFAULT 50,
                           energy INTEGER DEFAULT 100,
                           is_alive BOOLEAN DEFAULT 1,
                           balance INTEGER DEFAULT 1000,
                           last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                           bonus_streak INTEGER DEFAULT 0,
                           last_bonus_time TEXT DEFAULT NULL,
                           is_sleeping BOOLEAN DEFAULT 0, -- 0 = бодрствует, 1 = спит
                           went_to_sleep_time TEXT DEFAULT NULL -- время, когда уснул
                       )
                       ''')

        # 2. Таблица для бесплатных действий
        cursor.execute('''
                       CREATE TABLE IF NOT EXISTS free_actions
                       (
                           user_id INTEGER PRIMARY KEY,
                           last_feed TIMESTAMP DEFAULT '1970-01-01',
                           last_sleep TIMESTAMP DEFAULT '1970-01-01',
                           last_play TIMESTAMP DEFAULT '1970-01-01',
                           FOREIGN KEY (user_id) REFERENCES pets (user_id)
                       )
                       ''')

        # 3. Таблица инвентаря
        cursor.execute('''
                       CREATE TABLE IF NOT EXISTS inventory
                       (
                           id INTEGER PRIMARY KEY AUTOINCREMENT,
                           user_id INTEGER NOT NULL,
                           item_name TEXT NOT NULL,
                           quantity INTEGER NOT NULL DEFAULT 0,
                           created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                           updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                           UNIQUE (user_id, item_name),
                           FOREIGN KEY (user_id) REFERENCES pets (user_id)
                       )
                       ''')

        # БЛОК АВТОМАТИЧЕСКОЙ МИГРАЦИИ (Решает проблему OperationalError: no such column)
        try:
            # Проверяем, существует ли уже колонка в базе
            cursor.execute("SELECT is_sleeping FROM pets LIMIT 1")
        except sqlite3.OperationalError:
            # Если SQLite выдал ошибку, значит колонок в файле базы нет. Добавляем их:
            print("🔧 База данных устарела. Автоматически добавляю колонки для сна...")
            cursor.execute("ALTER TABLE pets ADD COLUMN is_sleeping BOOLEAN DEFAULT 0")
            cursor.execute("ALTER TABLE pets ADD COLUMN went_to_sleep_time TEXT DEFAULT NULL")
            print("✅ Колонки сна успешно внедрены!")

        cursor.execute('PRAGMA journal_mode=WAL')
        conn.commit()
        conn.close()
        print("✅ База данных и система инвентаря готовы!")

# Сразу вызываем инициализацию при старте скрипта
init_db()

def get_active_pet(user_id):
    """Получить базовые данные активного питомца (8 значений для совместимости со старым кодом)"""
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
    """Изменить баланс питомца на amount (может быть отрицательным) с защитой от минуса"""
    pet = get_active_pet(user_id)
    if pet:
        user_id, name, hunger, happiness, energy, is_alive, balance, last_activity = pet
        new_balance = balance + amount
        if new_balance < 0:
            new_balance = 0
        update_pet_stats(user_id, balance=new_balance)
        return new_balance
    return None


def get_free_action_time(user_id, action):
    """Получить время последнего бесплатного действия"""
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
                       SELECT last_feed, last_sleep, last_play
                       FROM free_actions
                       WHERE user_id = ?
                       ''', (user_id,))
        data = cursor.fetchone()
        conn.close()

        if data:
            last_feed, last_sleep, last_play = data
            if action == 'feed':
                return last_feed
            elif action == 'sleep':
                return last_sleep
            elif action == 'play':
                return last_play
        return None


def show_pet_info(message, user_id):
    """Показать информацию о питомце с динамическими кнопками сна"""
    pet = get_active_pet(user_id)
    if pet is None:
        bot.send_message(message.chat.id, "❌ Нет питомца!")
        return

    # Распаковываем стандартные 8 параметров
    user_id, name, hunger, happiness, energy, is_alive, balance, last_activity = pet

    # Дополнительно берем статус сна из новой функции
    sleep_data = get_pet_sleep_status(user_id)
    is_sleeping = sleep_data[0] if sleep_data else 0

    # === ИСПРАВЛЕННЫЙ БЛОК: Принудительно переводим все статы в ЦЕЛЫЕ числа ===
    hunger_val = int(hunger)
    happiness_val = int(happiness)
    energy_val = int(energy)

    # Теперь генерация полосок никогда не упадет
    hunger_bar = "█" * (hunger_val // 10) + "░" * (10 - hunger_val // 10)
    happiness_bar = "█" * (happiness_val // 10) + "░" * (10 - happiness_val // 10)
    energy_bar = "█" * (energy_val // 10) + "░" * (10 - energy_val // 10)
    # =======================================================================

    markup = InlineKeyboardMarkup()

    if is_sleeping:
        # Если спит — доступно только пробуждение, остальные кнопки скрыты
        markup.add(InlineKeyboardButton("⏰ Проснуться / Встать", callback_data="action_wake_up"))
    else:
        # Если бодрствует — стандартное меню
        markup.add(
            InlineKeyboardButton("🏪 Магазин", callback_data="menu_shop"),
            InlineKeyboardButton("🎒 Инвентарь", callback_data="menu_inventory")
        )

        # Кнопка сна разблокируется строго при энергии меньше 20
        if energy_val < 20:
            sleep_button = InlineKeyboardButton("😴 Уложить спать", callback_data="action_go_to_sleep")
        else:
            sleep_button = InlineKeyboardButton("🔒 Сон (Энергия >= 20)", callback_data="action_sleep_locked")

        markup.add(InlineKeyboardButton("🍖 Покормить", callback_data="action_feed"), sleep_button)
        markup.add(InlineKeyboardButton("🎮 Поиграть", callback_data="action_play"))

    markup.add(InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main"))

    status_label = "💤 КРЕПКО СПИТ" if is_sleeping else "🔋 Бодрствует"

    text = (
        f"🐾 **МОЙ ПИТОМЕЦ**\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Имя: **{name}**\n"
        f"❤️ Статус: **{status_label}**\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📊 **Параметры:**\n"
        f"🍖 Голод: {hunger_val}/100\n"
        f"┃{hunger_bar}\n"
        f"😊 Счастье: {happiness_val}/100\n"
        f"┃{happiness_bar}\n"
        f"⚡ Энергия: {energy_val}/100\n"
        f"┃{energy_bar}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Баланс: **{balance}** монет\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Выбери действие:"
    )

    bot.edit_message_text(text, chat_id=message.chat.id, message_id=message.message_id, parse_mode="Markdown",
                          reply_markup=markup)

def can_use_free_action(user_id, action):
    """Проверить, можно ли использовать бесплатное действие"""
    last_use = get_free_action_time(user_id, action)

    if last_use is None:
        return True, None

    if isinstance(last_use, str):
        last_use = datetime.fromisoformat(last_use)

    now = datetime.now()

    if action == 'feed':
        interval = FREE_FEED_INTERVAL
        action_name = "покормить"
    elif action == 'sleep':
        interval = FREE_SLEEP_INTERVAL
        action_name = "поспать"
    elif action == 'play':
        interval = FREE_PLAY_INTERVAL
        action_name = "поиграть"
    else:
        return False, None

    time_passed = (now - last_use).total_seconds()
    remaining = interval - time_passed

    if remaining <= 0:
        return True, None
    else:
        remaining_minutes = int(remaining // 60)
        remaining_hours = remaining_minutes // 60
        remaining_minutes = remaining_minutes % 60
        if remaining_hours > 0:
            return False, f"⏳ Подожди {remaining_hours}ч {remaining_minutes}мин"
        else:
            return False, f"⏳ Подожди {remaining_minutes} минут"


def update_free_action_time(user_id, action):
    """Обновить время последнего использования"""
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()

        now = datetime.now().isoformat()

        cursor.execute('SELECT user_id FROM free_actions WHERE user_id = ?', (user_id,))
        exists = cursor.fetchone()

        if exists:
            if action == 'feed':
                cursor.execute('UPDATE free_actions SET last_feed = ? WHERE user_id = ?', (now, user_id))
            elif action == 'sleep':
                cursor.execute('UPDATE free_actions SET last_sleep = ? WHERE user_id = ?', (now, user_id))
            elif action == 'play':
                cursor.execute('UPDATE free_actions SET last_play = ? WHERE user_id = ?', (now, user_id))
        else:
            if action == 'feed':
                cursor.execute('''
                               INSERT INTO free_actions (user_id, last_feed, last_sleep, last_play)
                               VALUES (?, ?, '1970-01-01', '1970-01-01')
                               ''', (user_id, now))
            elif action == 'sleep':
                cursor.execute('''
                               INSERT INTO free_actions (user_id, last_feed, last_sleep, last_play)
                               VALUES (?, '1970-01-01', ?, '1970-01-01')
                               ''', (user_id, now))
            elif action == 'play':
                cursor.execute('''
                               INSERT INTO free_actions (user_id, last_feed, last_sleep, last_play)
                               VALUES (?, '1970-01-01', '1970-01-01', ?)
                               ''', (user_id, now))

        conn.commit()
        conn.close()


def decrease_stats_all_pets():
    """Фоновое изменение параметров питомцев (каждые 10 минут)"""
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
                       SELECT user_id, hunger, happiness, energy, name, is_sleeping
                       FROM pets
                       WHERE is_alive = 1
                       ''')
        pets = cursor.fetchall()

        for user_id, hunger, happiness, energy, name, is_sleeping in pets:

            if is_sleeping:
                # === ЛОГИКА ДЛЯ СПЯЩЕГО ПИТОМЦА ===
                # Каждые 10 минут (1/6 часа): Энергия +1.66, Голод -1.0, Счастье -0.5
                new_energy = min(100, energy + (10 / 6))
                new_hunger = max(0, hunger - (6 / 6))
                new_happiness = max(0, happiness - (3 / 6))

                # Проверка: если во сне еда кончилась, питомец погибает
                if new_hunger <= 0:
                    cursor.execute('UPDATE pets SET hunger=0, happiness=0, energy=0, is_alive=0 WHERE user_id=?',
                                   (user_id,))
                    try:
                        bot.send_message(
                            user_id,
                            f"🪦 **Трагические новости...**\n\nТвой питомец **{name}** слишком долго спал и умер во сне от истощения. Начни заново с /start."
                        )
                    except Exception as e:
                        print(f"Не удалось отправить уведомление о смерти {user_id}: {e}")
                else:
                    cursor.execute('''
                                   UPDATE pets
                                   SET hunger=?,
                                       happiness=?,
                                       energy=?
                                   WHERE user_id = ?
                                   ''', (new_hunger, new_happiness, new_energy, user_id))
                continue  # Переходим к следующему питомцу, пропуская блок бодрствования

            # === ЛОГИКА ДЛЯ БОДРСТВУЮЩЕГО ПИТОМЦА ===
            new_hunger = max(0, hunger - 10)
            new_happiness = max(0, happiness - 3)
            new_energy = max(0, energy - 85)

            if new_hunger <= 0 or new_energy <= 0:
                cursor.execute('UPDATE pets SET hunger=0, happiness=0, energy=0, is_alive=0 WHERE user_id=?',
                               (user_id,))
                try:
                    bot.send_message(
                        user_id,
                        f"🪦 **Печальные новости...**\n\nТвой питомец **{name}** погиб от истощения.\nТы можешь завести нового друга с помощью /start.",
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    print(f"Не удалось отправить уведомление о смерти {user_id}: {e}")
            else:
                cursor.execute('UPDATE pets SET hunger=?, happiness=?, energy=? WHERE user_id=?',
                               (new_hunger, new_happiness, new_energy, user_id))

        conn.commit()
        conn.close()


def start_stats_scheduler():
    """Запускает фоновый поток для обновления параметров"""

    def schedule_loop():
        while True:
            schedule.run_pending()
            time.sleep(1)

    schedule.every(10).minutes.do(decrease_stats_all_pets)

    thread = threading.Thread(target=schedule_loop, daemon=True)
    thread.start()
    print("🔄 Система обновления запущена!")

def get_pet_sleep_status(user_id):
    """Получить только статус сна питомца (is_sleeping, went_to_sleep_time)"""
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
                       SELECT is_sleeping, went_to_sleep_time
                       FROM pets
                       WHERE user_id = ? AND is_alive = 1
                       ''', (user_id,))
        data = cursor.fetchone()
        conn.close()
        return data


start_stats_scheduler()


# ============ ГЛАВНОЕ МЕНЮ ============

def show_main_menu(chat_id, user_id):
    """Показать главное меню с кнопками"""
    pet = get_active_pet(user_id)

    if pet is None:
        bot.send_message(chat_id, "❌ У тебя нет питомца! Используй /start")
        return

    user_id, name, hunger, happiness, energy, is_alive, balance, last_activity = pet

    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🏪 Магазин", callback_data="menu_shop"),
        InlineKeyboardButton("🎒 Инвентарь", callback_data="menu_inventory")
    )
    markup.add(
        InlineKeyboardButton("🎁 Бонус", callback_data="menu_bonus"),
        InlineKeyboardButton("🐾 Питомец", callback_data="menu_pet")
    )
    markup.add(
        InlineKeyboardButton("🎮 Игры", callback_data="menu_games"),
        InlineKeyboardButton("📊 Статус", callback_data="menu_status")
    )

    text = (
        f"🏠 **ГЛАВНОЕ МЕНЮ**\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🐾 Питомец: **{name}**\n"
        f"💰 Баланс: **{balance}** монет\n"
        f"🍖 Голод: {hunger}/100\n"
        f"😊 Счастье: {happiness}/100\n"
        f"⚡ Энергия: {energy}/100\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Выбери действие:"
    )

    bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=markup)


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
        show_main_menu(message.chat.id, user_id)


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
    show_main_menu(message.chat.id, user_id)


# ============ НАВИГАЦИЯ ПО МЕНЮ ============

@bot.callback_query_handler(func=lambda call: call.data == "menu_main")
def menu_main(call):
    show_main_menu(call.message.chat.id, call.from_user.id)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "menu_shop")
def menu_shop(call):
    show_shop(call.message, call.from_user.id)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "menu_inventory")
def menu_inventory(call):
    show_inventory(call.message, call.from_user.id)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "menu_bonus")
def menu_bonus(call):
    daily_bonus_menu(call.message, call.from_user.id)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "menu_pet")
def menu_pet(call):
    show_pet_info(call.message, call.from_user.id)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "menu_games")
def menu_games(call):
    show_games_menu(call.message, call.from_user.id)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == "menu_status")
def menu_status(call):
    bot_send_status(call.message, call.from_user.id)
    bot.answer_callback_query(call.id)


# ============ МАГАЗИН С ВЫБОРОМ КОЛИЧЕСТВА ============

# Хранилище для выбора количества
shop_cart = {}

# Цены и метаданные товаров вынесены в единый конфиг для удобства изменения
SHOP_ITEMS = {
    "яблоко": {"price": 10, "emoji": "🍎"},
    "банан": {"price": 12, "emoji": "🍌"},
    "морковка": {"price": 8, "emoji": "🥕"},
    "куриная ножка": {"price": 20, "emoji": "🍗"},
    "рыбка": {"price": 22, "emoji": "🐟"},
    "стейк": {"price": 30, "emoji": "🥩"},
    "пицца": {"price": 25, "emoji": "🍕"},
    "суши": {"price": 35, "emoji": "🍣"},
    "мороженое": {"price": 15, "emoji": "🍦"},
    "печенье": {"price": 10, "emoji": "🍪"},
    "сок": {"price": 12, "emoji": "🧃"},
    "молоко": {"price": 10, "emoji": "🥛"},
    "домашний обед": {"price": 50, "emoji": "🧺"},
    "гурме-набор": {"price": 80, "emoji": "🎁"}
}


def show_shop(message, user_id):
    """Показать магазин. Кнопки ВСЕГДА видны и кликабельны независимо от баланса."""
    pet = get_active_pet(user_id)
    if pet is None:
        bot.send_message(message.chat.id, "❌ У тебя нет питомца!")
        return

    user_id, name, hunger, happiness, energy, is_alive, balance, last_activity = pet

    markup = InlineKeyboardMarkup(row_width=2)

    # Отрисовка кнопок разделена с логикой проверки: генерируем всё и всегда
    for item_name, info in SHOP_ITEMS.items():
        markup.add(InlineKeyboardButton(
            f"{info['emoji']} {item_name.title()} — {info['price']}💰",
            callback_data=f"shop_select:{item_name}"
        ))

    markup.add(InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main"))

    text = (
        f"🛒 **МАГАЗИН**\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Твой баланс: **{balance}** монет\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Выбери товар для покупки (доступны все позиции):"
    )

    # Корректно обрабатываем как новый вызов, так и переход из подменю (через edit)
    try:
        bot.edit_message_text(text, chat_id=message.chat.id, message_id=message.message_id, parse_mode="Markdown",
                              reply_markup=markup)
    except Exception:
        bot.send_message(message.chat.id, text, parse_mode="Markdown", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith('shop_select:'))
def shop_select(call):
    """Выбор товара. Переводит на экран количества."""
    user_id = call.from_user.id
    item_name = call.data.replace('shop_select:', '')

    if item_name not in SHOP_ITEMS:
        bot.answer_callback_query(call.id, "❌ Товар не найден!", show_alert=True)
        return

    pet = get_active_pet(user_id)
    if pet is None:
        bot.answer_callback_query(call.id, "❌ Нет питомца!", show_alert=True)
        return

    # Инициализируем или сбрасываем корзину до 1 шт. при новом заходе
    shop_cart[user_id] = {
        'item': item_name,
        'quantity': 1
    }

    show_quantity_selector(call.message, user_id, item_name)
    bot.answer_callback_query(call.id)


def show_quantity_selector(message, user_id, item_name, error_msg=""):
    """Экран выбора количества. Кнопка 'Купить' всегда активна!"""
    pet = get_active_pet(user_id)
    if pet is None:
        return

    user_id, name, hunger, happiness, energy, is_alive, balance, last_activity = pet

    quantity = shop_cart.get(user_id, {}).get('quantity', 1)
    price = SHOP_ITEMS[item_name]["price"]
    total = price * quantity
    emoji = SHOP_ITEMS[item_name]["emoji"]

    markup = InlineKeyboardMarkup(row_width=3)
    markup.add(
        InlineKeyboardButton("➖", callback_data=f"shop_qty:{item_name}:minus"),
        InlineKeyboardButton(f"{quantity} шт.", callback_data="shop_qty_display"),
        InlineKeyboardButton("➕", callback_data=f"shop_qty:{item_name}:plus")
    )
    # Кнопка покупки всегда на экране и всегда кликабельна!
    markup.add(
        InlineKeyboardButton(f"💳 Купить за {total}💰", callback_data=f"shop_buy:{item_name}:{quantity}"),
        InlineKeyboardButton("↩️ Назад в магазин", callback_data="shop_cancel")
    )
    markup.add(InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main"))

    text = (
        f"{emoji} **{item_name.title()}**\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Цена: **{price}** монет/шт.\n"
        f"💰 Твой баланс: **{balance}** монет\n"
        f"💳 Итого к оплате: **{total}** монет\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
    )

    if error_msg:
        text += f"⚠️ **ОШИБКА:** {error_msg}\n━━━━━━━━━━━━━━━━━━━\n"

    text += "Измени количество или подтверди покупку:"

    bot.edit_message_text(
        text,
        chat_id=message.chat.id,
        message_id=message.message_id,
        parse_mode="Markdown",
        reply_markup=markup
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith('shop_qty:'))
def shop_qty(call):
    """Изменение количества (клики по + и -)"""
    user_id = call.from_user.id
    data = call.data.split(':')
    item_name = data[1]
    action = data[2]

    if user_id not in shop_cart:
        shop_cart[user_id] = {'item': item_name, 'quantity': 1}

    quantity = shop_cart[user_id]['quantity']

    if action == 'plus':
        quantity = min(99, quantity + 1)
    elif action == 'minus':
        quantity = max(1, quantity - 1)

    shop_cart[user_id]['quantity'] = quantity

    # Перерисовываем интерфейс с новым количеством без проверок баланса
    show_quantity_selector(call.message, user_id, item_name)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == 'shop_qty_display')
def shop_qty_display(call):
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == 'shop_cancel')
def shop_cancel(call):
    """Возврат в основное меню магазина с очисткой временной корзины"""
    user_id = call.from_user.id
    if user_id in shop_cart:
        del shop_cart[user_id]

    show_shop(call.message, user_id)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith('shop_buy:'))
def shop_buy(call):
    """Логика транзакции покупки. Проверка баланса происходит строго здесь!"""
    user_id = call.from_user.id
    data = call.data.split(':')
    item_name = data[1]
    quantity = int(data[2])

    if item_name not in SHOP_ITEMS:
        bot.answer_callback_query(call.id, "❌ Товар не найден!", show_alert=True)
        return

    total_price = SHOP_ITEMS[item_name]["price"] * quantity

    pet = get_active_pet(user_id)
    if pet is None:
        bot.answer_callback_query(call.id, "❌ Нет питомца!", show_alert=True)
        return

    user_id, name, hunger, happiness, energy, is_alive, balance, last_activity = pet

    # ТРЕБОВАНИЕ 3: Если монет НЕ хватает -> НЕ списываем, НЕ выдаем, показываем уведомление
    if balance < total_price:
        # 1. Показываем всплывающее Telegram-уведомление (alert)
        bot.answer_callback_query(
            call.id,
            f"Не хватает монет! Нужно: {total_price}💰, у тебя: {balance}💰",
            show_alert=True
        )
        # 2. Логируем в консоль сервера
        print(
            f"⚠️ Игрок {user_id} пытался купить {item_name} x{quantity}, но не хватило средств ({balance}/{total_price})")
        # 3. Перерисовываем интерфейс, добавляя текстовое предупреждение прямо на экран, сохраняя кнопки активными!
        show_quantity_selector(call.message, user_id, item_name, error_msg="Не хватает монет!")
        return

    # Если баланса достаточно — проводим транзакцию (Блокировка БД для потокобезопасности)
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Списываем монеты
        new_balance = balance - total_price
        cursor.execute("UPDATE pets SET balance = ? WHERE user_id = ? AND is_alive = 1", (new_balance, user_id))

        # Намертво сохраняем состояние инвентаря в БД (выполняет требование сохранения стейта)
        cursor.execute('''
                       INSERT INTO inventory (user_id, item_name, quantity, updated_at)
                       VALUES (?, ?, ?, CURRENT_TIMESTAMP) ON CONFLICT(user_id, item_name) 
                       DO
                       UPDATE SET quantity = quantity + ?, updated_at = CURRENT_TIMESTAMP
                       ''', (user_id, item_name, quantity, quantity))

        conn.commit()
        conn.close()

    # Сбрасываем стейт сессии для этого пользователя
    if user_id in shop_cart:
        del shop_cart[user_id]

    emoji = SHOP_ITEMS[item_name]["emoji"]

    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("🔄 Купить еще", callback_data="menu_shop"),
        InlineKeyboardButton("🎒 В инвентарь", callback_data="menu_inventory"),
        InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")
    )

    text = (
        f"✅ **Покупка успешна!**\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"{emoji} Куплено: **{item_name.title()}** ×{quantity}\n"
        f"💰 Списано: **{total_price}** монет\n"
        f"💰 Остаток на балансе: **{new_balance}** монет\n"
        f"📦 Товар надежно сохранен в инвентаре.\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Куда отправимся?"
    )

    bot.edit_message_text(
        text,
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="Markdown",
        reply_markup=markup
    )
    bot.answer_callback_query(call.id, f"✅ {item_name.title()} ×{quantity} куплено!")


# ============ ИНВЕНТАРЬ ============

INVENTORY_EMOJIS = {
    "яблоко": "🍎", "банан": "🍌", "морковка": "🥕", "куриная ножка": "🍗",
    "рыбка": "🐟", "стейк": "🥩", "пицца": "🍕", "суши": "🍣",
    "мороженое": "🍦", "печенье": "🍪", "сок": "🧃", "молоко": "🥛",
    "домашний обед": "🧺", "гурме-набор": "🎁"
}

# Хранилище для выбора количества использования
use_cart = {}


def show_inventory(message, user_id, page=1, items_per_page=5):
    """Показать инвентарь с пагинацией"""
    pet = get_active_pet(user_id)
    if pet is None:
        bot.send_message(message.chat.id, "❌ Нет питомца!")
        return

    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT item_name, quantity FROM inventory WHERE user_id = ? AND quantity > 0 ORDER BY item_name",
            (user_id,))
        all_items = cursor.fetchall()
        conn.close()

    if not all_items:
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton("🏪 Магазин", callback_data="menu_shop"),
            InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")
        )

        bot.edit_message_text(
            "🎒 **ИНВЕНТАРЬ**\n━━━━━━━━━━━━━━━━━━━\n🪹 Твой инвентарь пуст!\nКупи что-нибудь в магазине.",
            chat_id=message.chat.id,
            message_id=message.message_id,
            parse_mode="Markdown",
            reply_markup=markup
        )
        return

    total_items = len(all_items)
    total_pages = (total_items + items_per_page - 1) // items_per_page

    if page < 1:
        page = 1
    if page > total_pages:
        page = total_pages

    start_idx = (page - 1) * items_per_page
    end_idx = min(start_idx + items_per_page, total_items)
    items = all_items[start_idx:end_idx]

    text = f"🎒 **ИНВЕНТАРЬ**\n━━━━━━━━━━━━━━━━━━━\n"
    text += f"📦 Всего: {total_items} предметов\n━━━━━━━━━━━━━━━━━━━\n\n"

    markup = InlineKeyboardMarkup(row_width=2)

    for item_name, quantity in items:
        emoji = INVENTORY_EMOJIS.get(item_name, "📦")
        text += f"{emoji} **{item_name.title()}** ×{quantity}\n"

        # Кнопки для каждого предмета
        markup.add(
            InlineKeyboardButton(f"🍖 Использовать", callback_data=f"inv_use_select:{item_name}"),
            InlineKeyboardButton(f"🗑️ Выбросить", callback_data=f"inv_drop:{item_name}")
        )

    text += "\n━━━━━━━━━━━━━━━━━━━\n"

    # Пагинация
    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton("◀️ Назад", callback_data=f"inv_page:{page - 1}"))
    nav_buttons.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="inv_page_display"))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton("Вперед ▶️", callback_data=f"inv_page:{page + 1}"))

    if nav_buttons:
        markup.row(*nav_buttons)

    markup.add(InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main"))

    bot.edit_message_text(
        text,
        chat_id=message.chat.id,
        message_id=message.message_id,
        parse_mode="Markdown",
        reply_markup=markup
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith('inv_page:'))
def inv_page(call):
    """Переключение страницы инвентаря"""
    user_id = call.from_user.id
    page = int(call.data.split(':')[1])
    show_inventory(call.message, user_id, page)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == 'inv_page_display')
def inv_page_display(call):
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith('inv_use_select:'))
def inv_use_select(call):
    """Выбор предмета для использования"""
    user_id = call.from_user.id
    item_name = call.data.replace('inv_use_select:', '')

    pet = get_active_pet(user_id)
    if pet is None:
        bot.answer_callback_query(call.id, "❌ Нет питомца!", show_alert=True)
        return

    # Проверяем наличие в инвентаре
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT quantity FROM inventory WHERE user_id = ? AND item_name = ?", (user_id, item_name))
        res = cursor.fetchone()
        conn.close()

    if not res or res[0] <= 0:
        bot.answer_callback_query(call.id, "❌ Нет этого предмета!", show_alert=True)
        return

    quantity = res[0]

    # Сохраняем в корзину использования
    use_cart[user_id] = {
        'item': item_name,
        'quantity': 1,
        'max_quantity': quantity
    }

    show_use_quantity_selector(call.message, user_id, item_name)


def show_use_quantity_selector(message, user_id, item_name):
    """Показать выбор количества для использования"""
    pet = get_active_pet(user_id)
    if pet is None:
        return

    if user_id not in use_cart:
        return

    cart = use_cart[user_id]
    quantity = cart['quantity']
    max_qty = cart['max_quantity']

    # Эффекты предметов
    item_effects = {
        "яблоко": {"hunger": 15, "happiness": 0},
        "банан": {"hunger": 18, "happiness": 0},
        "морковка": {"hunger": 12, "happiness": 0},
        "куриная ножка": {"hunger": 25, "happiness": 0},
        "рыбка": {"hunger": 28, "happiness": 0},
        "стейк": {"hunger": 35, "happiness": 5},
        "пицца": {"hunger": 30, "happiness": 5},
        "суши": {"hunger": 30, "happiness": 15},
        "мороженое": {"hunger": 12, "happiness": 15},
        "печенье": {"hunger": 8, "happiness": 10},
        "сок": {"hunger": 15, "happiness": 8},
        "молоко": {"hunger": 15, "happiness": 5},
        "домашний обед": {"hunger": 50, "happiness": 15},
        "гурме-набор": {"hunger": 70, "happiness": 30}
    }

    effect = item_effects.get(item_name, {"hunger": 10, "happiness": 0})
    emoji = INVENTORY_EMOJIS.get(item_name, "📦")

    markup = InlineKeyboardMarkup(row_width=3)
    markup.add(
        InlineKeyboardButton("➖", callback_data=f"use_qty:{item_name}:minus"),
        InlineKeyboardButton(f"{quantity}/{max_qty}", callback_data="use_qty_display"),
        InlineKeyboardButton("➕", callback_data=f"use_qty:{item_name}:plus")
    )
    markup.add(
        InlineKeyboardButton(f"✅ Использовать", callback_data=f"use_confirm:{item_name}:{quantity}"),
        InlineKeyboardButton("❌ Отмена", callback_data="use_cancel")
    )
    markup.add(InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main"))

    text = (
        f"{emoji} **Использовать {item_name.title()}**\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📦 В наличии: **{max_qty}** шт.\n"
        f"🍖 Эффект: +{effect['hunger']} голода, +{effect['happiness']} счастья\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Количество: **{quantity}** шт.\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Сколько использовать?"
    )

    bot.edit_message_text(
        text,
        chat_id=message.chat.id,
        message_id=message.message_id,
        parse_mode="Markdown",
        reply_markup=markup
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith('use_qty:'))
def use_qty(call):
    """Изменение количества для использования"""
    user_id = call.from_user.id
    data = call.data.split(':')
    item_name = data[1]
    action = data[2]

    if user_id not in use_cart:
        return

    cart = use_cart[user_id]

    if action == 'plus':
        cart['quantity'] = min(cart['max_quantity'], cart['quantity'] + 1)
    elif action == 'minus':
        cart['quantity'] = max(1, cart['quantity'] - 1)

    show_use_quantity_selector(call.message, user_id, item_name)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == 'use_qty_display')
def use_qty_display(call):
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == 'use_cancel')
def use_cancel(call):
    """Отмена использования"""
    user_id = call.from_user.id
    if user_id in use_cart:
        del use_cart[user_id]
    show_inventory(call.message, user_id)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith('use_confirm:'))
def use_confirm(call):
    """Подтверждение использования предмета"""
    user_id = call.from_user.id
    data = call.data.split(':')
    item_name = data[1]
    quantity = int(data[2])

    item_effects = {
        "яблоко": {"hunger": 15, "happiness": 0},
        "банан": {"hunger": 18, "happiness": 0},
        "морковка": {"hunger": 12, "happiness": 0},
        "куриная ножка": {"hunger": 25, "happiness": 0},
        "рыбка": {"hunger": 28, "happiness": 0},
        "стейк": {"hunger": 35, "happiness": 5},
        "пицца": {"hunger": 30, "happiness": 5},
        "суши": {"hunger": 30, "happiness": 15},
        "мороженое": {"hunger": 12, "happiness": 15},
        "печенье": {"hunger": 8, "happiness": 10},
        "сок": {"hunger": 15, "happiness": 8},
        "молоко": {"hunger": 15, "happiness": 5},
        "домашний обед": {"hunger": 50, "happiness": 15},
        "гурме-набор": {"hunger": 70, "happiness": 30}
    }

    if item_name not in item_effects:
        bot.answer_callback_query(call.id, "❌ Неизвестный предмет!", show_alert=True)
        return

    effect = item_effects[item_name]

    pet = get_active_pet(user_id)
    if pet is None:
        bot.answer_callback_query(call.id, "❌ Нет питомца!", show_alert=True)
        return

    user_id, name, hunger, happiness, energy, is_alive, balance, last_activity = pet

    # Проверяем наличие в инвентаре
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT quantity FROM inventory WHERE user_id = ? AND item_name = ?", (user_id, item_name))
        res = cursor.fetchone()

        if not res or res[0] < quantity:
            bot.answer_callback_query(call.id, "❌ Недостаточно предметов!", show_alert=True)
            conn.close()
            return

        current_qty = res[0]
        new_qty = current_qty - quantity

        # Обновляем инвентарь
        if new_qty > 0:
            cursor.execute(
                "UPDATE inventory SET quantity = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND item_name = ?",
                (new_qty, user_id, item_name)
            )
        else:
            cursor.execute("DELETE FROM inventory WHERE user_id = ? AND item_name = ?", (user_id, item_name))

        # Применяем эффекты (умножаем на количество)
        total_hunger = effect["hunger"] * quantity
        total_happiness = effect["happiness"] * quantity

        new_hunger = min(100, hunger + total_hunger)
        new_happiness = min(100, happiness + total_happiness)

        cursor.execute('''
                       UPDATE pets
                       SET hunger        = ?,
                           happiness     = ?,
                           last_activity = CURRENT_TIMESTAMP
                       WHERE user_id = ?
                         AND is_alive = 1
                       ''', (new_hunger, new_happiness, user_id))

        conn.commit()
        conn.close()

    # Удаляем корзину использования
    if user_id in use_cart:
        del use_cart[user_id]

    emoji = INVENTORY_EMOJIS.get(item_name, "📦")

    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("🎒 Инвентарь", callback_data="menu_inventory"),
        InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")
    )

    text = (
        f"{emoji} **{item_name.title()} использован!**\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🐾 {name} съел {quantity} шт. {item_name}!\n"
        f"🍖 Голод: {hunger} → {new_hunger}/100\n"
        f"😊 Счастье: {happiness} → {new_happiness}/100\n"
        f"📦 Осталось: {new_qty} шт.\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Что дальше?"
    )

    bot.edit_message_text(
        text,
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="Markdown",
        reply_markup=markup
    )
    bot.answer_callback_query(call.id, f"✅ {item_name.title()} использован!")


@bot.callback_query_handler(func=lambda call: call.data.startswith('inv_drop:'))
def inv_drop(call):
    """Выбрасывание предмета (с подтверждением)"""
    user_id = call.from_user.id
    item_name = call.data.replace('inv_drop:', '')

    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("✅ Да, выбросить", callback_data=f"drop_confirm:{item_name}"),
        InlineKeyboardButton("❌ Отмена", callback_data="menu_inventory")
    )

    text = (
        f"🗑️ **ВЫБРОСИТЬ {item_name.upper()}?**\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ Это действие НЕЛЬЗЯ отменить!\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Ты уверен?"
    )

    bot.edit_message_text(
        text,
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="Markdown",
        reply_markup=markup
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith('drop_confirm:'))
def drop_confirm(call):
    """Подтверждение выбрасывания"""
    user_id = call.from_user.id
    item_name = call.data.replace('drop_confirm:', '')

    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT quantity FROM inventory WHERE user_id = ? AND item_name = ?", (user_id, item_name))
        res = cursor.fetchone()

        if not res or res[0] <= 0:
            bot.answer_callback_query(call.id, "❌ Нет этого предмета!", show_alert=True)
            conn.close()
            return

        current_qty = res[0]
        new_qty = current_qty - 1

        if new_qty > 0:
            cursor.execute(
                "UPDATE inventory SET quantity = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND item_name = ?",
                (new_qty, user_id, item_name)
            )
        else:
            cursor.execute("DELETE FROM inventory WHERE user_id = ? AND item_name = ?", (user_id, item_name))

        conn.commit()
        conn.close()

    emoji = INVENTORY_EMOJIS.get(item_name, "📦")

    text = f"🗑️ Вы выбросили {emoji} {item_name.title()}. Осталось: {new_qty} шт."

    bot.answer_callback_query(call.id, f"🗑️ {item_name.title()} выброшен!")

    # Показываем обновленный инвентарь
    show_inventory(call.message, user_id)


# ============ ИНФОРМАЦИЯ О ПИТОМЦЕ ============
def show_pet_info(message, user_id):
    """Показать информацию о питомце с динамическими кнопками сна"""
    pet = get_active_pet(user_id)
    if pet is None:
        bot.send_message(message.chat.id, "❌ Нет питомца!")
        return

    # Распаковываем стандартные 8 параметров
    user_id, name, hunger, happiness, energy, is_alive, balance, last_activity = pet

    # Дополнительно берем статус сна из новой функции
    sleep_data = get_pet_sleep_status(user_id)
    is_sleeping = sleep_data[0] if sleep_data else 0

    # Приводим к int для красивого вывода статус-баров
    hunger_display = int(hunger)

    hunger_bar = "█" * (hunger_display // 10) + "░" * (10 - hunger_display // 10)
    happiness_bar = "█" * (happiness // 10) + "░" * (10 - happiness // 10)
    energy_bar = "█" * (int(energy) // 10) + "░" * (10 - int(energy) // 10)

    markup = InlineKeyboardMarkup()

    if is_sleeping:
        # Если спит — доступно только пробуждение, остальные кнопки скрыты
        markup.add(InlineKeyboardButton("⏰ Проснуться / Встать", callback_data="action_wake_up"))
    else:
        # Если бодрствует — стандартное меню
        markup.add(
            InlineKeyboardButton("🏪 Магазин", callback_data="menu_shop"),
            InlineKeyboardButton("🎒 Инвентарь", callback_data="menu_inventory")
        )

        # Кнопка сна разблокируется строго при энергии меньше 20
        if energy < 20:
            sleep_button = InlineKeyboardButton("😴 Уложить спать", callback_data="action_go_to_sleep")
        else:
            sleep_button = InlineKeyboardButton("🔒 Сон (Энергия >= 20)", callback_data="action_sleep_locked")

        markup.add(InlineKeyboardButton("🍖 Покормить", callback_data="action_feed"), sleep_button)
        markup.add(InlineKeyboardButton("🎮 Поиграть", callback_data="action_play"))

    markup.add(InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main"))

    status_label = "💤 КРЕПКО СПИТ" if is_sleeping else "🔋 Бодрствует"

    text = (
        f"🐾 **МОЙ ПИТОМЕЦ**\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Имя: **{name}**\n"
        f"❤️ Статус: **{status_label}**\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📊 **Параметры:**\n"
        f"🍖 Голод: {hunger_display}/100\n"
        f"┃{hunger_bar}\n"
        f"😊 Счастье: {happiness}/100\n"
        f"┃{happiness_bar}\n"
        f"⚡ Энергия: {int(energy)}/100\n"
        f"┃{energy_bar}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Баланс: **{balance}** монет\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Выбери действие:"
    )

    bot.edit_message_text(text, chat_id=message.chat.id, message_id=message.message_id, parse_mode="Markdown",
                          reply_markup=markup)


# ============ БЕСПЛАТНЫЕ ДЕЙСТВИЯ (ЧЕРЕЗ КНОПКИ) ============

@bot.callback_query_handler(func=lambda call: call.data == 'action_feed')
def action_feed(call):
    """Бесплатное кормление через кнопку"""
    user_id = call.from_user.id
    pet = get_active_pet(user_id)

    if pet is None:
        bot.answer_callback_query(call.id, "❌ Нет питомца!", show_alert=True)
        return

    user_id, name, hunger, happiness, energy, is_alive, balance, last_activity = pet

    if hunger >= 100:
        bot.answer_callback_query(call.id, f"🍔 {name} уже сыт!", show_alert=True)
        return

    can_use, msg = can_use_free_action(user_id, 'feed')
    if not can_use:
        bot.answer_callback_query(call.id, msg, show_alert=True)
        return

    new_hunger = min(100, hunger + 20)
    new_happiness = min(100, happiness + 3)

    update_pet_stats(user_id, hunger=new_hunger, happiness=new_happiness)
    update_free_action_time(user_id, 'feed')

    bot.answer_callback_query(call.id, f"🍖 {name} покормлен! +20 голода")

    # Показываем обновленную информацию о питомце
    show_pet_info(call.message, user_id)


@bot.callback_query_handler(func=lambda call: call.data == 'action_sleep_locked')
def action_sleep_locked(call):
    """Обработка клика по закрытой кнопке сна"""
    bot.answer_callback_query(call.id, "⚠️ Питомец полон сил! Уложить спать можно только при энергии меньше 20%.", show_alert=True)


@bot.callback_query_handler(func=lambda call: call.data == 'action_go_to_sleep')
def action_go_to_sleep(call):
    """Перевод питомца в режим сна"""
    user_id = call.from_user.id
    pet = get_active_pet(user_id)

    if pet is None:
        bot.answer_callback_query(call.id, "❌ Нет питомца!", show_alert=True)
        return

    now = datetime.now().isoformat()
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE pets SET is_sleeping = 1, went_to_sleep_time = ? WHERE user_id = ?', (now, user_id))
        conn.commit()
        conn.close()

    bot.answer_callback_query(call.id, "💤 Твой питомец сладко уснул и начал восстанавливать энергию!", show_alert=True)
    show_pet_info(call.message, user_id)


@bot.callback_query_handler(func=lambda call: call.data == 'action_wake_up')
def action_wake_up(call):
    """Пробуждение питомца"""
    user_id = call.from_user.id
    pet = get_active_pet(user_id)

    if pet is None:
        bot.answer_callback_query(call.id, "❌ Нет питомца!", show_alert=True)
        return

    # Безопасно распаковываем стандартные 8 параметров
    user_id, name, hunger, happiness, energy, is_alive, balance, last_activity = pet

    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
                       UPDATE pets 
                       SET is_sleeping = 0, 
                           went_to_sleep_time = NULL 
                       WHERE user_id = ?
                       ''', (user_id,))
        conn.commit()
        conn.close()

    bot.answer_callback_query(
        call.id,
        f"⏰ {name} успешно проснулся!\n🔋 Энергия: {int(energy)}%\n🍖 Голод: {int(hunger)}%",
        show_alert=True
    )
    show_pet_info(call.message, user_id)


@bot.callback_query_handler(func=lambda call: call.data == 'action_play')
def action_play(call):
    """Бесплатная игра через кнопку"""
    user_id = call.from_user.id
    pet = get_active_pet(user_id)

    if pet is None:
        bot.answer_callback_query(call.id, "❌ Нет питомца!", show_alert=True)
        return

    user_id, name, hunger, happiness, energy, is_alive, balance, last_activity = pet

    if happiness >= 100:
        bot.answer_callback_query(call.id, f"😊 {name} уже счастлив!", show_alert=True)
        return

    if energy < 20:
        bot.answer_callback_query(call.id, f"😴 {name} устал! Отправь спать", show_alert=True)
        return

    can_use, msg = can_use_free_action(user_id, 'play')
    if not can_use:
        bot.answer_callback_query(call.id, msg, show_alert=True)
        return

    new_happiness = min(100, happiness + 20)
    new_energy = max(0, energy - 10)

    update_pet_stats(user_id, happiness=new_happiness, energy=new_energy)
    update_free_action_time(user_id, 'play')

    bot.answer_callback_query(call.id, f"🎮 Игра с {name}! +20 счастья")

    show_pet_info(call.message, user_id)


# ============ СТАТУС ============

def bot_send_status(message, user_id):
    """Отправить статус питомца"""
    pet = get_active_pet(user_id)

    if pet is None:
        bot.send_message(message.chat.id, "❌ У тебя нет активного питомца! Используй /start, чтобы завести.")
        return

    user_id, name, hunger, happiness, energy, is_alive, balance, last_activity = pet

    hunger_status = ""
    if hunger >= 80:
        hunger_status = "🍔 Сыт"
    elif hunger >= 50:
        hunger_status = "😐 Нормально"
    elif hunger >= 20:
        hunger_status = "😟 Хочет есть"
    else:
        hunger_status = "⚠️ Голодный! Срочно покорми!"

    happiness_status = ""
    if happiness >= 80:
        happiness_status = "😄 Счастлив"
    elif happiness >= 50:
        happiness_status = "🙂 Нормально"
    elif happiness >= 20:
        happiness_status = "😐 Немного грустный"
    else:
        happiness_status = "😢 Грустный! Поиграй с ним!"

    energy_status = ""
    if energy >= 80:
        energy_status = "⚡ Полон сил"
    elif energy >= 50:
        energy_status = "🔋 Нормально"
    elif energy >= 20:
        energy_status = "😴 Устал"
    else:
        energy_status = "🥱 Очень устал! Отправь спать!"

    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("🐾 Питомец", callback_data="menu_pet"),
        InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")
    )

    status_text = f"""
🐾 **Статус питомца**

Имя: {name}
❤️ Статус: Жив и здоров!

📊 Параметры:
🍖 Голод: {hunger}/100 ({hunger_status})
😊 Счастье: {happiness}/100 ({happiness_status})
⚡ Энергия: {energy}/100 ({energy_status})
💰 Баланс: {balance} монет

⚠️ Голод -5, Счастье -3, Энергия -2 каждые 10 минут!
    """

    bot.edit_message_text(
        status_text,
        chat_id=message.chat.id,
        message_id=message.message_id,
        parse_mode="Markdown",
        reply_markup=markup
    )


# ============ ИГРЫ ============

def show_games_menu(message, user_id):
    """Показать меню игр"""
    pet = get_active_pet(user_id)
    if pet is None:
        bot.send_message(message.chat.id, "❌ Нет питомца!")
        return

    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🎯 Угадай число", callback_data="game_guess"),
        InlineKeyboardButton("✊✌️✋ КНБ", callback_data="game_rps")
    )
    markup.add(
        InlineKeyboardButton("📊 Статистика", callback_data="game_stats"),
        InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")
    )

    text = (
        f"🎮 **ИГРЫ**\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Выбери игру:\n\n"
        f"🎯 **Угадай число**\n"
        f"💰 Награда: 20 монет\n"
        f"📉 Штраф: -5 монет\n\n"
        f"✊✌️✋ **Камень-Ножницы-Бумага**\n"
        f"💰 Награда: 15 монет\n"
        f"📉 Штраф: -5 монет\n"
        f"━━━━━━━━━━━━━━━━━━━"
    )

    bot.edit_message_text(
        text,
        chat_id=message.chat.id,
        message_id=message.message_id,
        parse_mode="Markdown",
        reply_markup=markup
    )

def apply_game_stats_cost(user_id):
    """Списывает 5% энергии и добавляет 15% счастья при начале игры"""
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Получаем текущие показатели
        cursor.execute('SELECT happiness, energy FROM pets WHERE user_id = ? AND is_alive = 1', (user_id,))
        res = cursor.fetchone()
        if res:
            current_happiness, current_energy = res

            # Считаем новые значения (явно приводим к int для безопасности)
            new_happiness = min(100, int(current_happiness) + 15)
            new_energy = max(0, int(current_energy) - 5)

            cursor.execute('''
                           UPDATE pets
                           SET happiness = ?,
                               energy    = ?
                           WHERE user_id = ?
                             AND is_alive = 1
                           ''', (new_happiness, new_energy, user_id))
            conn.commit()
        conn.close()


@bot.callback_query_handler(func=lambda call: call.data == "game_guess")
def start_guess_game_callback(call):
    """Запуск игры Угадай число при нажатии на кнопку в меню игр"""
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    pet = get_active_pet(user_id)

    # 1. Проверяем, есть ли питомец
    if pet is None:
        bot.answer_callback_query(call.id, "❌ У тебя нет питомца!", show_alert=True)
        return

    _, name, _, _, energy, _, _, _ = pet

    # 2. Проверяем энергию для старта (нужно минимум 15%)
    if int(energy) < 15:
        bot.answer_callback_query(call.id, f"⚠️ {name} слишком устал для игр ({int(energy)}%)!", show_alert=True)
        return

    # 3. Проверяем, не запущена ли уже игра у этого пользователя
    if user_id in active_games:
        bot.answer_callback_query(call.id, "⚠️ Ты уже в игре!", show_alert=True)
        return

    # === СНИМАЕМ ЭНЕРГИЮ В БАЗЕ ДАННЫХ ===
    apply_game_stats_cost(user_id)

    # Сразу запрашиваем обновленные статы, чтобы показать игроку актуальную цифру
    pet = get_active_pet(user_id)
    _, _, _, _, updated_energy, _, _, _ = pet

    # Генерируем секретное число от 1 до 10
    secret = random.randint(1, 10)
    active_games[user_id] = secret

    # Строим клавиатуру с цифрами 1-10
    markup = InlineKeyboardMarkup(row_width=5)
    buttons = []
    for i in range(1, 11):
        buttons.append(InlineKeyboardButton(str(i), callback_data=f"guess_{i}"))
    markup.add(*buttons)
    markup.add(InlineKeyboardButton("🏳️ Сдаться", callback_data="guess_giveup"))

    # Редактируем сообщение меню, превращая его в игровое поле
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text=f"🎯 **Угадай число!**\n\n"
             f"Я загадал число от 1 до 10.\n"
             f"🔋 Питомец потратил 5% энергии за вход.\n"
             f"🔋 Текущая энергия: **{int(updated_energy)}%**\n"
             f"💰 Награда: **20 монет**\n"
             f"📉 Штраф: **-5 монет** за неверный клик\n\n"
             f"Выбери число:",
        parse_mode="Markdown",
        reply_markup=markup
    )
    bot.answer_callback_query(call.id, "Игра началась!")


@bot.callback_query_handler(func=lambda call: call.data == 'game_rps')
def game_rps(call):
    """Запуск игры КНБ"""
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    pet = get_active_pet(user_id)

    if pet is None:
        bot.answer_callback_query(call.id, "❌ Нет питомца!", show_alert=True)
        return

    # Распаковываем параметры питомца
    _, name, _, _, energy, _, balance, _ = pet

    # 1. ПРОВЕРКА ЭНЕРГИИ: Хватает ли для старта (нужно минимум 15%)
    if int(energy) < 15:
        bot.answer_callback_query(call.id, f"⚠️ {name} слишком устал для игр ({int(energy)}%)!", show_alert=True)
        return

    # 2. ПРОВЕРКА МОНЕТ: Достаточно ли денег на случай проигрыша
    if balance < 5:
        bot.answer_callback_query(call.id, "❌ Нужно минимум 5 монет!", show_alert=True)
        return

    # === СНИМАЕМ ЭНЕРГИЮ В БАЗЕ ДАННЫХ ===
    apply_game_stats_cost(user_id)

    # Сразу запрашиваем обновленные статы, чтобы показать игроку актуальную цифру энергии
    pet = get_active_pet(user_id)
    _, _, _, _, updated_energy, _, _, _ = pet

    active_rps_games[user_id] = True

    markup = InlineKeyboardMarkup(row_width=3)
    markup.add(
        InlineKeyboardButton("🪨 Камень", callback_data="rps_rock"),
        InlineKeyboardButton("✂️ Ножницы", callback_data="rps_scissors"),
        InlineKeyboardButton("📄 Бумага", callback_data="rps_paper")
    )
    markup.add(
        InlineKeyboardButton("🏳️ Сдаться", callback_data="rps_giveup"),
        InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")
    )

    bot.edit_message_text(
        text=f"✊✌️✋ **Камень, Ножницы, Бумага!**\n\n"
             f"Сыграй против ИИ бота!\n"
             f"🔋 Питомец потратил 5% энергии за вход.\n"
             f"🔋 Текущая энергия: **{int(updated_energy)}%**\n"
             f"💰 Награда за победу: **15 монет**\n"
             f"📉 Проигрыш: **-5 монет**\n"
             f"🤝 Ничья: без изменений\n\n"
             f"Сделай свой ход:",
        chat_id=chat_id,
        message_id=call.message.message_id,
        parse_mode="Markdown",
        reply_markup=markup
    )
    bot.answer_callback_query(call.id, "Игра началась!")


@bot.callback_query_handler(func=lambda call: call.data == 'game_stats')
def game_stats(call):
    """Показать статистику игр"""
    user_id = call.from_user.id
    pet = get_active_pet(user_id)

    if pet is None:
        bot.answer_callback_query(call.id, "❌ Нет питомца!", show_alert=True)
        return

    user_id, name, hunger, happiness, energy, is_alive, balance, last_activity = pet

    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("🎯 Играть", callback_data="game_guess"),
        InlineKeyboardButton("✊✌️✋ Играть", callback_data="game_rps")
    )
    markup.add(InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main"))

    bot.edit_message_text(
        f"📊 **Статистика игр**\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🐾 Питомец: {name}\n"
        f"💰 Баланс: {balance} монет\n"
        f"🍖 Голод: {hunger}/100\n"
        f"😊 Счастье: {happiness}/100\n"
        f"⚡ Энергия: {energy}/100\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Выбери игру:",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="Markdown",
        reply_markup=markup
    )
    bot.answer_callback_query(call.id)


# ============ БОНУСЫ ============

def daily_bonus_menu(message, user_id):
    """Показать меню ежедневного бонуса"""
    pet = get_active_pet(user_id)
    if pet is None:
        bot.send_message(message.chat.id, "❌ Нет питомца!")
        return

    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT bonus_streak, last_bonus_time, balance FROM pets WHERE user_id = ? AND is_alive = 1",
                       (user_id,))
        res = cursor.fetchone()
        conn.close()

    if not res:
        return

    streak, last_bonus_str, balance = res
    now = datetime.now()

    can_claim = False
    time_left_str = ""

    if last_bonus_str is None:
        can_claim = True
    else:
        last_bonus_time = datetime.fromisoformat(last_bonus_str)
        time_passed = now - last_bonus_time

        if time_passed >= timedelta(hours=24):
            can_claim = True
            if time_passed >= timedelta(hours=48):
                streak = 0
        else:
            can_claim = False
            time_left = timedelta(hours=24) - time_passed
            hours, remainder = divmod(int(time_left.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            time_left_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    display_streak = streak if not can_claim else (streak + 1)
    days_in_current_ten = display_streak % 10 if display_streak % 10 != 0 else 10
    reward = 50 if display_streak % 10 == 0 else 10

    progress_bar = "🟩" * days_in_current_ten + "⬜" * (10 - days_in_current_ten)

    text = (
        f"🎁 **ЕЖЕДНЕВНЫЙ БОНУС**\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🔥 Стрик: **{display_streak} дн.**\n"
        f"📊 До супер-награды (50💰): **{10 - days_in_current_ten} дн.**\n"
        f"🗺️ Прогресс:\n"
        f"|{progress_bar}| {days_in_current_ten}/10\n\n"
        f"💰 Награда: **{reward} монет**\n"
    )

    markup = InlineKeyboardMarkup()
    if can_claim:
        markup.add(InlineKeyboardButton("🎁 Забрать бонус!", callback_data="claim_daily_bonus"))
    else:
        text += f"\n⏳ Через: `{time_left_str}`"
        markup.add(InlineKeyboardButton("🔒 Уже получено", callback_data="bonus_disabled"))

    markup.add(InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main"))

    bot.edit_message_text(
        text,
        chat_id=message.chat.id,
        message_id=message.message_id,
        parse_mode="Markdown",
        reply_markup=markup
    )


@bot.callback_query_handler(func=lambda call: call.data == 'claim_daily_bonus')
def handle_claim_bonus(call):
    """Забрать ежедневный бонус"""
    user_id = call.from_user.id
    now = datetime.now()

    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT bonus_streak, last_bonus_time, balance FROM pets WHERE user_id = ? AND is_alive = 1",
                       (user_id,))
        res = cursor.fetchone()

        if not res:
            bot.answer_callback_query(call.id, "❌ Питомец не найден!", show_alert=True)
            conn.close()
            return

        streak, last_bonus_str, balance = res

        if last_bonus_str is not None:
            last_bonus_time = datetime.fromisoformat(last_bonus_str)
            if now - last_bonus_time < timedelta(hours=24):
                bot.answer_callback_query(call.id, "⏳ Бонус еще не остыл!", show_alert=True)
                conn.close()
                return

            if now - last_bonus_time >= timedelta(hours=48):
                streak = 0

        new_streak = streak + 1
        reward = 50 if new_streak % 10 == 0 else 10
        new_balance = balance + reward

        cursor.execute('''
                       UPDATE pets
                       SET bonus_streak    = ?,
                           last_bonus_time = ?,
                           balance         = ?
                       WHERE user_id = ?
                         AND is_alive = 1
                       ''', (new_streak, now.isoformat(), new_balance, user_id))
        conn.commit()
        conn.close()

    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("🎁 Бонус", callback_data="menu_bonus"),
        InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")
    )

    bot.edit_message_text(
        f"✨ **БОНУС ПОЛУЧЕН!** ✨\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🪙    ✨    🪙    ✨    🪙\n"
        f"    **+{reward} 💰**\n"
        f"🪙    ✨    🪙    ✨    🪙\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🔥 Стрик: **{new_streak} дн.**\n"
        f"💰 Баланс: **{new_balance}** монет",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="Markdown",
        reply_markup=markup
    )
    bot.answer_callback_query(call.id, f"➕ {reward} монет! Стрик: {new_streak} дн.")


@bot.callback_query_handler(func=lambda call: call.data == 'bonus_disabled')
def handle_disabled_bonus_click(call):
    bot.answer_callback_query(call.id, "⏳ Бонус уже получен! Завтра приходи.", show_alert=True)


# ============ ОСТАВШИЕСЯ ИГРЫ (СОХРАНЯЕМ СТАРУЮ ЛОГИКУ) ============

active_games = {}
active_rps_games = {}


@bot.message_handler(commands=['guess'])
def start_guess_game(message):
    """Запуск игры Угадай число (для обратной совместимости)"""
    user_id = message.from_user.id
    pet = get_active_pet(user_id)

    if pet is None:
        bot.send_message(message.chat.id, "❌ У тебя нет питомца! Используй /start")
        return

    if user_id in active_games:
        bot.send_message(message.chat.id, "⚠️ Ты уже играешь!")
        return

    secret = random.randint(1, 10)
    active_games[user_id] = secret

    markup = InlineKeyboardMarkup(row_width=5)
    buttons = []
    for i in range(1, 11):
        buttons.append(InlineKeyboardButton(str(i), callback_data=f"guess_{i}"))
    markup.add(*buttons)
    markup.add(InlineKeyboardButton("🏳️ Сдаться", callback_data="guess_giveup"))

    bot.send_message(
        message.chat.id,
        f"🎯 **Угадай число!**\n\n"
        f"Я загадал число от 1 до 10.\n"
        f"💰 Награда: **20 монет**\n"
        f"📉 Штраф: **-5 монет**\n\n"
        f"Выбери число:",
        parse_mode="Markdown",
        reply_markup=markup
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith('guess_'))
def handle_guess(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id

    if user_id not in active_games:
        bot.answer_callback_query(call.id, "❌ Игра не найдена! Начни новую.", show_alert=True)
        return

    # Запрашиваем актуальные данные питомца
    pet = get_active_pet(user_id)
    if pet is None:
        bot.answer_callback_query(call.id, "❌ Питомец не найден!")
        return

    _, name, _, _, energy, _, _, _ = pet
    secret = active_games[user_id]

    # Клавиатура для финала (Перезапуск или Возврат)
    final_markup = InlineKeyboardMarkup(row_width=2)
    final_markup.add(
        InlineKeyboardButton("🔄 Играть заново", callback_data="game_guess"),
        InlineKeyboardButton("🎮 К играм", callback_data="menu_games")
    )

    # ЛОГИКА КНОПКИ "СДАТЬСЯ" (Проверяем первой, чтобы избежать краша)
    if call.data == "guess_giveup":
        del active_games[user_id]
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=f"🏳️ **Ты сдался!**\n\nЗагаданное число было: **{secret}**\n\nХочешь попробовать еще раз?",
            parse_mode="Markdown",
            reply_markup=final_markup
        )
        bot.answer_callback_query(call.id, "Ты сдался!")
        return

    # Если это не кнопка сдачи, значит нажата цифра. Безопасно парсим её:
    guess = int(call.data.split('_')[1])

    # СЛУЧАЙ А: Победил (угадал)
    if guess == secret:
        del active_games[user_id]
        new_balance = change_balance(user_id, 20)

        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=f"🎉 **ПОЗДРАВЛЯЮ!**\n\n"
                 f"Ты угадал число **{secret}**!\n"
                 f"💰 Ты получил **20 монет**!\n"
                 f"🔋 Энергия питомца: **{int(energy)}%**\n"
                 f"💰 Новый баланс: **{new_balance}** монет",
            parse_mode="Markdown",
            reply_markup=final_markup
        )
        bot.answer_callback_query(call.id, f"🎉 Угадал! +20 монет!")

    # СЛУЧАЙ Б: Не угадал (ошибка)
    else:
        hint = "больше" if guess < secret else "меньше"
        new_balance = change_balance(user_id, -5)

        # Пересоздаем кнопки цифр для следующей попытки
        markup = InlineKeyboardMarkup(row_width=5)
        buttons = []
        for i in range(1, 11):
            buttons.append(InlineKeyboardButton(str(i), callback_data=f"guess_{i}"))
        markup.add(*buttons)
        markup.add(InlineKeyboardButton("🏳️ Сдаться", callback_data="guess_giveup"))

        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=f"❌ Число **{guess}** не верно!\n"
                 f"📈 Загаданное число **{hint}** чем {guess}\n"
                 f"💰 -5 монет за попытку\n"
                 f"🔋 Энергия питомца: **{int(energy)}%**\n"
                 f"💰 Новый баланс: **{new_balance}** монет\n\n"
                 f"Попробуй снова! Выбери число:",
            parse_mode="Markdown",
            reply_markup=markup
        )
        bot.answer_callback_query(call.id, f"❌ Не угадал! -5 монет.")


@bot.message_handler(commands=['rps'])
def start_rps_game(message):
    """Запуск игры КНБ (для обратной совместимости)"""
    user_id = message.from_user.id
    pet = get_active_pet(user_id)

    if pet is None:
        bot.send_message(message.chat.id, "❌ У тебя нет питомца! Используй /start")
        return

    user_id, name, hunger, happiness, energy, is_alive, balance, last_activity = pet
    if balance < 5:
        bot.send_message(message.chat.id, "❌ У тебя слишком мало монет, чтобы играть (минимум 5)!")
        return

    active_rps_games[user_id] = True

    markup = InlineKeyboardMarkup(row_width=3)
    markup.add(
        InlineKeyboardButton("🪨 Камень", callback_data="rps_rock"),
        InlineKeyboardButton("✂️ Ножницы", callback_data="rps_scissors"),
        InlineKeyboardButton("📄 Бумага", callback_data="rps_paper")
    )
    markup.add(InlineKeyboardButton("🏳️ Сдаться", callback_data="rps_giveup"))

    bot.send_message(
        message.chat.id,
        f"✊✌️✋ **Камень, Ножницы, Бумага!**\n\n"
        f"Сыграй против ИИ бота!\n"
        f"💰 Награда за победу: **15 монет**\n"
        f"📉 Проигрыш: **-5 монет**\n"
        f"🤝 Ничья: без изменений\n\n"
        f"Сделай свой ход:",
        parse_mode="Markdown",
        reply_markup=markup
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith('rps_'))
def handle_rps(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id

    if user_id not in active_rps_games:
        bot.answer_callback_query(call.id, "❌ Игра не найдена! Начни новую.", show_alert=True)
        return

    # СОЗДАЕМ КНОПКИ ДЛЯ ФИНАЛА (Победа / Сдача / Проигрыш / Ничья)
    final_markup = InlineKeyboardMarkup(row_width=2)
    final_markup.add(
        InlineKeyboardButton("🔄 Играть заново", callback_data="game_rps"),
        InlineKeyboardButton("🎮 К играм", callback_data="menu_games")
    )

    # Логика кнопки "Сдаться"
    if call.data == "rps_giveup":
        del active_rps_games[user_id]
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text="🏳️ **Ты отказался от игры!**\n\nСыграем еще раз?",
            parse_mode="Markdown",
            reply_markup=final_markup  # Добавили новые кнопки вместо текста про "/"
        )
        bot.answer_callback_query(call.id, "Ты сдался!")
        return

    player_choice = call.data.split('_')[1]
    choices = ["rock", "scissors", "paper"]
    bot_choice = random.choice(choices)

    ru_names = {"rock": "🪨 Камень", "scissors": "✂️ Ножницы", "paper": "📄 Бумага"}

    pet = get_active_pet(user_id)
    if pet is None:
        del active_rps_games[user_id]
        bot.answer_callback_query(call.id, "❌ Питомец не найден!")
        return

    # Просчет результатов
    if player_choice == bot_choice:
        result_text = "🤝 **Ничья!**"
        alert_msg = "🤝 Ничья!"
        new_balance = pet[6]
    elif (player_choice == "rock" and bot_choice == "scissors") or \
            (player_choice == "scissors" and bot_choice == "paper") or \
            (player_choice == "paper" and bot_choice == "rock"):
        new_balance = change_balance(user_id, 15)
        result_text = f"🎉 **ПОБЕДА!**\n💰 Ты получил **15 монет**!"
        alert_msg = "🎉 Победа! +15 монет!"
    else:
        new_balance = change_balance(user_id, -5)
        result_text = f"❌ **ПРОИГРЫШ!**\n💰 Списано **5 монет**."
        alert_msg = "❌ Проигрыш! -5 монет."

    del active_rps_games[user_id]

    bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text=f"✊✌️✋ **Результаты игры:**\n\n"
             f"🧑 Твой ход: **{ru_names[player_choice]}**\n"
             f"🤖 Ход соперника: **{ru_names[bot_choice]}**\n\n"
             f"{result_text}\n"
             f"💰 Новый баланс: **{new_balance}** монет",
        parse_mode="Markdown",
        reply_markup=final_markup  # Применяем кнопки к финалу матча
    )
    bot.answer_callback_query(call.id, alert_msg)

# ============ ЗАПУСК БОТА ============

if __name__ == "__main__":
    print("🤖 Бот-тамагочи запущен!")
    print("🎮 Игры активны!")
    print("🔄 Голод -5, Счастье -3, Энергия -2 каждые 10 минут!")
    print("📊 Доступные команды: /start, /status, /feed, /sleep, /play, /guess, /rps, /guess_stats, /shop")
    bot.infinity_polling()
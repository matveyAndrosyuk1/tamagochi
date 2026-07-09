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

FREE_FEED_INTERVAL = 6 * 3600
FREE_SLEEP_INTERVAL = 4 * 3600
FREE_PLAY_INTERVAL = 3 * 3600


def get_db_connection():
    return sqlite3.connect('tamagochi.db', timeout=10)


def init_db():
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pets (
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
                is_sleeping BOOLEAN DEFAULT 0,
                went_to_sleep_time TEXT DEFAULT NULL,
                death_cause TEXT DEFAULT NULL
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS free_actions (
                user_id INTEGER PRIMARY KEY,
                last_feed TIMESTAMP DEFAULT '1970-01-01',
                last_sleep TIMESTAMP DEFAULT '1970-01-01',
                last_play TIMESTAMP DEFAULT '1970-01-01',
                FOREIGN KEY (user_id) REFERENCES pets (user_id)
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS inventory (
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

        try:
            cursor.execute("SELECT is_sleeping FROM pets LIMIT 1")
        except sqlite3.OperationalError:
            cursor.execute("ALTER TABLE pets ADD COLUMN is_sleeping BOOLEAN DEFAULT 0")
            cursor.execute("ALTER TABLE pets ADD COLUMN went_to_sleep_time TEXT DEFAULT NULL")

        try:
            cursor.execute("SELECT death_cause FROM pets LIMIT 1")
        except sqlite3.OperationalError:
            cursor.execute("ALTER TABLE pets ADD COLUMN death_cause TEXT DEFAULT NULL")

        cursor.execute('PRAGMA journal_mode=WAL')
        conn.commit()
        conn.close()
        print("✅ База данных готова!")


init_db()


def get_active_pet(user_id):
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
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO pets (user_id, name, hunger, happiness, energy, is_alive, balance, last_activity, bonus_streak, last_bonus_time, is_sleeping, went_to_sleep_time)
            VALUES (?, ?, 80, 50, 100, 1, 1000, CURRENT_TIMESTAMP, 0, NULL, 0, NULL)
        ''', (user_id, name))
        conn.commit()
        conn.close()


def update_pet_stats(user_id, **kwargs):
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


def can_use_free_action(user_id, action):
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


def get_pet_sleep_status(user_id):
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


SHOP_ITEMS = {
    "яблоко": {"price": 10, "emoji": "🍎", "type": "food", "hunger": 15, "happiness": 0, "energy": 0},
    "банан": {"price": 12, "emoji": "🍌", "type": "food", "hunger": 18, "happiness": 0, "energy": 0},
    "морковка": {"price": 8, "emoji": "🥕", "type": "food", "hunger": 12, "happiness": 0, "energy": 0},
    "куриная ножка": {"price": 20, "emoji": "🍗", "type": "food", "hunger": 25, "happiness": 0, "energy": 0},
    "рыбка": {"price": 22, "emoji": "🐟", "type": "food", "hunger": 28, "happiness": 0, "energy": 0},
    "стейк": {"price": 30, "emoji": "🥩", "type": "food", "hunger": 35, "happiness": 5, "energy": 0},
    "пицца": {"price": 25, "emoji": "🍕", "type": "food", "hunger": 30, "happiness": 5, "energy": 0},
    "суши": {"price": 35, "emoji": "🍣", "type": "food", "hunger": 30, "happiness": 15, "energy": 0},
    "мороженое": {"price": 15, "emoji": "🍦", "type": "food", "hunger": 12, "happiness": 15, "energy": 0},
    "печенье": {"price": 10, "emoji": "🍪", "type": "food", "hunger": 8, "happiness": 10, "energy": 0},
    "сок": {"price": 12, "emoji": "🧃", "type": "food", "hunger": 15, "happiness": 8, "energy": 0},
    "молоко": {"price": 10, "emoji": "🥛", "type": "food", "hunger": 15, "happiness": 5, "energy": 0},
    "домашний обед": {"price": 50, "emoji": "🧺", "type": "food", "hunger": 50, "happiness": 15, "energy": 0},
    "гурме-набор": {"price": 80, "emoji": "🎁", "type": "food", "hunger": 70, "happiness": 30, "energy": 0},
    "витамины": {"price": 20, "emoji": "💊", "type": "medicine", "hunger": 0, "happiness": 0, "energy": 20},
    "эликсир": {"price": 50, "emoji": "🧪", "type": "medicine", "hunger": 0, "happiness": 0, "energy": 100},
    "чай": {"price": 15, "emoji": "🍵", "type": "medicine", "hunger": 0, "happiness": 5, "energy": 10},
    "энергетик": {"price": 25, "emoji": "🔋", "type": "medicine", "hunger": 0, "happiness": -5, "energy": 30},
}

ITEM_EFFECTS = {
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
    "гурме-набор": {"hunger": 70, "happiness": 30},
    "витамины": {"hunger": 0, "happiness": 0, "energy": 20},
    "эликсир": {"hunger": 0, "happiness": 0, "energy": 100},
    "чай": {"hunger": 0, "happiness": 5, "energy": 10},
    "энергетик": {"hunger": 0, "happiness": -5, "energy": 30},
}

INVENTORY_EMOJIS = {
    "яблоко": "🍎", "банан": "🍌", "морковка": "🥕",
    "куриная ножка": "🍗", "рыбка": "🐟", "стейк": "🥩",
    "пицца": "🍕", "суши": "🍣", "мороженое": "🍦",
    "печенье": "🍪", "сок": "🧃", "молоко": "🥛",
    "домашний обед": "🧺", "гурме-набор": "🎁",
    "витамины": "💊", "эликсир": "🧪", "чай": "🍵", "энергетик": "🔋"
}


def decrease_stats_all_pets():
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
                new_energy = min(100, energy + (10 / 6))
                new_hunger = max(0, hunger - (6 / 6))
                new_happiness = max(0, happiness - (3 / 6))
                if new_hunger <= 0:
                    cursor.execute('''
                        UPDATE pets SET hunger=0, happiness=0, energy=0, is_alive=0, death_cause='💤 Смерть во сне от истощения'
                        WHERE user_id=?
                    ''', (user_id,))
                    try:
                        bot.send_message(
                            user_id,
                            f"🪦 **Трагические новости...**\n\nТвой питомец **{name}** слишком долго спал и умер во сне от истощения.\nНачни заново с /start"
                        )
                    except Exception:
                        pass
                else:
                    cursor.execute('''
                        UPDATE pets SET hunger=?, happiness=?, energy=? WHERE user_id=?
                    ''', (new_hunger, new_happiness, new_energy, user_id))
                continue
            new_hunger = max(0, hunger - 10)
            new_happiness = max(0, happiness - 3)
            new_energy = max(0, energy - 8)
            if new_hunger <= 0 or new_energy <= 0:
                if new_hunger <= 0 and new_energy <= 0:
                    cause = "🍽️ Голод и истощение"
                elif new_hunger <= 0:
                    cause = "🍽️ Голод"
                else:
                    cause = "⚡ Истощение"
                cursor.execute('''
                    UPDATE pets SET hunger=0, happiness=0, energy=0, is_alive=0, death_cause=?
                    WHERE user_id=?
                ''', (cause, user_id))
                try:
                    bot.send_message(
                        user_id,
                        f"🪦 **Печальные новости...**\n\nТвой питомец **{name}** погиб от истощения.\nПричина: {cause}\n\nТы можешь завести нового друга с помощью /start."
                    )
                except Exception:
                    pass
            else:
                cursor.execute('''
                    UPDATE pets SET hunger=?, happiness=?, energy=? WHERE user_id=?
                ''', (new_hunger, new_happiness, new_energy, user_id))
        conn.commit()
        conn.close()


def start_stats_scheduler():
    def schedule_loop():
        while True:
            schedule.run_pending()
            time.sleep(1)
    schedule.every(10).minutes.do(decrease_stats_all_pets)
    thread = threading.Thread(target=schedule_loop, daemon=True)
    thread.start()
    print("🔄 Система обновления запущена!")


start_stats_scheduler()


def show_main_menu(chat_id, user_id):
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
        f"🏠 **ГЛАВНОЕ МЕНЮ**\n━━━━━━━━━━━━━━━━━━━\n"
        f"🐾 Питомец: **{name}**\n"
        f"💰 Баланс: **{balance}** монет\n"
        f"🍖 Голод: {hunger}/100\n"
        f"😊 Счастье: {happiness}/100\n"
        f"⚡ Энергия: {energy}/100\n━━━━━━━━━━━━━━━━━━━\n"
        f"Выбери действие:"
    )
    bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=markup)


@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    pet = get_active_pet(user_id)
    if pet is None:
        with DB_LOCK:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT name, death_cause FROM pets WHERE user_id = ? AND is_alive = 0 ORDER BY pet_id DESC LIMIT 1
            ''', (user_id,))
            dead_pet = cursor.fetchone()
            conn.close()
        if dead_pet:
            name, cause = dead_pet
            msg = bot.send_message(
                message.chat.id,
                f"🪦 **Твой питомец {name} умер.**\nПричина: {cause}\n\nХочешь завести нового?\n**Как назовём его?**",
                parse_mode="Markdown"
            )
            bot.register_next_step_handler(msg, process_name_step)
        else:
            msg = bot.send_message(
                message.chat.id,
                "🐱 Привет! У тебя нет питомца.\n**Как назовём твоего нового друга?**",
                parse_mode="Markdown"
            )
            bot.register_next_step_handler(msg, process_name_step)
    else:
        show_main_menu(message.chat.id, user_id)


def process_name_step(message):
    user_id = message.from_user.id
    pet_name = message.text.strip()
    if pet_name.startswith('/'):
        bot.send_message(message.chat.id, "❌ Имя не должно начинаться с команды!")
        return
    if len(pet_name) > 20:
        msg = bot.send_message(message.chat.id, "⚠️ Слишком длинное имя (до 20 символов):")
        bot.register_next_step_handler(msg, process_name_step)
        return
    create_pet(user_id, pet_name)
    show_main_menu(message.chat.id, user_id)


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


shop_cart = {}


def show_shop(message, user_id):
    pet = get_active_pet(user_id)
    if pet is None:
        bot.send_message(message.chat.id, "❌ У тебя нет питомца!")
        return
    user_id, name, hunger, happiness, energy, is_alive, balance, last_activity = pet
    markup = InlineKeyboardMarkup(row_width=2)
    food_items = [k for k, v in SHOP_ITEMS.items() if v['type'] == 'food']
    medicine_items = [k for k, v in SHOP_ITEMS.items() if v['type'] == 'medicine']
    for item_name in food_items:
        info = SHOP_ITEMS[item_name]
        markup.add(InlineKeyboardButton(
            f"{info['emoji']} {item_name.title()} — {info['price']}💰",
            callback_data=f"shop_select:{item_name}"
        ))
    if medicine_items:
        markup.add(InlineKeyboardButton("═" * 10, callback_data="shop_separator"))
        for item_name in medicine_items:
            info = SHOP_ITEMS[item_name]
            markup.add(InlineKeyboardButton(
                f"{info['emoji']} {item_name.title()} — {info['price']}💰",
                callback_data=f"shop_select:{item_name}"
            ))
    markup.add(InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main"))
    text = (
        f"🛒 **МАГАЗИН**\n━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Баланс: **{balance}** монет\n━━━━━━━━━━━━━━━━━━━\n"
        f"🍖 **Еда** — голод и счастье\n💊 **Лекарства** — энергия\n━━━━━━━━━━━━━━━━━━━\n"
        f"Выбери товар:"
    )
    try:
        bot.edit_message_text(text, chat_id=message.chat.id, message_id=message.message_id, parse_mode="Markdown", reply_markup=markup)
    except Exception:
        bot.send_message(message.chat.id, text, parse_mode="Markdown", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith('shop_select:'))
def shop_select(call):
    user_id = call.from_user.id
    item_name = call.data.replace('shop_select:', '')
    if item_name not in SHOP_ITEMS:
        bot.answer_callback_query(call.id, "❌ Товар не найден!", show_alert=True)
        return
    pet = get_active_pet(user_id)
    if pet is None:
        bot.answer_callback_query(call.id, "❌ Нет питомца!", show_alert=True)
        return
    shop_cart[user_id] = {'item': item_name, 'quantity': 1}
    show_quantity_selector(call.message, user_id, item_name)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == 'shop_separator')
def shop_separator(call):
    bot.answer_callback_query(call.id)


def show_quantity_selector(message, user_id, item_name, error_msg=""):
    pet = get_active_pet(user_id)
    if pet is None:
        return
    user_id, name, hunger, happiness, energy, is_alive, balance, last_activity = pet
    quantity = shop_cart.get(user_id, {}).get('quantity', 1)
    info = SHOP_ITEMS[item_name]
    price = info['price']
    total = price * quantity
    emoji = info['emoji']
    effects = []
    if info.get('hunger', 0) > 0:
        effects.append(f"+{info['hunger']} голода")
    if info.get('happiness', 0) != 0:
        effects.append(f"{'+' if info['happiness'] > 0 else ''}{info['happiness']} счастья")
    if info.get('energy', 0) > 0:
        effects.append(f"+{info['energy']} энергии")
    markup = InlineKeyboardMarkup(row_width=3)
    markup.add(
        InlineKeyboardButton("➖", callback_data=f"shop_qty:{item_name}:minus"),
        InlineKeyboardButton(f"{quantity} шт.", callback_data="shop_qty_display"),
        InlineKeyboardButton("➕", callback_data=f"shop_qty:{item_name}:plus")
    )
    markup.add(
        InlineKeyboardButton(f"💳 Купить за {total}💰", callback_data=f"shop_buy:{item_name}:{quantity}"),
        InlineKeyboardButton("↩️ Назад", callback_data="shop_cancel")
    )
    markup.add(InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main"))
    text = (
        f"{emoji} **{item_name.title()}**\n━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Цена: **{price}** монет/шт.\n"
        f"💊 Эффект: {', '.join(effects)}\n"
        f"💰 Баланс: **{balance}** монет\n"
        f"💳 Итого: **{total}** монет\n━━━━━━━━━━━━━━━━━━━\n"
    )
    if error_msg:
        text += f"⚠️ **{error_msg}**\n━━━━━━━━━━━━━━━━━━━\n"
    text += "Измени количество или купи:"
    bot.edit_message_text(text, chat_id=message.chat.id, message_id=message.message_id, parse_mode="Markdown", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith('shop_qty:'))
def shop_qty(call):
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
    show_quantity_selector(call.message, user_id, item_name)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == 'shop_qty_display')
def shop_qty_display(call):
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == 'shop_cancel')
def shop_cancel(call):
    user_id = call.from_user.id
    if user_id in shop_cart:
        del shop_cart[user_id]
    show_shop(call.message, user_id)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith('shop_buy:'))
def shop_buy(call):
    user_id = call.from_user.id
    data = call.data.split(':')
    item_name = data[1]
    quantity = int(data[2])
    if item_name not in SHOP_ITEMS:
        bot.answer_callback_query(call.id, "❌ Товар не найден!", show_alert=True)
        return
    info = SHOP_ITEMS[item_name]
    total_price = info['price'] * quantity
    pet = get_active_pet(user_id)
    if pet is None:
        bot.answer_callback_query(call.id, "❌ Нет питомца!", show_alert=True)
        return
    user_id, name, hunger, happiness, energy, is_alive, balance, last_activity = pet
    if balance < total_price:
        bot.answer_callback_query(call.id, f"Не хватает монет! Нужно: {total_price}💰, у тебя: {balance}💰", show_alert=True)
        show_quantity_selector(call.message, user_id, item_name, error_msg="Не хватает монет!")
        return
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        new_balance = balance - total_price
        cursor.execute("UPDATE pets SET balance = ? WHERE user_id = ? AND is_alive = 1", (new_balance, user_id))
        cursor.execute('''
            INSERT INTO inventory (user_id, item_name, quantity, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP) ON CONFLICT(user_id, item_name) 
            DO UPDATE SET quantity = quantity + ?, updated_at = CURRENT_TIMESTAMP
        ''', (user_id, item_name, quantity, quantity))
        conn.commit()
        conn.close()
    if user_id in shop_cart:
        del shop_cart[user_id]
    emoji = info['emoji']
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("🔄 Купить еще", callback_data="menu_shop"),
        InlineKeyboardButton("🎒 В инвентарь", callback_data="menu_inventory"),
        InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")
    )
    text = (
        f"✅ **Покупка успешна!**\n━━━━━━━━━━━━━━━━━━━\n"
        f"{emoji} Куплено: **{item_name.title()}** ×{quantity}\n"
        f"💰 Списано: **{total_price}** монет\n"
        f"💰 Остаток: **{new_balance}** монет\n━━━━━━━━━━━━━━━━━━━\n"
        f"Куда отправимся?"
    )
    bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=markup)
    bot.answer_callback_query(call.id, f"✅ {item_name.title()} ×{quantity} куплено!")


def show_inventory(message, user_id, page=1, items_per_page=5):
    pet = get_active_pet(user_id)
    if pet is None:
        bot.send_message(message.chat.id, "❌ Нет питомца!")
        return
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT item_name, quantity FROM inventory WHERE user_id = ? AND quantity > 0 ORDER BY item_name", (user_id,))
        all_items = cursor.fetchall()
        conn.close()
    if not all_items:
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton("🏪 Магазин", callback_data="menu_shop"),
            InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")
        )
        bot.edit_message_text(
            "🎒 **ИНВЕНТАРЬ**\n━━━━━━━━━━━━━━━━━━━\n🪹 Пусто!\nКупи что-нибудь в магазине.",
            chat_id=message.chat.id, message_id=message.message_id, parse_mode="Markdown", reply_markup=markup
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
    text = f"🎒 **ИНВЕНТАРЬ**\n━━━━━━━━━━━━━━━━━━━\n📦 Всего: {total_items} предметов\n━━━━━━━━━━━━━━━━━━━\n\n"
    markup = InlineKeyboardMarkup(row_width=2)
    for item_name, quantity in items:
        emoji = INVENTORY_EMOJIS.get(item_name, "📦")
        text += f"{emoji} **{item_name.title()}** ×{quantity}\n"
        markup.add(
            InlineKeyboardButton(f"🍖 Использовать", callback_data=f"inv_use_select:{item_name}"),
            InlineKeyboardButton(f"🗑️ Выбросить", callback_data=f"inv_drop:{item_name}")
        )
    text += "\n━━━━━━━━━━━━━━━━━━━\n"
    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton("◀️ Назад", callback_data=f"inv_page:{page - 1}"))
    nav_buttons.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data="inv_page_display"))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton("Вперед ▶️", callback_data=f"inv_page:{page + 1}"))
    if nav_buttons:
        markup.row(*nav_buttons)
    markup.add(InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main"))
    bot.edit_message_text(text, chat_id=message.chat.id, message_id=message.message_id, parse_mode="Markdown", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith('inv_page:'))
def inv_page(call):
    user_id = call.from_user.id
    page = int(call.data.split(':')[1])
    show_inventory(call.message, user_id, page)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data == 'inv_page_display')
def inv_page_display(call):
    bot.answer_callback_query(call.id)


use_cart = {}


@bot.callback_query_handler(func=lambda call: call.data.startswith('inv_use_select:'))
def inv_use_select(call):
    user_id = call.from_user.id
    item_name = call.data.replace('inv_use_select:', '')
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT quantity FROM inventory WHERE user_id = ? AND item_name = ?", (user_id, item_name))
        res = cursor.fetchone()
        conn.close()
    if not res or res[0] <= 0:
        bot.answer_callback_query(call.id, "❌ Нет этого предмета!", show_alert=True)
        return
    use_cart[user_id] = {'item': item_name, 'quantity': 1, 'max_quantity': res[0]}
    show_use_quantity_selector(call.message, user_id, item_name)


def show_use_quantity_selector(message, user_id, item_name):
    pet = get_active_pet(user_id)
    if pet is None:
        return
    if user_id not in use_cart:
        return
    cart = use_cart[user_id]
    quantity = cart['quantity']
    max_qty = cart['max_quantity']
    effect = ITEM_EFFECTS.get(item_name, {"hunger": 0, "happiness": 0, "energy": 0})
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
        f"{emoji} **Использовать {item_name.title()}**\n━━━━━━━━━━━━━━━━━━━\n"
        f"📦 В наличии: **{max_qty}** шт.\n"
        f"🍖 Эффект: "
    )
    if effect.get("hunger", 0) > 0:
        text += f"+{effect['hunger']} голода, "
    if effect.get("happiness", 0) != 0:
        text += f"{'+' if effect['happiness'] > 0 else ''}{effect['happiness']} счастья, "
    if effect.get("energy", 0) > 0:
        text += f"+{effect['energy']} энергии"
    text += f"\n━━━━━━━━━━━━━━━━━━━\n📦 Количество: **{quantity}** шт.\n━━━━━━━━━━━━━━━━━━━\nСколько использовать?"
    bot.edit_message_text(text, chat_id=message.chat.id, message_id=message.message_id, parse_mode="Markdown", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith('use_qty:'))
def use_qty(call):
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
    user_id = call.from_user.id
    if user_id in use_cart:
        del use_cart[user_id]
    show_inventory(call.message, user_id)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith('use_confirm:'))
def use_confirm(call):
    user_id = call.from_user.id
    data = call.data.split(':')
    item_name = data[1]
    quantity = int(data[2])
    effect = ITEM_EFFECTS.get(item_name, {"hunger": 0, "happiness": 0, "energy": 0})
    pet = get_active_pet(user_id)
    if pet is None:
        bot.answer_callback_query(call.id, "❌ Нет питомца!", show_alert=True)
        return
    user_id, name, hunger, happiness, energy, is_alive, balance, last_activity = pet
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
        if new_qty > 0:
            cursor.execute("UPDATE inventory SET quantity = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND item_name = ?", (new_qty, user_id, item_name))
        else:
            cursor.execute("DELETE FROM inventory WHERE user_id = ? AND item_name = ?", (user_id, item_name))
        total_hunger = effect.get("hunger", 0) * quantity
        total_happiness = effect.get("happiness", 0) * quantity
        total_energy = effect.get("energy", 0) * quantity
        new_hunger = min(100, max(0, hunger + total_hunger))
        new_happiness = min(100, max(0, happiness + total_happiness))
        new_energy = min(100, max(0, energy + total_energy))
        cursor.execute('''
            UPDATE pets SET hunger=?, happiness=?, energy=?, last_activity=CURRENT_TIMESTAMP
            WHERE user_id=? AND is_alive=1
        ''', (new_hunger, new_happiness, new_energy, user_id))
        conn.commit()
        conn.close()
    if user_id in use_cart:
        del use_cart[user_id]
    emoji = INVENTORY_EMOJIS.get(item_name, "📦")
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("🎒 Инвентарь", callback_data="menu_inventory"),
        InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")
    )
    text = (
        f"{emoji} **{item_name.title()} использован!**\n━━━━━━━━━━━━━━━━━━━\n"
        f"🐾 {name} съел {quantity} шт. {item_name}!\n"
        f"🍖 Голод: {hunger} → {new_hunger}/100\n"
        f"😊 Счастье: {happiness} → {new_happiness}/100\n"
        f"⚡ Энергия: {energy} → {new_energy}/100\n"
        f"📦 Осталось: {new_qty} шт.\n━━━━━━━━━━━━━━━━━━━\nЧто дальше?"
    )
    bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=markup)
    bot.answer_callback_query(call.id, f"✅ {item_name.title()} использован!")


@bot.callback_query_handler(func=lambda call: call.data.startswith('inv_drop:'))
def inv_drop(call):
    user_id = call.from_user.id
    item_name = call.data.replace('inv_drop:', '')
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("✅ Да, выбросить", callback_data=f"drop_confirm:{item_name}"),
        InlineKeyboardButton("❌ Отмена", callback_data="menu_inventory")
    )
    text = f"🗑️ **ВЫБРОСИТЬ {item_name.upper()}?**\n━━━━━━━━━━━━━━━━━━━\n⚠️ Это действие НЕЛЬЗЯ отменить!\n━━━━━━━━━━━━━━━━━━━\nТы уверен?"
    bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=markup)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith('drop_confirm:'))
def drop_confirm(call):
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
            cursor.execute("UPDATE inventory SET quantity = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND item_name = ?", (new_qty, user_id, item_name))
        else:
            cursor.execute("DELETE FROM inventory WHERE user_id = ? AND item_name = ?", (user_id, item_name))
        conn.commit()
        conn.close()
    bot.answer_callback_query(call.id, f"🗑️ {item_name.title()} выброшен!")
    show_inventory(call.message, user_id)


def show_pet_info(message, user_id):
    pet = get_active_pet(user_id)
    if pet is None:
        bot.send_message(message.chat.id, "❌ Нет питомца!")
        return
    user_id, name, hunger, happiness, energy, is_alive, balance, last_activity = pet
    sleep_data = get_pet_sleep_status(user_id)
    is_sleeping = sleep_data[0] if sleep_data else 0
    hunger_display = int(hunger)
    hunger_bar = "█" * (hunger_display // 10) + "░" * (10 - hunger_display // 10)
    happiness_bar = "█" * (happiness // 10) + "░" * (10 - happiness // 10)
    energy_bar = "█" * (int(energy) // 10) + "░" * (10 - int(energy) // 10)
    markup = InlineKeyboardMarkup()
    if is_sleeping:
        markup.add(InlineKeyboardButton("⏰ Проснуться / Встать", callback_data="action_wake_up"))
    else:
        markup.add(
            InlineKeyboardButton("🏪 Магазин", callback_data="menu_shop"),
            InlineKeyboardButton("🎒 Инвентарь", callback_data="menu_inventory")
        )
        if energy < 20:
            sleep_button = InlineKeyboardButton("😴 Уложить спать", callback_data="action_go_to_sleep")
        else:
            sleep_button = InlineKeyboardButton("🔒 Сон (Энергия >= 20)", callback_data="action_sleep_locked")
        markup.add(InlineKeyboardButton("🍖 Покормить", callback_data="action_feed"), sleep_button)
        markup.add(InlineKeyboardButton("🎮 Поиграть", callback_data="action_play"))
    markup.add(InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main"))
    status_label = "💤 КРЕПКО СПИТ" if is_sleeping else "🔋 Бодрствует"
    text = (
        f"🐾 **МОЙ ПИТОМЕЦ**\n━━━━━━━━━━━━━━━━━━━\n"
        f"Имя: **{name}**\n❤️ Статус: **{status_label}**\n━━━━━━━━━━━━━━━━━━━\n"
        f"📊 **Параметры:**\n"
        f"🍖 Голод: {hunger_display}/100\n┃{hunger_bar}\n"
        f"😊 Счастье: {happiness}/100\n┃{happiness_bar}\n"
        f"⚡ Энергия: {int(energy)}/100\n┃{energy_bar}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Баланс: **{balance}** монет\n━━━━━━━━━━━━━━━━━━━\n"
        f"Выбери действие:"
    )
    bot.edit_message_text(text, chat_id=message.chat.id, message_id=message.message_id, parse_mode="Markdown", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data == 'action_feed')
def action_feed(call):
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
    show_pet_info(call.message, user_id)


@bot.callback_query_handler(func=lambda call: call.data == 'action_sleep_locked')
def action_sleep_locked(call):
    bot.answer_callback_query(call.id, "⚠️ Питомец полон сил! Уложить спать можно только при энергии меньше 20%.", show_alert=True)


@bot.callback_query_handler(func=lambda call: call.data == 'action_go_to_sleep')
def action_go_to_sleep(call):
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
    bot.answer_callback_query(call.id, "💤 Твой питомец уснул!", show_alert=True)
    show_pet_info(call.message, user_id)


@bot.callback_query_handler(func=lambda call: call.data == 'action_wake_up')
def action_wake_up(call):
    user_id = call.from_user.id
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE pets SET is_sleeping = 0, went_to_sleep_time = NULL WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()
    bot.answer_callback_query(call.id, "⏰ Питомец проснулся!", show_alert=True)
    show_pet_info(call.message, user_id)


@bot.callback_query_handler(func=lambda call: call.data == 'action_play')
def action_play(call):
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


def bot_send_status(message, user_id):
    pet = get_active_pet(user_id)
    if pet is None:
        bot.send_message(message.chat.id, "❌ Нет питомца!")
        return
    user_id, name, hunger, happiness, energy, is_alive, balance, last_activity = pet
    hunger_status = "🍔 Сыт" if hunger >= 80 else "😐 Нормально" if hunger >= 50 else "😟 Хочет есть" if hunger >= 20 else "⚠️ Голодный!"
    happiness_status = "😄 Счастлив" if happiness >= 80 else "🙂 Нормально" if happiness >= 50 else "😐 Немного грустный" if happiness >= 20 else "😢 Грустный!"
    energy_status = "⚡ Полон сил" if energy >= 80 else "🔋 Нормально" if energy >= 50 else "😴 Устал" if energy >= 20 else "🥱 Очень устал!"
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("🐾 Питомец", callback_data="menu_pet"),
        InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")
    )
    text = (
        f"🐾 **Статус питомца**\n━━━━━━━━━━━━━━━━━━━\n"
        f"Имя: {name}\n❤️ Жив и здоров!\n━━━━━━━━━━━━━━━━━━━\n"
        f"🍖 Голод: {hunger}/100 ({hunger_status})\n"
        f"😊 Счастье: {happiness}/100 ({happiness_status})\n"
        f"⚡ Энергия: {energy}/100 ({energy_status})\n"
        f"💰 Баланс: {balance} монет\n━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ Голод -5, Счастье -3, Энергия -2 каждые 10 минут!"
    )
    bot.edit_message_text(text, chat_id=message.chat.id, message_id=message.message_id, parse_mode="Markdown", reply_markup=markup)


def show_games_menu(message, user_id):
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
        f"🎮 **ИГРЫ**\n━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Угадай число: 20 монет / -5 штраф\n"
        f"✊✌️✋ КНБ: 15 монет / -5 штраф\n━━━━━━━━━━━━━━━━━━━\n"
        f"Выбери игру:"
    )
    bot.edit_message_text(text, chat_id=message.chat.id, message_id=message.message_id, parse_mode="Markdown", reply_markup=markup)


def daily_bonus_menu(message, user_id):
    pet = get_active_pet(user_id)
    if pet is None:
        bot.send_message(message.chat.id, "❌ Нет питомца!")
        return
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT bonus_streak, last_bonus_time, balance FROM pets WHERE user_id = ? AND is_alive = 1", (user_id,))
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
        f"🎁 **ЕЖЕДНЕВНЫЙ БОНУС**\n━━━━━━━━━━━━━━━━━━━\n"
        f"🔥 Стрик: **{display_streak} дн.**\n"
        f"📊 До супер-награды: **{10 - days_in_current_ten} дн.**\n"
        f"🗺️ {progress_bar} {days_in_current_ten}/10\n\n"
        f"💰 Награда: **{reward} монет**\n"
    )
    markup = InlineKeyboardMarkup()
    if can_claim:
        markup.add(InlineKeyboardButton("🎁 Забрать бонус!", callback_data="claim_daily_bonus"))
    else:
        text += f"\n⏳ Через: `{time_left_str}`"
        markup.add(InlineKeyboardButton("🔒 Уже получено", callback_data="bonus_disabled"))
    markup.add(InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main"))
    bot.edit_message_text(text, chat_id=message.chat.id, message_id=message.message_id, parse_mode="Markdown", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data == 'claim_daily_bonus')
def handle_claim_bonus(call):
    user_id = call.from_user.id
    now = datetime.now()
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT bonus_streak, last_bonus_time, balance FROM pets WHERE user_id = ? AND is_alive = 1", (user_id,))
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
            UPDATE pets SET bonus_streak=?, last_bonus_time=?, balance=? WHERE user_id=? AND is_alive=1
        ''', (new_streak, now.isoformat(), new_balance, user_id))
        conn.commit()
        conn.close()
    markup = InlineKeyboardMarkup()
    markup.add(
        InlineKeyboardButton("🎁 Бонус", callback_data="menu_bonus"),
        InlineKeyboardButton("🏠 Главное меню", callback_data="menu_main")
    )
    bot.edit_message_text(
        f"✨ **БОНУС ПОЛУЧЕН!** ✨\n━━━━━━━━━━━━━━━━━━━\n🪙 ✨ 🪙 ✨ 🪙\n**+{reward} 💰**\n🪙 ✨ 🪙 ✨ 🪙\n━━━━━━━━━━━━━━━━━━━\n🔥 Стрик: **{new_streak} дн.**\n💰 Баланс: **{new_balance}** монет",
        chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=markup
    )
    bot.answer_callback_query(call.id, f"➕ {reward} монет!")


@bot.callback_query_handler(func=lambda call: call.data == 'bonus_disabled')
def handle_disabled_bonus_click(call):
    bot.answer_callback_query(call.id, "⏳ Бонус уже получен! Завтра приходи.", show_alert=True)


active_games = {}
active_rps_games = {}


def apply_game_stats_cost(user_id):
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT happiness, energy FROM pets WHERE user_id = ? AND is_alive = 1', (user_id,))
        res = cursor.fetchone()
        if res:
            current_happiness, current_energy = res
            new_happiness = min(100, int(current_happiness) + 15)
            new_energy = max(0, int(current_energy) - 5)
            cursor.execute('''
                UPDATE pets SET happiness=?, energy=? WHERE user_id=? AND is_alive=1
            ''', (new_happiness, new_energy, user_id))
            conn.commit()
        conn.close()


@bot.callback_query_handler(func=lambda call: call.data == "game_guess")
def start_guess_game_callback(call):
    user_id = call.from_user.id
    pet = get_active_pet(user_id)
    if pet is None:
        bot.answer_callback_query(call.id, "❌ Нет питомца!", show_alert=True)
        return
    _, name, _, _, energy, _, _, _ = pet
    if int(energy) < 15:
        bot.answer_callback_query(call.id, f"⚠️ {name} слишком устал ({int(energy)}%)!", show_alert=True)
        return
    if user_id in active_games:
        bot.answer_callback_query(call.id, "⚠️ Ты уже в игре!", show_alert=True)
        return
    apply_game_stats_cost(user_id)
    pet = get_active_pet(user_id)
    _, _, _, _, updated_energy, _, _, _ = pet
    secret = random.randint(1, 10)
    active_games[user_id] = secret
    markup = InlineKeyboardMarkup(row_width=5)
    buttons = []
    for i in range(1, 11):
        buttons.append(InlineKeyboardButton(str(i), callback_data=f"guess_{i}"))
    markup.add(*buttons)
    markup.add(InlineKeyboardButton("🏳️ Сдаться", callback_data="guess_giveup"))
    bot.edit_message_text(
        f"🎯 **Угадай число!**\n━━━━━━━━━━━━━━━━━━━\n"
        f"🔋 Энергия: {int(updated_energy)}%\n"
        f"💰 +20 монет | -5 за ошибку\n━━━━━━━━━━━━━━━━━━━\n"
        f"Выбери число:",
        chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=markup
    )
    bot.answer_callback_query(call.id, "Игра началась!")


@bot.callback_query_handler(func=lambda call: call.data.startswith('guess_'))
def handle_guess(call):
    user_id = call.from_user.id
    if user_id not in active_games:
        bot.answer_callback_query(call.id, "❌ Игра не найдена!", show_alert=True)
        return
    pet = get_active_pet(user_id)
    if pet is None:
        bot.answer_callback_query(call.id, "❌ Питомец не найден!", show_alert=True)
        return
    _, name, _, _, energy, _, _, _ = pet
    secret = active_games[user_id]
    final_markup = InlineKeyboardMarkup(row_width=2)
    final_markup.add(
        InlineKeyboardButton("🔄 Играть заново", callback_data="game_guess"),
        InlineKeyboardButton("🎮 К играм", callback_data="menu_games")
    )
    if call.data == "guess_giveup":
        del active_games[user_id]
        bot.edit_message_text(
            f"🏳️ **Ты сдался!**\n\nЗагаданное число: **{secret}**",
            chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=final_markup
        )
        bot.answer_callback_query(call.id, "Ты сдался!")
        return
    guess = int(call.data.split('_')[1])
    if guess == secret:
        del active_games[user_id]
        new_balance = change_balance(user_id, 20)
        bot.edit_message_text(
            f"🎉 **ПОЗДРАВЛЯЮ!**\n\nУгадал **{secret}**!\n+20 монет!\n💰 Новый баланс: **{new_balance}**",
            chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=final_markup
        )
        bot.answer_callback_query(call.id, "🎉 Угадал! +20 монет!")
    else:
        hint = "больше" if guess < secret else "меньше"
        new_balance = change_balance(user_id, -5)
        markup = InlineKeyboardMarkup(row_width=5)
        buttons = []
        for i in range(1, 11):
            buttons.append(InlineKeyboardButton(str(i), callback_data=f"guess_{i}"))
        markup.add(*buttons)
        markup.add(InlineKeyboardButton("🏳️ Сдаться", callback_data="guess_giveup"))
        bot.edit_message_text(
            f"❌ **{guess}** не верно!\n📈 Загаданное число **{hint}**\n💰 -5 монет\n💰 Новый баланс: **{new_balance}**\n\nПопробуй снова:",
            chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=markup
        )
        bot.answer_callback_query(call.id, "❌ Не угадал! -5 монет.")


@bot.callback_query_handler(func=lambda call: call.data == "game_rps")
def game_rps(call):
    user_id = call.from_user.id
    pet = get_active_pet(user_id)
    if pet is None:
        bot.answer_callback_query(call.id, "❌ Нет питомца!", show_alert=True)
        return
    _, name, _, _, energy, _, balance, _ = pet
    if int(energy) < 15:
        bot.answer_callback_query(call.id, f"⚠️ {name} слишком устал ({int(energy)}%)!", show_alert=True)
        return
    if balance < 5:
        bot.answer_callback_query(call.id, "❌ Нужно минимум 5 монет!", show_alert=True)
        return
    apply_game_stats_cost(user_id)
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
        f"✊✌️✋ **Камень, Ножницы, Бумага!**\n━━━━━━━━━━━━━━━━━━━\n"
        f"🔋 Энергия: {int(updated_energy)}%\n"
        f"💰 +15 монет | -5 монет\n━━━━━━━━━━━━━━━━━━━\n"
        f"Сделай ход:",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode="Markdown",
        reply_markup=markup
    )
    bot.answer_callback_query(call.id, "Игра началась!")


@bot.callback_query_handler(func=lambda call: call.data == 'game_stats')
def game_stats(call):
    pet = get_active_pet(call.from_user.id)
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
        f"📊 **Статистика**\n━━━━━━━━━━━━━━━━━━━\n"
        f"🐾 {name}\n💰 {balance} монет\n"
        f"🍖 {hunger}/100 | 😊 {happiness}/100 | ⚡ {energy}/100\n━━━━━━━━━━━━━━━━━━━\n"
        f"Выбери игру:",
        chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=markup
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda call: call.data.startswith('rps_'))
def handle_rps(call):
    user_id = call.from_user.id
    if user_id not in active_rps_games:
        bot.answer_callback_query(call.id, "❌ Игра не найдена!", show_alert=True)
        return
    final_markup = InlineKeyboardMarkup(row_width=2)
    final_markup.add(
        InlineKeyboardButton("🔄 Играть заново", callback_data="game_rps"),
        InlineKeyboardButton("🎮 К играм", callback_data="menu_games")
    )
    if call.data == "rps_giveup":
        del active_rps_games[user_id]
        bot.edit_message_text(
            "🏳️ **Ты отказался от игры!**",
            chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=final_markup
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
        bot.answer_callback_query(call.id, "❌ Питомец не найден!", show_alert=True)
        return
    if player_choice == bot_choice:
        result_text = "🤝 **Ничья!**"
        alert_msg = "🤝 Ничья!"
        new_balance = pet[6]
    elif (player_choice == "rock" and bot_choice == "scissors") or \
         (player_choice == "scissors" and bot_choice == "paper") or \
         (player_choice == "paper" and bot_choice == "rock"):
        new_balance = change_balance(user_id, 15)
        result_text = f"🎉 **ПОБЕДА!**\n💰 +15 монет!"
        alert_msg = "🎉 Победа! +15 монет!"
    else:
        new_balance = change_balance(user_id, -5)
        result_text = f"❌ **ПРОИГРЫШ!**\n💰 -5 монет."
        alert_msg = "❌ Проигрыш! -5 монет."
    del active_rps_games[user_id]
    bot.edit_message_text(
        f"✊✌️✋ **Результат:**\n━━━━━━━━━━━━━━━━━━━\n"
        f"🧑 Твой ход: **{ru_names[player_choice]}**\n"
        f"🤖 Ход соперника: **{ru_names[bot_choice]}**\n\n"
        f"{result_text}\n💰 Новый баланс: **{new_balance}** монет",
        chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=final_markup
    )
    bot.answer_callback_query(call.id, alert_msg)


if __name__ == "__main__":
    print("🤖 Бот-тамагочи запущен!")
    print("🔄 Голод -5, Счастье -3, Энергия -2 каждые 10 минут!")
    bot.infinity_polling()
import telebot
import sqlite3
import threading
import random
import schedule
import time
from datetime import datetime, timedelta
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

TOKEN = "8912217606:AAFAxQKalVqR1RoDNB0zNP41LfZHJn0XXzU"
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

        # Таблица для бесплатных действий
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS free_actions (
                user_id    INTEGER PRIMARY KEY,
                last_feed  TIMESTAMP DEFAULT '1970-01-01',
                last_sleep TIMESTAMP DEFAULT '1970-01-01',
                last_play  TIMESTAMP DEFAULT '1970-01-01',
                FOREIGN KEY (user_id) REFERENCES pets(user_id)
            )
        ''')

        # === АВТОМАТИЧЕСКОЕ ОБНОВЛЕНИЕ ДЛЯ ЕЖЕДНЕВНОГО БОНУСА ===
        try:
            cursor.execute("ALTER TABLE pets ADD COLUMN bonus_streak INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # Если колонка уже есть, пропускаем

        try:
            cursor.execute("ALTER TABLE pets ADD COLUMN last_bonus_time TEXT DEFAULT NULL")
        except sqlite3.OperationalError:
            pass
        # =======================================================

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


def bot_send_status(message):
    """Отправить статус питомца"""
    user_id = message.from_user.id
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

    status_text = f"""
🐾 **Статус питомца**

Имя: {name}
❤️ Статус: Жив и здоров!

📊 Параметры:
🍖 Голод: {hunger}/100 ({hunger_status})
😊 Счастье: {happiness}/100 ({happiness_status})
⚡ Энергия: {energy}/100 ({energy_status})
💰 Баланс: {balance} монет

🎮 Бесплатные действия:
/feed  - покормить (бесплатно, раз в 6ч)
/sleep - поспать (бесплатно, раз в 4ч)
/play  - поиграть (бесплатно, раз в 3ч)

🏪 Если срочно нужно:
/shop - магазин еды
💊 Лекарства в разработке

⚠️ Голод -5, Счастье -3, Энергия -2 каждые 10 минут!
    """
    bot.send_message(message.chat.id, status_text, parse_mode="Markdown")


# ============ СИСТЕМА ГОЛОДА, СЧАСТЬЯ И ЭНЕРГИИ ============

def decrease_stats_all_pets():
    """Уменьшает голод, счастье и энергию у всех живых питомцев"""
    with DB_LOCK:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT user_id, hunger, happiness, energy, name
            FROM pets 
            WHERE is_alive = 1
        ''')
        pets = cursor.fetchall()

        for user_id, hunger, happiness, energy, name in pets:
            new_hunger = max(0, hunger - 5)
            new_happiness = max(0, happiness - 3)
            new_energy = max(0, energy - 2)

            cursor.execute('''
                UPDATE pets SET hunger = ?, happiness = ?, energy = ?
                WHERE user_id = ?
            ''', (new_hunger, new_happiness, new_energy, user_id))

            if new_hunger == 0:
                print(f"⚠️ {name} голоден! Покормите его!")
            if new_happiness == 0:
                print(f"😢 {name} грустный! Поиграйте с ним!")
            if new_energy == 0:
                print(f"😴 {name} устал! Отправьте спать!")

        conn.commit()
        conn.close()
        print(f"✅ Обновлены параметры у {len(pets)} питомцев в {datetime.now().strftime('%H:%M:%S')}")


def start_stats_scheduler():
    """Запускает фоновый поток для обновления параметров"""

    def schedule_loop():
        while True:
            schedule.run_pending()
            time.sleep(1)

    schedule.every(10).minutes.do(decrease_stats_all_pets)

    thread = threading.Thread(target=schedule_loop, daemon=True)
    thread.start()
    print("🔄 Система обновления запущена! (голод -5, счастье -3, энергия -2 каждые 10 минут)")


start_stats_scheduler()


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
            f"🍖 Голод: {hunger}/100\n"
            f"😊 Счастье: {happiness}/100\n"
            f"⚡ Энергия: {energy}/100\n\n"
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
        f"🍖 Голод: 80/100\n"
        f"😊 Счастье: 50/100\n"
        f"⚡ Энергия: 100/100\n\n"
        f"🎮 Сыграй в /guess и заработай ещё монет!",
        parse_mode="Markdown"
    )


@bot.message_handler(commands=['status'])
def status(message):
    bot_send_status(message)


@bot.message_handler(commands=['feed'])
def feed(message):
    """Бесплатное кормление раз в 6 часов"""
    user_id = message.from_user.id
    pet = get_active_pet(user_id)

    if pet is None:
        bot.send_message(message.chat.id, "❌ У тебя нет активного питомца! Используй /start, чтобы завести.")
        return

    user_id, name, hunger, happiness, energy, is_alive, balance, last_activity = pet

    if hunger >= 100:
        bot.send_message(message.chat.id, f"🍔 {name} уже сыт! Не перекармливай.")
        return

    # Проверяем, можно ли бесплатно покормить
    can_use, msg = can_use_free_action(user_id, 'feed')
    if not can_use:
        bot.send_message(
            message.chat.id,
            f"{msg}\n\n🍖 Купи еду в магазине: /shop",
            parse_mode="Markdown"
        )
        return

    # Бесплатное кормление
    new_hunger = min(100, hunger + 20)
    new_happiness = min(100, happiness + 3)

    update_pet_stats(user_id, hunger=new_hunger, happiness=new_happiness)
    update_free_action_time(user_id, 'feed')

    bot.send_message(
        message.chat.id,
        f"🍖 **Бесплатное кормление!**\n\n"
        f"{name} покормлен!\n"
        f"🍖 Голод: {hunger} → {new_hunger}/100\n"
        f"😊 Счастье: {happiness} → {new_happiness}/100\n\n"
        f"⏳ Следующее бесплатное кормление через 6 часов.\n"
        f"🏪 Если срочно: /shop"
    )


@bot.message_handler(commands=['sleep'])
def sleep(message):
    """Бесплатный сон раз в 4 часа"""
    user_id = message.from_user.id
    pet = get_active_pet(user_id)

    if pet is None:
        bot.send_message(message.chat.id, "❌ У тебя нет активного питомца! Используй /start, чтобы завести.")
        return

    user_id, name, hunger, happiness, energy, is_alive, balance, last_activity = pet

    if energy >= 100:
        bot.send_message(message.chat.id, f"⚡ {name} уже полон энергии!")
        return

    # Проверяем, можно ли бесплатно поспать
    can_use, msg = can_use_free_action(user_id, 'sleep')
    if not can_use:
        bot.send_message(
            message.chat.id,
            f"{msg}\n\n💊 Скоро появится магазин лекарств!",
            parse_mode="Markdown"
        )
        return

    # Бесплатный сон
    new_energy = min(100, energy + 25)

    update_pet_stats(user_id, energy=new_energy)
    update_free_action_time(user_id, 'sleep')

    bot.send_message(
        message.chat.id,
        f"😴 **Бесплатный сон!**\n\n"
        f"{name} поспал и восстановил силы!\n"
        f"⚡ Энергия: {energy} → {new_energy}/100\n\n"
        f"⏳ Следующий бесплатный сон через 4 часа."
    )


@bot.message_handler(commands=['play'])
def play(message):
    """Бесплатная игра раз в 3 часа"""
    user_id = message.from_user.id
    pet = get_active_pet(user_id)

    if pet is None:
        bot.send_message(message.chat.id, "❌ У тебя нет активного питомца! Используй /start, чтобы завести.")
        return

    user_id, name, hunger, happiness, energy, is_alive, balance, last_activity = pet

    if happiness >= 100:
        bot.send_message(message.chat.id, f"😊 {name} уже счастлив!")
        return

    if energy < 20:
        bot.send_message(message.chat.id, f"😴 {name} устал! Отправь спать /sleep")
        return

    # Проверяем, можно ли бесплатно поиграть
    can_use, msg = can_use_free_action(user_id, 'play')
    if not can_use:
        bot.send_message(
            message.chat.id,
            f"{msg}\n\n🧸 Скоро появятся игрушки в магазине!",
            parse_mode="Markdown"
        )
        return

    # Бесплатная игра
    new_happiness = min(100, happiness + 20)
    new_energy = max(0, energy - 10)

    update_pet_stats(user_id, happiness=new_happiness, energy=new_energy)
    update_free_action_time(user_id, 'play')

    bot.send_message(
        message.chat.id,
        f"🎮 **Бесплатная игра!**\n\n"
        f"Ты поиграл с {name}!\n"
        f"😊 Счастье: {happiness} → {new_happiness}/100\n"
        f"⚡ Энергия: {energy} → {new_energy}/100\n\n"
        f"⏳ Следующая бесплатная игра через 3 часа."
    )


# ============ МАГАЗИН ============

@bot.message_handler(commands=['shop'])
def shop(message):
    markup = InlineKeyboardMarkup(row_width=3)
    food = [
        "Яблоко", "Банан", "Морковка",
        "Куриная ножка", "Рыбка", "Стейк",
        "Пицца", "Суши", "Мороженое",
        "Печенье", "Сок", "Молоко",
        "Домашний обед", "Гурме-набор"
    ]
    buttons = []
    for i in food:
        buttons.append(InlineKeyboardButton(i, callback_data=f"buy_{i.lower()}"))
    markup.add(*buttons)

    text = (
        "🍖 **Еда в магазине**\n\n"
        "🍎 Яблоко — 10 монет\n"
        "Сочное, хрустящее, полное витаминов.\n"
        "+15 голода\n\n"

        "🍌 Банан — 12 монет\n"
        "Энергия в кожуре! Быстро утоляет голод.\n"
        "+18 голода\n\n"

        "🥕 Морковка — 8 монет\n"
        "Хрустим на здоровье! Источник витамина А.\n"
        "+12 голода\n\n"

        "🍗 Куриная ножка — 20 монет\n"
        "Запечённая курочка — классика вкуса.\n"
        "+25 голода\n\n"

        "🐟 Рыбка — 22 монет\n"
        "Свежая рыба из чистого озера.\n"
        "+28 голода\n\n"

        "🥩 Стейк — 30 монет\n"
        "Сочный стейк на гриле. Настоящий пир!\n"
        "+35 голода, +5 счастья\n\n"

        "🍕 Пицца — 25 монет\n"
        "Как из итальянской печи! С соусом и сыром.\n"
        "+30 голода, +5 счастья\n\n"

        "🍣 Суши — 35 монет\n"
        "Вкус Японии! Рис, рыба и васаби.\n"
        "+30 голода, +15 счастья\n\n"

        "🍦 Мороженое — 15 монет\n"
        "Холодное лакомство для настроения.\n"
        "+12 голода, +15 счастья\n\n"

        "🍪 Печенье — 10 монет\n"
        "Домашнее печенье к чаю.\n"
        "+8 голода, +10 счастья\n\n"

        "🧃 Сок — 12 монет\n"
        "Фруктовая свежесть в каждом глотке.\n"
        "+15 голода, +8 счастья\n\n"

        "🥛 Молоко — 10 монет\n"
        "Стакан тёплого молока — как в детстве.\n"
        "+15 голода, +5 счастья\n\n"

        "🧺 Домашний обед — 50 монет\n"
        "Полноценный обед из трёх блюд!\n"
        "+50 голода, +15 счастья\n\n"

        "🎁 Гурме-набор — 80 монет\n"
        "Элитное угощение для взыскательных!\n"
        "+70 голода, +30 счастья\n\n"

        "📝 Нажми на кнопку, чтобы купить:"
    )

    bot.send_message(
        message.chat.id,
        text,
        parse_mode="Markdown",
        reply_markup=markup
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith('buy_'))
def handle_buy(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id

    item_name = call.data.replace('buy_', '')

    items = {
        "яблоко": {"price": 10, "hunger": 15, "happiness": 0},
        "банан": {"price": 12, "hunger": 18, "happiness": 0},
        "морковка": {"price": 8, "hunger": 12, "happiness": 0},
        "куриная ножка": {"price": 20, "hunger": 25, "happiness": 0},
        "рыбка": {"price": 22, "hunger": 28, "happiness": 0},
        "стейк": {"price": 30, "hunger": 35, "happiness": 5},
        "пицца": {"price": 25, "hunger": 30, "happiness": 5},
        "суши": {"price": 35, "hunger": 30, "happiness": 15},
        "мороженое": {"price": 15, "hunger": 12, "happiness": 15},
        "печенье": {"price": 10, "hunger": 8, "happiness": 10},
        "сок": {"price": 12, "hunger": 15, "happiness": 8},
        "молоко": {"price": 10, "hunger": 15, "happiness": 5},
        "домашний обед": {"price": 50, "hunger": 50, "happiness": 15},
        "гурме-набор": {"price": 80, "hunger": 70, "happiness": 30}
    }

    if item_name not in items:
        bot.answer_callback_query(call.id, "❌ Товар не найден!", show_alert=True)
        return

    item = items[item_name]

    # Критический участок: проверка и списание защищены LOCK
    with DB_LOCK:
        pet = get_active_pet(user_id)
        if pet is None:
            bot.answer_callback_query(call.id, "❌ У тебя нет питомца!", show_alert=True)
            return

        user_id, name, hunger, happiness, energy, is_alive, balance, last_activity = pet

        if balance < item["price"]:
            bot.answer_callback_query(
                call.id,
                f"❌ Недостаточно монет! Нужно: {item['price']}, у тебя: {balance}",
                show_alert=True
            )
            return

        # Списываем баланс внутри лока
        new_balance = balance - item["price"]

        new_hunger = min(100, hunger + item["hunger"])
        new_happiness = happiness
        if item["happiness"] > 0:
            new_happiness = min(100, happiness + item["happiness"])

        # Апдейтим БД напрямую, чтобы избежать гонки потоков
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
                       UPDATE pets
                       SET balance       = ?,
                           hunger        = ?,
                           happiness     = ?,
                           last_activity = ?
                       WHERE user_id = ?
                         AND is_alive = 1
                       ''', (new_balance, new_hunger, new_happiness, datetime.now().isoformat(), user_id))
        conn.commit()
        conn.close()

    emojis = {
        "яблоко": "🍎", "банан": "🍌", "морковка": "🥕", "куриная ножка": "🍗",
        "рыбка": "🐟", "стейк": "🥩", "пицца": "🍕", "суши": "🍣",
        "мороженое": "🍦", "печенье": "🍪", "сок": "🧃", "молоко": "🥛",
        "домашний обед": "🧺", "гурме-набор": "🎁"
    }
    emoji = emojis.get(item_name, "🍖")

    text = (
        f"✅ **Покупка успешна!**\n\n"
        f"{emoji} Ты купил **{item_name.title()}** за {item['price']} монет!\n"
        f"🍖 Голод: {hunger} → {new_hunger}/100\n"
    )
    if item["happiness"] > 0:
        text += f"😊 Счастье: {happiness} → {new_happiness}/100\n"
    text += f"\n💰 Новый баланс: **{new_balance}** монет"

    bot.edit_message_text(
        text,
        chat_id=chat_id,
        message_id=call.message.message_id,
        parse_mode="Markdown"
    )
    bot.answer_callback_query(call.id, f"✅ Куплено {item_name}!")


# ============ ИГРА "УГАДАЙ ЧИСЛО" ============

active_games = {}


@bot.message_handler(commands=['guess'])
def start_guess_game(message):
    user_id = message.from_user.id
    pet = get_active_pet(user_id)

    if pet is None:
        bot.send_message(message.chat.id, "❌ У тебя нет питомца! Используй /start")
        return

    if user_id in active_games:
        bot.send_message(message.chat.id, "⚠️ Ты уже играешь! Заверши игру или угадай число.")
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

    if user_id not in active_games:
        bot.answer_callback_query(call.id, "❌ Игра не найдена! Начни новую /guess", show_alert=True)
        return

    secret = active_games[user_id]

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

    guess = int(call.data.split('_')[1])

    if guess == secret:
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
        hint = "больше" if guess < secret else "меньше"
        new_balance = change_balance(user_id, -5)

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
                 f"💰 Новый баланс: **{new_balance}** монет\n\n"
                 f"Попробуй снова! Выбери число:",
            parse_mode="Markdown",
            reply_markup=markup
        )
        bot.answer_callback_query(call.id, f"❌ Не угадал! -5 монет. Попробуй ещё!")



#=========== == ========== === = = === = = = == = =  ==  = === =
active_games = {}
active_rps_games = {}  # Хранилище для игры камень-ножницы-бумага


@bot.message_handler(commands=['rps'])
def start_rps_game(message):
    user_id = message.from_user.id
    pet = get_active_pet(user_id)

    if pet is None:
        bot.send_message(message.chat.id, "❌ У тебя нет питомца! Используй /start")
        return

    # Проверяем баланс перед игрой (нужно хотя бы 5 монет)
    user_id, name, hunger, happiness, energy, is_alive, balance, last_activity = pet
    if balance < 5:
        bot.send_message(message.chat.id, "❌ У тебя слишком мало монет, чтобы играть (минимум 5)!")
        return

    # Регистрируем сессию игры для пользователя
    active_rps_games[user_id] = True

    markup = InlineKeyboardMarkup(row_width=3)
    btn_rock = InlineKeyboardButton("🪨 Камень", callback_data="rps_rock")
    btn_scissors = InlineKeyboardButton("✂️ Ножницы", callback_data="rps_scissors")
    btn_paper = InlineKeyboardButton("📄 Бумага", callback_data="rps_paper")
    btn_giveup = InlineKeyboardButton("🏳️ Сдаться", callback_data="rps_giveup")

    markup.add(btn_rock, btn_scissors, btn_paper)
    markup.add(btn_giveup)

    bot.send_message(
        message.chat.id,
        f"✊✌️✋ **Камень, Ножницы, Бумага!**\n\n"
        f"Сыграй против ИИ бота!\n"
        f"💰 Награда за победу: **15 монет**\n"
        f"📉 Проигрыш: -5 монет\n"
        f"🤝 Ничья: 0 монет\n\n"
        f"Сделай свой ход:",
        parse_mode="Markdown",
        reply_markup=markup
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith('rps_'))
def handle_rps(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id

    if user_id not in active_rps_games:
        bot.answer_callback_query(call.id, "❌ Игра не найдена! Начни новую /rps", show_alert=True)
        return

    if call.data == "rps_giveup":
        del active_rps_games[user_id]
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text="🏳️ Ты отказался от игры!\n\nИспользуй /rps чтобы начать заново.",
            parse_mode="Markdown"
        )
        bot.answer_callback_query(call.id, "Ты сдался!")
        return

    # Ход игрока
    player_choice = call.data.split('_')[1]

    # Ход ИИ бота
    choices = ["rock", "scissors", "paper"]
    bot_choice = random.choice(choices)

    ru_names = {"rock": "🪨 Камень", "scissors": "✂️ Ножницы", "paper": "📄 Бумага"}

    # Проверяем баланс перед расчетом (на случай, если списали в другом месте)
    pet = get_active_pet(user_id)
    if pet is None:
        del active_rps_games[user_id]
        bot.answer_callback_query(call.id, "❌ Питомец не найден!")
        return

    # Логика игры
    # Ничья
    if player_choice == bot_choice:
        result_text = "🤝 **Ничья!** никто ничего не потерял."
        alert_msg = "🤝 Ничья!"
        new_balance = pet[6]  # баланс не меняется
    # Победа игрока
    elif (player_choice == "rock" and bot_choice == "scissors") or \
            (player_choice == "scissors" and bot_choice == "paper") or \
            (player_choice == "paper" and bot_choice == "rock"):
        new_balance = change_balance(user_id, 15)
        result_text = f"🎉 **ПОЗДРАВЛЯЮ! Ты победил противника!**\n💰 Ты получил **15 монет**!"
        alert_msg = "🎉 Победа! +15 монет!"
    # Проигрыш игрока
    else:
        new_balance = change_balance(user_id, -5)
        result_text = f"❌ **Увы, ты проиграл!** Бот оказался хитрее.\n💰 Списано **5 монет**."
        alert_msg = "❌ Проигрыш! -5 монет."

    # Закрываем сессию игры
    del active_rps_games[user_id]

    bot.edit_message_text(
        chat_id=chat_id,
        message_id=call.message.message_id,
        text=f"✊✌️✋ **Результаты игры:**\n\n"
             f"🧑 Твой ход: **{ru_names[player_choice]}**\n"
             f"🤖 Ход соперника: **{ru_names[bot_choice]}**\n\n"
             f"{result_text}\n"
             f"💰 Новый баланс: **{new_balance}** монет\n\n"
             f"Хочешь реванш? Жми /rps",
        parse_mode="Markdown"
    )
    bot.answer_callback_query(call.id, alert_msg)


@bot.message_handler(commands=['guess_stats'])
def guess_stats(message):
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
        f"🍖 Голод: {hunger}/100\n"
        f"😊 Счастье: {happiness}/100\n"
        f"⚡ Энергия: {energy}/100\n\n"
        f"📝 Чтобы сыграть, используй /guess",
        parse_mode="Markdown"
    )

#==================================================================

@bot.message_handler(commands=['bonus'])
def daily_bonus_menu(message):
    user_id = message.from_user.id
    chat_id = message.chat.id

    pet = get_active_pet(user_id)
    if pet is None:
        bot.send_message(chat_id, "❌ У тебя нет питомца! Используй /start")
        return

    # Запрашиваем актуальные данные бонусов из БД под замком
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
        # Игрок вообще ни разу не брал бонус
        can_claim = True
    else:
        last_bonus_time = datetime.fromisoformat(last_bonus_str)
        # Считаем разницу в днях на основе чистых дат (без учета часов, чтобы сброс был в полночь или ровно через 24ч)
        # В данном случае делаем честные 24 часа для кнопки "Забрать":
        time_passed = now - last_bonus_time

        if time_passed >= timedelta(hours=24):
            can_claim = True
            # Проверка на пропуск дней: если прошло больше 48 часов, стрик сгорает
            if time_passed >= timedelta(hours=48):
                streak = 0
        else:
            can_claim = False
            # Вычисляем сколько осталось спать таймеру (ЧЧ:ММ:СС)
            time_left = timedelta(hours=24) - time_passed
            hours, remainder = divmod(int(time_left.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            time_left_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    # Расчет будущей награды (прогресс)
    display_streak = streak if not can_claim else (streak + 1)
    next_milestone = ((display_streak - 1) // 10 + 1) * 10
    days_in_current_ten = display_streak % 10 if display_streak % 10 != 0 else 10

    # Сумма награды по формуле
    reward = 50 if display_streak % 10 == 0 else 10

    # Визуальный индикатор прогресса (ProgressBar)
    progress_bar = "🟩" * days_in_current_ten + "⬜" * (10 - days_in_current_ten)

    # Интерфейс (UI)
    text = (
        f"🎁 **ЕЖЕДНЕВНЫЙ БОНУС** 🎁\n\n"
        f"🔥 Текущий стрик: **{display_streak} дн.**\n"
        f"📊 До супер-награды (50 💰): **{10 - days_in_current_ten} дн.**\n"
        f"🗺️ Прогресс текущего цикла:\n"
        f"|{progress_bar}| {days_in_current_ten}/10\n\n"
        f"💰 Сегодняшняя награда: **{reward} монет**\n"
    )

    markup = InlineKeyboardMarkup()
    if can_claim:
        btn_claim = InlineKeyboardButton("🎁 Забрать бонус!", callback_data="claim_daily_bonus")
        markup.add(btn_claim)
    else:
        text += f"\n⏳ Следующий бонус будет доступен через: `{time_left_str}`"
        btn_disabled = InlineKeyboardButton("🔒 Уже получено", callback_data="bonus_disabled")
        markup.add(btn_disabled)

    bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=markup)

    @bot.callback_query_handler(func=lambda call: call.data == 'claim_daily_bonus')
    def handle_claim_bonus(call):
        user_id = call.from_user.id
        chat_id = call.message.chat.id
        now = datetime.now()

        with DB_LOCK:
            # 1. Защита: проверяем статус заново внутри лока
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

            # Проверяем тайм-лимит еще раз
            if last_bonus_str is not None:
                last_bonus_time = datetime.fromisoformat(last_bonus_str)
                if now - last_bonus_time < timedelta(hours=24):
                    bot.answer_callback_query(call.id, "⏳ Секундочку! Бонус еще не остыл.", show_alert=True)
                    conn.close()
                    return

                # Проверка на сброс стрика (если пропустил больше суток поверх нормы)
                if now - last_bonus_time >= timedelta(hours=48):
                    streak = 0

            # 2. Логика начисления: увеличиваем стрик
            new_streak = streak + 1

            # Рассчитываем сумму по формуле
            reward = 50 if new_streak % 10 == 0 else 10
            new_balance = balance + reward

            # 3. Апдейтим БД
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

        # 5. Дополнительно (визуальный фидбек / анимация текстом)
        congratulations = (
            f"✨ **БАМ! АНИМАЦИЯ НАЧИСЛЕНИЯ** ✨\n"
            f"🪙    ✨    🪙    ✨    🪙\n"
            f"    🪙    **+{reward} 💰**    🪙\n"
            f"🪙    ✨    🪙    ✨    🪙\n\n"
            f"🎉 Ты успешно забрал ежедневный бонус!\n"
            f"🔥 Твой стрик вырос до **{new_streak}** дней подряд.\n"
            f"💰 Баланс пополнен: **{new_balance}** монет."
        )

        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=congratulations,
            parse_mode="Markdown"
        )

        bot.answer_callback_query(call.id, f"➕ Начислено {reward} монет! Стрик: {new_streak} дней.", show_alert=False)

    @bot.callback_query_handler(func=lambda call: call.data == 'bonus_disabled')
    def handle_disabled_bonus_click(call):
        bot.answer_callback_query(call.id, "⏳ Кнопка заблокирована! Загляни сюда позже.",
                                  show_alert=True)


if __name__ == "__main__":
    print("🤖 Бот-тамагочи запущен!")
    print("🎮 Игра 'Угадай число' и 'КНБ' активны!")
    print("🔄 Голод -5, Счастье -3, Энергия -2 каждые 10 минут!")
    print("📊 Доступные команды: /start, /status, /feed, /sleep, /play, /guess, /rps, /guess_stats, /shop")
    bot.infinity_polling()
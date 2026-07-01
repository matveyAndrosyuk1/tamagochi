import telebot

TOKEN = "8912217606:AAFAxQKalVqR1RoDNB0zNP41LfZHJn0XXzU"

# Создаём экземпляр бота
bot = telebot.TeleBot(TOKEN)

# Обработчик команды /start
@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id, "Привет! Я бот.")

# Запуск бота
if __name__ == "__main__":
    print("Бот запущен...")
    bot.infinity_polling()
from telebot import TeleBot, types
import os
import re

TOKEN = os.environ.get('BOT_TOKEN')
bot = TeleBot(TOKEN)
TARGET = "Бот по стокам : @growagarden2bot"

def handler(request):
    if request.method == "POST":
        json_data = request.get_json()
        update = types.Update.de_json(json_data)
        bot.process_new_updates([update])
        return 'ok', 200
    return 'Bot is running', 200

import logging
from aiogram import Bot
from config import TOKEN

# Настройка логгера
logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
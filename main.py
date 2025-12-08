import asyncio
from aiogram import Dispatcher
from bot import bot
from bot.handlers import base, link_parse, rag_query, quiz


# --- ЗАПУСК ---
async def main():
    dp = Dispatcher()

    dp.include_router(base.router)
    dp.include_router(link_parse.router)
    dp.include_router(quiz.router)
    dp.include_router(rag_query.router)

    print("🚀 Бот запущен (База данных: ./rag_db)")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен")
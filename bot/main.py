import logging 
import asyncio
from telegram import Update 
from telegram.ext import Application, CommandHandler
from bot.onboarding import onboarding_handler
from bot.handlers.search import search_command, help_command
from bot.handlers.agent_control import score_command, tailor_command
from bot.worker import start_worker
from dotenv import load_dotenv
import os

load_dotenv()


logging.basicConfig(level = logging.INFO, format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def main():
    
    #Initialize the bot with your token
    application = Application.builder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    
    #Register handlers
    application.add_handler(onboarding_handler)
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("score", score_command))
    application.add_handler(CommandHandler("tailor", tailor_command))
    
    logger.info("[BOT] Starting background worker and polling....")
    
    #Start the worker task alongside the bot
    loop = asyncio.get_event_loop()
    loop.create_task(start_worker())
    
    #Run the bot until CTRL+C is pressed
    application.run_polling(allowed_updates=Update.ALL_TYPES)
    

if __name__ == "__main__":
    main()    
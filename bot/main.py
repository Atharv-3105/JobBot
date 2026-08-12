import logging 
import asyncio
import signal 
from telegram import Update 
from telegram.ext import Application, CommandHandler
from bot.onboarding import onboarding_handler
from bot.handlers.search import search_command, help_command
from bot.handlers.agent_control import score_command, tailor_command
from bot.handlers.stats import stats_command
from bot.worker import start_worker, job_queue
from dotenv import load_dotenv
import os

load_dotenv()


logging.basicConfig(level = logging.INFO, format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


worker_tasks = []

async def post_shutdown(application: Application):
    """ 
        Function for cleanup after bot shuts down
    """
    global worker_tasks
    logger.info("[BOT] Shutdown signal received. Draining queue...")
    
    #Signal worker to stop accepting new tasks
    job_queue.signal_shutdown()
    
    #Wait for pending tasks to complete
    drained = await job_queue.wait_for_drain(timeout = 30.0)
    
    if drained:
        logger.info("[BOT] All queue tasks completed successfully")
    else:
        logger.warning("[BOT] Shutdown timeout - some tasks may not have completed")
    
    #Cancel worker tasks
    for task in worker_tasks:
        task.cancel()
        
    #Wait for workers to finish
    await asyncio.gather(*worker_tasks, return_exceptions = True)
    logger.info("[BOT] All workers stopped. Goodbye!")
    
    
async def post_init(application: Application):
    """ 
        Start background workers after Python-telegram bot initializes
        We are doing this because PTB already intercepts SIGINT,SIGTERM events to shutdown it'e event loop
        Inorder to stop race conditions between PTB shutting down it's internal event loop and our own graceful shutdown drainning logic
    """
    global worker_tasks
    NUM_WORKERS = 2
    logger.info("[BOT] Starting Background workers...")
    for i in range(NUM_WORKERS):
        task = asyncio.create_task(start_worker(worker_id= i +1))
        worker_tasks.append(task)

def main():
    
    #Initialize the bot with your token
    application = Application.builder().token(os.getenv("TELEGRAM_BOT_TOKEN")).post_init(post_init).post_stop(post_shutdown).build()
    
    #Register handlers
    application.add_handler(onboarding_handler)
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("score", score_command))
    application.add_handler(CommandHandler("tailor", tailor_command))
    application.add_handler(CommandHandler("stats", stats_command))
    
    logger.info("[BOT] Starting polling....")
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)
   
    

if __name__ == "__main__":
    main()    
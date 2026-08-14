import logging 
from telegram import Update 
from telegram.ext import ContextTypes
from bot.worker import job_queue

logger = logging.getLogger(__name__)

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ 
        /cancel - Cancel all running/queued tasks for the current user
    """
    user_id = update.effective_user.id 
    chat_id = update.effective_chat.id 
    
    active_count = job_queue.get_user_active_count(user_id)
    
    if active_count == 0:
        await update.message.reply_text("You have no active tasks to cancel.")
        return 
    
    cancelled = await job_queue.cancel_user_tasks(user_id)
    
    await update.message.reply_text(
        f"🛑 **Cancelled {cancelled} task(s).**\n\n"
        f"Any in-progress work has been stopped, and queued tasks have been removed.",
        parse_mode='Markdown'
    )
    
    logger.info(f"[CANCEL] User {user_id} cancelled their {cancelled} tasks")
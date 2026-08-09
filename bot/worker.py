import asyncio 
import logging
from dataclasses import dataclass
from typing import Any 
from telegram import Bot
from agent.orchestrator import pipeline 


logger = logging.getLogger(__name__)

#we define a Dataclass which holds the details about our bot's state
@dataclass 
class BotTask:
    chat_id: str 
    user_id: str 
    task_type: str 
    initial_state: dict 
    bot: Bot 
    
#We use an Aysncio Queue as our WorkerQueue
job_queue = asyncio.Queue()

async def start_worker():
    """ 
        Function which starts the background workers loop
    """
    
    logger.info("[QUEUE] Background worker started.")
    while True:
        task: BotTask = await job_queue.get()
        try:
            logger.info(f"[QUEUE] Processing {task.task_type} task for user {task.user_id}")
            final_state = await pipeline.ainvoke(task.initial_state)
            await _send_result(task, final_state)
        
        except Exception as e:
            error_msg = str(e).lower()
            
            #If rate-limit is completely exhausted, re-queue with a delay
            if "rate-limit" in error_msg or "exhausted" in error_msg:
                logger.warning(f"[QUEUE] Rate-Limit exhausted for User {task.user_id}. Re-queuing for later...")
                
                #We wait for 1 minute before retrying 
                await asyncio.sleep(60)
                await job_queue.put(task)
                
            else:
                logger.error(f"[QUEUE] Task failed for user {task.user_id}: {e}")
                await task.bot.send_message(chat_id = task.chat_id, text = f"An unexpected error occured while processing your request. \n\nDetails: {str(e)}")
        
        finally:
            job_queue.task_done()
            
async def _send_result(task: BotTask, final_state: dict):
    """ 
        Function which send's the final report and PDFs to the user
    """
    await task.bot.send_message(chat_id = task.chat_id, text = final_state["final_report"], parse_mode='Markdown')
    
    if final_state.get("tailored_jobs"):
        for job in final_state["tailored_jobs"]:
            pdf_path = job["pdf_path"]
            caption = f"**{job['title']}** at {job['company']} (Score: {job['score']})"
            with open(pdf_path, "rb") as pdf_file:
                await task.bot.send_document(chat_id = task.chat_id, document = pdf_file, filename = f"{[job['company']]}_resume.pdf",
                                             caption = caption, parse_mode='Markdown')
                
    else:
        await task.bot.send_message(chat_id=task.chat_id, text = "No jobs scored high enough (A/B) to tailor resumes for.")
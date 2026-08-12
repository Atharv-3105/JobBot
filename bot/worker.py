import asyncio 
import logging
import hashlib
import time
import os 
from dataclasses import dataclass, field 
from typing import Any , Dict
from telegram import Bot
from telegram.error import TelegramError
from agent.orchestrator import pipeline 


logger = logging.getLogger(__name__)

#we define a Dataclass which holds the details about our bot's state
@dataclass 
class BotTask:
    chat_id: int  
    user_id: int 
    task_type: str 
    initial_state: dict 
    bot: Bot
    dedup_key: str = field(default="")    #Unique identifier for deduplication
    created_at: float = field(default_factory=time.time)
    
    def __post_init__(self):
        """ 
            Function to generate unique hash of dedup_key after initialization
        """
        if not self.dedup_key:
            #For search: Create hash from user_id + task_type + keyword 
            #For score/tailor: Create hash from user_id + task_type + job_url
            job_url = self.initial_state.get('raw_jobs', [{}])[0].get('url', '')
            hash_input = f"{self.user_id}:{self.task_type}:{job_url or self.initial_state.get('keyword', '')}"
            self.dedup_key = hashlib.md5(hash_input.encode()).hexdigest()
    
    
class TaskQueue:
    """ 
        We define our own task-queue with deduplication support
    """
    def __init__(self, maxsize: int = 20):
        self._queue = asyncio.Queue(maxsize=maxsize)
        self._active_tasks: Dict[str, float] = {}       #It will have task_id --> timestamp
        self._lock = asyncio.Lock()
        self._shutdown_event = asyncio.Event()
        self._maxsize = maxsize
        
    
    async def put(self, task: BotTask) -> bool:
        """ 
            Function which ADDS tasks to queue if not duplicate
            Returns True if added, False if duplicate
        """
        async with self._lock:
            #Remove Stale task cleanup (remove tasks which are older than 10 minutes from the Queue)
            now = time.time()
            stale_tasks = [tid for tid, ts in self._active_tasks.items() if now - ts > 600]
            for tid in stale_tasks:
                logger.warning(f"[QUEUE] Removing stale task: {tid}")
                del self._active_tasks[tid]
            
            
            if task.dedup_key in self._active_tasks:
                logger.info(f"[QUEUE] Duplicate task detected:(dedup_key: {task.dedup_key})")
                return False 
            
            self._active_tasks[task.dedup_key] = time.time()
            await self._queue.put(task)
            
            logger.info(f"[QUEUE] Task {task.dedup_key} Added | Queue size: {self._queue.qsize()}/{self._maxsize} | Active: {len(self._active_tasks)}")
            return True 
        
    async def get(self) -> BotTask:
        """ 
            Function which GETs next task from queue
        """
        return await self._queue.get()
    
    async def task_done(self, dedup_key: str):
        """ 
            Mark task as completed and remove from active set
        """
        self._active_tasks.pop(dedup_key, None)
        self._queue.task_done()
    
    def signal_shutdown(self):
        """  
            Signal worker for SHUTDOWN
        """
        return self._shutdown_event.set()
    
    def is_shutdown(self) -> bool:
        """ 
            Check if shutdown was signaled
        """
        return self._shutdown_event.is_set()
    
    async def wait_for_drain(self, timeout: float = 30.0) -> bool:
        """ 
            Wait for all tasks to complete with timeout
            Returns True if drained, False if timeout
        """
        try:
            await asyncio.wait_for(self._queue.join(), timeout = timeout)
            return True 
        except asyncio.TimeoutError:
            logger.warning(f"[QUEUE] Shutdown timeout: {self._queue.qsize()} tasks still pending")
            return False     
 
#We use our TaskQueue as our WorkerQueue
job_queue = TaskQueue(maxsize = 20)

async def start_worker(worker_id: int):
    """ 
        Function which starts the background workers loop with graceful shutdown
    """
    
    logger.info(f"[QUEUE] Worker_{worker_id} Started.")
    
    while not job_queue.is_shutdown():
        try:
            #Wait for the task with timeout to check shutdown flag periodically
            try:
                task = await asyncio.wait_for(job_queue.get(), timeout = 1.0)
            except asyncio.TimeoutError:
                continue 
            
            try:
                logger.info(f"[QUEUE] Worker{worker_id} | Processing {task.task_type} task | User {task.user_id} | Key: {task.dedup_key}")
                #Process the task 
                final_state = await pipeline.ainvoke(task.initial_state)
                
                await _send_result(task, final_state)
            
            except asyncio.CancelledError:
                #Handles CancelledError cleanly without getting race conditions
                logger.info(f"[QUEUE] Worker_{worker_id} task cancelled during shutdown.")
                raise
            
            except Exception as e:
                logger.error(f"[QUEUE] Worker_{worker_id} Task failed for user {task.user_id}: {e}")
                
                #Notify the User of the failure
                try:
                    await task.bot.send_message(chat_id = task.chat_id, text = f"⚠️ Processing failed: {str(e)[:100]}\n\nPlease try again in a minute.")
                except TelegramError as notify_err:
                    logger.error(f"[QUEUE] Failed to notify user: {notify_err}")
            
            finally:
                #Always mark task as done to prevent queue deadlockkkkk
                job_queue.task_done(task.dedup_key)
                
        except asyncio.CancelledError:
            #Clean worker exit on shutdown
            logger.info(f"[QUEUE] Worker_{worker_id} shutting down gracefully")
            break 

        except Exception as e:
            logger.error(f"[QUEUE] Worker_{worker_id} encountered unexpected error: {e}")
            await asyncio.sleep(1.0) #Prevent tight looping on unexpected errors
    
    logger.info(f"[QUEUE] Worker_{worker_id} shutting down gracefully")
    
    
            
async def _send_result(task: BotTask, final_state: dict):
    """ 
        Function which send's the final report and PDFs to the user
    """
    try:
        await task.bot.send_message(
            chat_id = task.chat_id, text = final_state["final_report"],
            parse_mode = 'Markdown'
        )
        
    except TelegramError as e:
        logger.error(f"[QUEUE] failed to send final report to user {task.user_id}: {e}")
        return #Abort PDF sending if the user blocked the bot or chat is unavailable
    
    if final_state.get("tailored_jobs"):
        for job in final_state["tailored_jobs"]:
            pdf_path = job["pdf_path"]
            caption = f"**{job['title']}** at {job['company']} (Score: {job['score']})"
            
            #File existence check 
            if not os.path.exists(pdf_path):
                logger.error(f"[QUEUE] PDF file not found at {pdf_path} for user {task.user_id}")
                await task.bot.send_message(chat_id=task.chat_id, text = f" ⚠️ Error: The tailored PDF for {job['company']} could not be generated.")
                continue
            
            try:
                with open(pdf_path, "rb") as pdf_file:
                    await task.bot.send_message(chat_id = task.chat_id, document = pdf_file, filename = f"{job['company']}_resume.pdf", caption = caption, parse_mode = "Markdown")
            
            except TelegramError as e:
                logger.error(f"[QUEUE] Failed to send PDF {pdf_path} to user {task.user_id} : {e}")
            
            except Exception as e:
                logger.error(f"[QUEUE] Unexpected error sending PDF {pdf_path} : {e}")
                
    else:
        await task.bot.send_message(chat_id = task.chat_id, text = "No jobs scored high enough (A/B) to tailor resumes for.")
                
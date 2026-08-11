import asyncio 
import logging
import hashlib
from dataclasses import dataclass, field 
from typing import Any , Set
from telegram import Bot
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
    task_id: str = field(default="")    #Unique identifier for deduplication
    
    def __post_init__(self):
        """ 
            Function to generate unique hash of task_id after initialization
        """
        if not self.task_id:
            #Create hash from user_id + task_type + keyword
            hash_input = f"{self.user_id}:{self.task_type}:{self.initial_state.get('keyword', '')}"
            self.task_id = hashlib.md5(hash_input.encode()).hexdigest()
    
    
class TaskQueue:
    """ 
        We define our own task-queue with deduplication support
    """
    def __init__(self):
        self._queue = asyncio.Queue()
        self._active_tasks: Set[str] = set()        #It will have tasks currently running or processing
        self._lock = asyncio.Lock()
        self._shutdown_event = asyncio.Event()
        
    
    async def put(self, task: BotTask) -> bool:
        """ 
            Function which ADDS tasks to queue if not duplicate
            Returns True if added, False if duplicate
        """
        async with self._lock:
            if task.task_id in self._active_tasks:
                logger.info(f"[QUEUE] Duplicate task detected: {task.task_id}")
                return False 
            
            self._active_tasks.add(task.task_id)
            await self._queue.put(task)
            logger.info(f"[QUEUE] Task {task.task_id} added to queue")
            return True 
        
    async def get(self) -> BotTask:
        """ 
            Function which GETs next task from queue
        """
        return await self._queue.get()
    
    async def task_done(self, task_id: str):
        """ 
            Mark task as completed and remove from active set
        """
        self._active_tasks.discard(task_id)
        self._queue.task_done()
        
    def is_duplicate(self, task_id: str) -> bool:
        """ 
            Check if task is already active
        """
        return task_id in self._active_tasks
    
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
    
    async def wait_for_drain(self, timeout: float = 30.0):
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
        
    def get_queue_size(self)-> int:
        """ 
            Get number of pending tasks
        """
        return self._queue.qsize()
    
    def get_active_count(self)-> int:
        """ 
            Get number of active tasks (queued + processing)
        """
        return len(self._active_tasks)
    
 
#We use our TaskQueue as our WorkerQueue
job_queue = TaskQueue()

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
                logger.info(f"[QUEUE] Worker{worker_id} | Processing {task.task_type} task | User {task.user_id}")
                #Process the task 
                final_state = await pipeline.ainvoke(task.initial_state)
                
                await _send_result(task, final_state)
            
            except Exception as e:
                logger.error(f"[QUEUE] Worker_{worker_id} Task failed for user {task.user_id}: {e}")
                
                #Notify the User of the failure
                try:
                    await task.bot.send_message(chat_id = task.chat_id, text = f"⚠️ Processing failed: {str(e)[:100]}\n\nPlease try again in a minute.")
                except Exception as notify_err:
                    logger.error(f"[QUEUE] Failed to notify user: {notify_err}")
            
            finally:
                #Always mark task as done
                job_queue.task_done(task.task_id)
                
        except Exception as e:
            logger.error(f"[QUEUE] Worker_{worker_id} encountered unexpected error: {e}")
            await asyncio.sleep(1.0)
    
    logger.info(f"[QUEUE] Worker_{worker_id} shutting down gracefully")
    
    
            
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
                await task.bot.send_document(chat_id = task.chat_id, document = pdf_file, filename = f"{job['company']}_resume.pdf",
                                             caption = caption, parse_mode='Markdown')
                
    else:
        await task.bot.send_message(chat_id=task.chat_id, text = "No jobs scored high enough (A/B) to tailor resumes for.")
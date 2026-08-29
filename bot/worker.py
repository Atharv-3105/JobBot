import asyncio 
import logging
import hashlib
import time
import os 
from dataclasses import dataclass, field 
from typing import Any , Dict, Set
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
    cancel_event: asyncio.Event = field(default_factory = asyncio.Event)
    
    def __post_init__(self):
        """ 
            Function to generate unique hash of dedup_key after initialization
        """
        if not self.dedup_key:
            #For search: Create hash from user_id + task_type + keyword 
            #For score/tailor: Create hash from user_id + task_type + job_url
            raw_jobs = self.initial_state.get("raw_jobs", [])
            url = raw_jobs[0].url if raw_jobs else self.initial_state.get("keyword", "unknown")
            hash_input = f"{self.user_id}:{self.task_type}:{url}"
            self.dedup_key = hashlib.md5(hash_input.encode()).hexdigest()
    
    
class TaskQueue:
    """ 
        Thread-Safe TaskQueue with:
        - Deduplication (by user+task_type+URL)
        - Per-user cancellation support
        - Maxsize bounds
        - Stale Task Cleanup (10 min TTL)
        - Queue metrics logging
    """
    
    def __init__(self, maxsize: int = 20):
        self._queue = asyncio.Queue(maxsize=maxsize)
        self._active_tasks: Dict[str, float] = {}       #It will have dedup_key --> timestamp
        self._user_active_tasks: Dict[int, Set[str]] = {}       #user_id -> set of dedup_keys
        self._running_tasks: Dict[str, BotTask] = {}            #dedup_key -> BotTask (for cancellation)
        self._lock = asyncio.Lock()
        self._shutdown_event = asyncio.Event()
        self._maxsize = maxsize
        
    
    async def put(self, task: BotTask) -> bool:
        """ 
            Function which ADDS tasks to queue
            Returns False if dulicate or queue full
        """
        async with self._lock:
            #Remove Stale task cleanup (remove tasks which are older than 10 minutes from the Queue)
            now = time.time()
            stale_tasks = [tid for tid, ts in self._active_tasks.items() if now - ts > 600]
            for tid in stale_tasks:
                logger.warning(f"[QUEUE] Removing stale task: {tid}")
                self._active_tasks.pop(tid, None)
                
                #Clean-up User Mapping
                for user_tasks in self._user_active_tasks.values():
                    user_tasks.discard(tid)
                    
    
            #Deduplication Check         
            if task.dedup_key in self._active_tasks:
                logger.info(f"[QUEUE] Duplicate task rejected:({task.dedup_key})")
                return False 
            
            #Size Check
            if self._queue.full():
                logger.error(f"[QUEUE] Queue full ({self._maxsize}). Rejecting task")
                return False
            
            self._active_tasks[task.dedup_key] = time.time()
            self._user_active_tasks.setdefault(task.user_id, set()).add(task.dedup_key)
            await self._queue.put(task)
            
            logger.info(
                f"[QUEUE] Task queued | user={task.user_id} | type={task.task_type} | "
                f"queue={self._queue.qsize()}/{self._maxsize} | active={len(self._active_tasks)}"
            )
            return True 
        
    async def get(self) -> BotTask:
        """ 
            Function which GETs next task from queue
        """
        return await self._queue.get()
    
    async def mark_processing(self, task:BotTask):
        """ 
            Function to mark task as currently being processed (for cancellation support)
        """
        self._running_tasks[task.dedup_key] = task 
    
    def task_done(self, dedup_key: str):
        """ 
            Mark task as completed and cleanup all tracking dicts.
        """
        self._active_tasks.pop(dedup_key, None)
        self._running_tasks.pop(dedup_key, None)
        
        #Find and remove the task from the User-Mapping
        for user_tasks in self._user_active_tasks.values():
            user_tasks.discard(dedup_key)
        
        self._queue.task_done()
        
    def is_duplicate(self, dedup_key: str) -> bool:
        """ 
            Function to check if a task with this dedup_key is already active
        """
        return dedup_key in self._active_tasks
    
    def has_user_active_task(self, user_id: int) -> bool:
        """ 
            Function to Check if a user has any active tasks.
        """
        return bool(self._user_active_tasks.get(user_id))
    
    def get_user_active_count(self, user_id: int) -> bool:
        """ 
            Function to Get Count of the active tasks for a user
        """
        return len(self._user_active_tasks.get(user_id, set()))
    
    async def cancel_user_tasks(self, user_id: int) -> int:
        """ 
            Cancel all acitve tasks for a user
            Returns the number of tasks cancelled.
        """ 
        
        cancelled = 0
        async with self._lock:
            user_keys = list(self._user_active_tasks.get(user_id, set()))
            for key in user_keys:
                #Signal cancellation via the task's event
                if key in self._running_tasks:
                    self._running_tasks[key].cancel_event.set()
                    cancelled += 1
                    logger.info(f"[QUEUE] Cancelled running task for user {user_id}")
                else:
                    #If the task is queued but not running we remove it from the Queue
                    #We mark the task as stale in the queue as removing from a Asynchronous Queue is complex and will be added in FUTURE
                    self._active_tasks.pop(key, None)
                    cancelled += 1
                    logger.info(f"[QUEUE] Removed queued task for user {user_id}: {key}")
                    
            
            self._user_active_tasks.pop(user_id, None)

        return cancelled
    
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
        
    
    def get_info(self, user_id: int) -> dict:
        """ 
            Returns queue position info for a specific user
        """  
        
        #Count number of tasks that are ahead of this user
        position = self._queue.qsize() + 1
        
        #We estimate near around 45 seconds per task
        estimated_seconds = position * 45
        
        return {
            "position": position,
            "estimated_seconds": estimated_seconds,
            "estimated_str": f"~{estimated_seconds}s" if estimated_seconds < 60 else f"~{estimated_seconds // 60}m {estimated_seconds}s",
            "active_workers": len(self._running_tasks)
        }
        
    
    @property
    def qsize(self) -> int:
        return self._queue.qsize()
    
    @property
    def active_count(self) -> int:
        return len(self._active_tasks)

 
#We use our TaskQueue as our WorkerQueue
job_queue = TaskQueue(maxsize = 20)

async def start_worker(worker_id: int):
    """ 
        Function which starts the background workers loop with graceful shutdown and cancellation support.
    """
    
    logger.info(f"[QUEUE] Worker_{worker_id} Started.")
    
    while not job_queue.is_shutdown():
        try:
            #Wait for the task with timeout to check shutdown flag periodically
            try:
                task = await asyncio.wait_for(job_queue.get(), timeout = 1.0)
            except asyncio.TimeoutError:
                continue 
            
            #Check if the task is already cancelled before starting
            if task.cancel_event.is_set():
                logger.info(f"[QUEUE] Worker_{worker_id} | Task {task.dedup_key} cancelled before start")
                job_queue.task_done(task.dedup_key)
                continue 
            
            
            try:
                #Mark the task as processing 
                await job_queue.mark_processing(task)
                logger.info(f"[QUEUE] Worker{worker_id} | Processing {task.task_type} task | User {task.user_id} | Key: {task.dedup_key}")
                
                #Process the task 
                final_state = await pipeline.ainvoke(task.initial_state)
                
                #Check if the task was cancelled during processing
                if task.cancel_event.is_set():
                    logger.info(f"[QUEUE] Task {task.dedup_key} cancelled after processing. Not sending results.")
                    await task.bot.send_message(chat_id = task.chat_id, text = "🛑 Your task was cancelled.", parse_mode='Markdown')
                    
                else:
                    await _send_result(task, final_state)
            
            except asyncio.CancelledError:
                #Handles CancelledError cleanly without getting race conditions
                logger.info(f"[QUEUE] Worker_{worker_id} task cancelled during shutdown.")
                raise
            
            except Exception as e:
                logger.error(f"[QUEUE] Worker_{worker_id} Task failed for user {task.user_id}: {e}")
                
                #Notify the User of the failure (no raw exception details - internals stay in the logs)
                try:
                    await task.bot.send_message(chat_id = task.chat_id, text = "⚠️ Something went wrong on my end while processing that. Please try again in a minute.", parse_mode = 'Markdown')
                except TelegramError as notify_err:
                    logger.error(f"[QUEUE] Failed to notify user {task.user_id}: {notify_err}")
            
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
                await task.bot.send_message(chat_id=task.chat_id, text = f"⚠️ The tailored PDF for {job['company']} couldn't be generated — sorry about that. Please try again.", parse_mode = 'Markdown')
                continue
            
            try:
                with open(pdf_path, "rb") as pdf_file:
                    await task.bot.send_document(chat_id = task.chat_id, document = pdf_file, filename = f"{job['company']}_resume.pdf", caption = caption)
            
            except TelegramError as e:
                logger.error(f"[QUEUE] Failed to send PDF {pdf_path} to user {task.user_id} : {e}")
            
            except Exception as e:
                logger.error(f"[QUEUE] Unexpected error sending PDF {pdf_path} : {e}")
                
    else:
        #tailored_jobs can be empty for two different reasons - don't conflate them.
        scored_ab = any(sj.score in ("A", "B") for sj in final_state.get("scored_jobs", []))
        if scored_ab:
            #A/B jobs existed, but tailoring itself didn't succeed (e.g. the LLM's
            #proposed changes failed a validation guard) - the final_report above
            #already explains this via Pipeline Stats, no separate message needed here.
            pass
        else:
            await task.bot.send_message(
                chat_id = task.chat_id,
                text = "No jobs scored high enough (A/B) to tailor a resume for this time. Try a broader keyword, or check `/stats` to see what's been found so far.",
                parse_mode = 'Markdown'
            )
                
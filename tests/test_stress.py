import pytest
import asyncio
import random
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import MagicMock, AsyncMock, patch
from bot.worker import TaskQueue, BotTask, start_worker
from db.models import Base, User, Job, JobStatus
from db.crud import save_user, save_job, get_user_stats

@pytest.mark.asyncio
async def test_concurrency_stress():
    """
    Stress Test: Simulate multiple users submitting search and scoring tasks concurrently.
    We spin up 3 workers and queue 15 tasks from different simulated users.
    We mock the pipeline calls to perform database reads and writes under concurrency
    to ensure no SQLite database locks or event loop deadlocks occur.
    """
    # 1. Setup a real shared in-memory DB or temporary file DB
    # Let's use a temporary sqlite file DB to simulate real file lock scenarios (which is more stressful than in-memory)
    import tempfile
    import os
    db_fd, db_path = tempfile.mkstemp()
    os.close(db_fd)
    
    db_url = f"sqlite:///{db_path}"
    engine = create_engine(db_url, connect_args={'check_same_thread': False})
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    # Mock get_db dependency injection to point to our test DB engine
    def mock_get_db_context():
        class Context:
            def __enter__(self):
                self.db = SessionLocal()
                return self.db
            def __exit__(self, exc_type, exc_val, exc_tb):
                self.db.close()
        return Context()

    # 2. Setup task queue and worker tasks
    tq = TaskQueue(maxsize=50)
    
    # We will patch the global job_queue in bot.worker with our test queue
    # and patch get_db to return our custom mock context
    
    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()
    
    # Track completed tasks
    completed_reports = []
    
    # Mock pipeline processing: it should write to user profile, insert a scored job, and sleep randomly
    async def mock_pipeline_invoke(state):
        user_id = state["user_id"]
        keyword = state["keyword"]
        
        # Simulate db reads and writes
        with mock_get_db_context() as db:
            # Register user if not exists
            save_user(db, user_id, f"user_{user_id}", ["Developer"], {"core": ["Python"]}, f"/tmp/user_{user_id}.tex")
            
            # Save a random job
            job = save_job(
                db, 
                user_id, 
                f"Role {keyword}", 
                f"Company {random.randint(1,100)}", 
                f"https://job_{random.randint(1,100000)}", 
                "stress_test"
            )
            
            # Read stats
            stats = get_user_stats(db, user_id)
            
        # Simulate variable network latency
        await asyncio.sleep(random.uniform(0.01, 0.1))
        
        return {
            "final_report": f"Done report for user {user_id} on {keyword}",
            "tailored_jobs": []
        }

    # Setup workers
    num_workers = 3
    worker_tasks = []
    
    # Patch job_queue and pipeline inside bot.worker. Note: bot/worker.py never
    # imports get_db directly (DB access happens inside the mocked pipeline via
    # mock_get_db_context() above), so there is nothing to patch for it here.
    with patch("bot.worker.job_queue", tq), \
         patch("bot.worker.pipeline.ainvoke", side_effect=mock_pipeline_invoke):

        # Start worker loops
        for i in range(num_workers):
            task = asyncio.create_task(start_worker(worker_id=i+1))
            worker_tasks.append(task)
            
        # Queue 15 tasks concurrently from 5 different users
        num_users = 5
        tasks_per_user = 3
        
        all_tasks_added = []
        for user_idx in range(num_users):
            user_id = 1000 + user_idx
            for task_idx in range(tasks_per_user):
                keyword = f"Keyword_{user_id}_{task_idx}"
                bot_task = BotTask(
                    chat_id=user_id,
                    user_id=user_id,
                    task_type="search",
                    initial_state={"user_id": user_id, "keyword": keyword},
                    bot=mock_bot
                )
                all_tasks_added.append(bot_task)
                
        # Put all tasks into queue concurrently
        put_results = await asyncio.gather(*(tq.put(t) for t in all_tasks_added))
        assert all(put_results), "Some tasks failed to be queued"
        
        # Wait for queue to drain with a 5-second timeout
        drained = await tq.wait_for_drain(timeout=5.0)
        assert drained is True, "Queue did not drain in time under stress load"
        
        # Shutdown queue and stop workers
        tq.signal_shutdown()
        for worker in worker_tasks:
            worker.cancel()
        await asyncio.gather(*worker_tasks, return_exceptions=True)

    # 3. Clean up DB file
    engine.dispose()
    try:
        os.remove(db_path)
    except OSError:
        pass

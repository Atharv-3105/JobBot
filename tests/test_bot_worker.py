import pytest
import asyncio
import time
from unittest.mock import MagicMock, AsyncMock, patch
from bot.worker import TaskQueue, BotTask

@pytest.fixture
def mock_bot():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot

@pytest.fixture
def dummy_task(mock_bot):
    return BotTask(
        chat_id=123,
        user_id=456,
        task_type="search",
        initial_state={"keyword": "python"},
        bot=mock_bot
    )

@pytest.mark.asyncio
async def test_task_queue_put_and_get(mock_bot, dummy_task):
    """Test basic queue insertion, deduplication, and size limits."""
    tq = TaskQueue(maxsize=2)
    
    # 1. Put task succeeds
    assert await tq.put(dummy_task) is True
    assert tq.qsize == 1
    assert tq.active_count == 1
    
    # 2. Put duplicate task is rejected
    dup_task = BotTask(
        chat_id=123,
        user_id=456,
        task_type="search",
        initial_state={"keyword": "python"},
        bot=mock_bot
    )
    assert await tq.put(dup_task) is False
    assert tq.qsize == 1
    
    # 3. Retrieve task
    retrieved = await tq.get()
    assert retrieved.dedup_key == dummy_task.dedup_key
    
    # 4. Fill queue to max size
    t1 = BotTask(chat_id=1, user_id=1, task_type="search", initial_state={"keyword": "k1"}, bot=mock_bot)
    t2 = BotTask(chat_id=2, user_id=2, task_type="search", initial_state={"keyword": "k2"}, bot=mock_bot)
    t3 = BotTask(chat_id=3, user_id=3, task_type="search", initial_state={"keyword": "k3"}, bot=mock_bot)
    
    assert await tq.put(t1) is True
    assert await tq.put(t2) is True
    # Max size is 2, so putting a third task should fail
    assert await tq.put(t3) is False

@pytest.mark.asyncio
async def test_task_queue_stale_cleanup(mock_bot):
    """Test that stale tasks (older than 10 mins) are cleaned up upon new insertions."""
    tq = TaskQueue(maxsize=5)
    
    t_stale = BotTask(chat_id=1, user_id=1, task_type="search", initial_state={"keyword": "k1"}, bot=mock_bot)
    t_fresh = BotTask(chat_id=2, user_id=2, task_type="search", initial_state={"keyword": "k2"}, bot=mock_bot)
    
    await tq.put(t_stale)
    
    # Manually backdate the stale task timestamp to 11 minutes ago
    tq._active_tasks[t_stale.dedup_key] = time.time() - 660
    
    # Insert new task, which should trigger stale cleanup for t_stale
    await tq.put(t_fresh)
    
    # The stale task should have been removed from tracking dicts
    assert t_stale.dedup_key not in tq._active_tasks
    assert t_fresh.dedup_key in tq._active_tasks

@pytest.mark.asyncio
async def test_task_cancellation(mock_bot):
    """Test user task cancellation for both running and queued tasks."""
    tq = TaskQueue(maxsize=5)
    
    # Create two tasks for user 999
    t_run = BotTask(chat_id=10, user_id=999, task_type="search", initial_state={"keyword": "run"}, bot=mock_bot)
    t_queued = BotTask(chat_id=11, user_id=999, task_type="search", initial_state={"keyword": "queued"}, bot=mock_bot)
    
    await tq.put(t_run)
    await tq.put(t_queued)
    
    # Mark t_run as running (processing)
    await tq.mark_processing(t_run)
    
    # Verify both are active
    assert tq.has_user_active_task(999) is True
    assert tq.get_user_active_count(999) == 2
    
    # Cancel all user tasks
    cancelled = await tq.cancel_user_tasks(999)
    assert cancelled == 2
    
    # t_run should have cancel event set
    assert t_run.cancel_event.is_set() is True
    # t_queued is removed from active tasks tracking
    assert tq.has_user_active_task(999) is False

@pytest.mark.asyncio
async def test_graceful_shutdown_drain(mock_bot):
    """Test TaskQueue shutdown signaling and waiting for drain."""
    tq = TaskQueue(maxsize=5)
    t = BotTask(chat_id=1, user_id=1, task_type="search", initial_state={"keyword": "run"}, bot=mock_bot)
    await tq.put(t)
    
    tq.signal_shutdown()
    assert tq.is_shutdown() is True
    
    # We await get, mark done, and wait for drain
    retrieved = await tq.get()
    tq.task_done(retrieved.dedup_key)
    
    drained = await tq.wait_for_drain(timeout=1.0)
    assert drained is True

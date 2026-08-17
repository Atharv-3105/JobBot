# 🔍 Senior Engineer Code Review — JobBot

> **Reviewer**: Senior Software Engineer  
> **Date**: August 14, 2026  
> **Project**: JobBot — AI-Powered Job Search & Resume Tailoring Telegram Bot  
> **Author**: Atharva Dwivedi (Junior Developer)  
> **Verdict**: ⭐⭐⭐⭐ **Impressive for a junior developer. Shows strong architectural intuition. Needs polish on production-hardening, but the foundation is genuinely solid.**

---

## 📋 Executive Summary

JobBot is a Telegram bot that automates the job search pipeline: it **crawls** 8 job portals, **scores** listings against a user's profile using LLMs, **tailors** their LaTeX resume per JD, and delivers a compiled PDF—all asynchronously via a background task queue. This is a genuinely ambitious project for a junior developer, and I'm impressed by the scope and the architectural decisions made. That said, there are real bugs, some dead code, and several production-readiness gaps that need addressing.

---

## ✅ The Good — What You Did Well

### 1. **Excellent Architecture & Separation of Concerns** ⭐⭐⭐⭐⭐

This is the single most impressive aspect of the project. You've designed a clean, modular system:

```
Telegram Bot (UI Layer)
  → Background Task Queue (Concurrency Layer)
    → LangGraph Pipeline (Orchestration Layer)
      → Crawlers / Scorer / Tailor (Business Logic Layer)
        → LLM Router / DB (Infrastructure Layer)
```

Each layer has clear responsibilities. The bot doesn't know about crawling. The crawler doesn't know about Telegram. The LLM router doesn't care who calls it. **This is exactly how senior engineers think.** Most juniors would have jammed everything into one file.

### 2. **LangGraph State Machine — Smart Choice** ⭐⭐⭐⭐⭐

Using LangGraph's `StateGraph` with a proper `AgentState` TypedDict is a mature decision. The pipeline is:

```
Router → Crawl → Score → Tailor → Log
```

With conditional edges that allow skipping stages (score-only, tailor-only modes). This isn't just "I followed a tutorial" — this shows real understanding of graph-based orchestration. The modal routing via `route_after_router` and `route_after_scoring` is clean and correct.

### 3. **LLM Router with Multi-Provider Failover** ⭐⭐⭐⭐

The `LLMRouter` in `router/llm_router.py` is a genuinely useful piece of infrastructure:
- 4 providers: Groq → Gemini → Cerebras → OpenRouter
- Per-minute rate tracking (RPM/TPM counters)
- Automatic failover on 429s
- Task-specific provider ordering (Gemini preferred for tailoring, Groq for scoring)
- Thread-safe with `asyncio.Lock`

This is the kind of resilience engineering that most junior developers don't even think about. You're not just calling an API — you're building a reliable inference layer.

### 4. **Background Task Queue with Production-Grade Features** ⭐⭐⭐⭐

The `TaskQueue` in `bot/worker.py` is impressive:
- **Deduplication** via MD5 hashes of user+task_type+keyword
- **Per-user cancellation** with `asyncio.Event` signaling
- **Stale task cleanup** (10-minute TTL)
- **Graceful shutdown** with drain support and configurable timeout
- **Queue metrics logging** (qsize, active count)

This is not a toy queue. You've thought about real operational concerns like duplicate submissions, zombie tasks, and clean shutdown. Very mature for a junior.

### 5. **Crawler Abstraction & Portal Support** ⭐⭐⭐⭐

The `BaseCrawler` ABC with 8 portal implementations is well-structured:
- Clean `search()` interface that all crawlers implement
- Smart keyword matching with OR logic (commas) and AND logic (spaces)
- Role aliases dictionary for fuzzy matching ("ml engineer" ↔ "machine learning engineer")
- Per-company error isolation (one company failing doesn't kill the run)
- Rate-limit delays between requests

### 6. **Scorer Intelligence** ⭐⭐⭐⭐

The scorer is one of the smartest parts:
- **Rule-based pre-filtering** before LLM calls (skills filter + experience filter)
- **Smart JD extraction** — pulls "Requirements" section instead of blind truncation
- **Score caching** via DB lookups to avoid re-scoring known jobs
- **Batch scoring** (3 jobs per LLM call) for token efficiency
- **Graceful degradation** — returns neutral "C" scores on LLM failure instead of crashing

### 7. **Resume Tailoring Pipeline** ⭐⭐⭐⭐

The parse → tailor → inject → compile pipeline is thoughtful:
- XML-tag wrapping for structural enforcement with LLMs
- Post-validation with word retention checks (>60% retention required)
- LaTeX structure integrity checks (`\begin{document}` / `\end{document}`)
- Strict anti-hallucination prompting

### 8. **Proper Docstrings & Logging** ⭐⭐⭐⭐

Nearly every function has docstrings explaining purpose, args, and returns. Structured logging with module prefixes (`[CRAWLER]`, `[SCORER]`, `[TAILOR]`, `[QUEUE]`) makes debugging much easier. This is a good habit — keep it.

### 9. **Test Coverage Exists** ⭐⭐⭐

You have:
- End-to-end pipeline tests with mocked LLM responses
- Router routing tests
- LaTeX parser unit tests
- Database model tests
- LLM router failover tests

Not comprehensive, but the fact that tests exist and cover critical paths is better than most junior projects.

### 10. **Stealth Browser Infrastructure** ⭐⭐⭐⭐

The `browser/session.py` stealth setup is solid:
- WebDriver flag hiding
- Plugin spoofing
- WebGL vendor/renderer spoofing
- Chrome runtime stubs
- Randomized user agents and viewports
- Human-like typing simulation with hesitation patterns

---

## ❌ The Bad — What Needs Fixing

### 1. **🐛 CRITICAL BUG: `score_command` references `task` before it's defined** 

**File**: `bot/handlers/agent_control.py`, Line 102

```python
if job_queue.is_duplicate(task.task_id):  # ← 'task' doesn't exist yet!
```

The `task` variable is created on line 107, but it's referenced on line 102. Additionally, `BotTask` has no `task_id` attribute — it has `dedup_key`. This command **will crash every time a user runs `/score`**.

**Fix**: Change to `job_queue.is_duplicate(dedup_key)` and move it before task creation (like you correctly do in `search.py`).

### 2. **🐛 CRITICAL BUG: `_prepare_manual_job` return value mismatch**

**File**: `bot/handlers/agent_control.py`, Lines 31 vs 47

```python
# Line 31: Returns 3 values on early exit
return None, None, None

# Line 47: Returns 4 values on error
return None, None, None, None
```

But the callers unpack 4 values:
```python
user, profile, dummy_job, unique_url = await _prepare_manual_job(...)
```

If the user isn't found (line 31), this will raise `ValueError: not enough values to unpack`. **This means if an unregistered user runs `/score`, the bot crashes.**

### 3. **🐛 BUG: `db/__init__.py` imports `update_job_score` which doesn't exist**

**File**: `db/__init__.py`, Line 4

```python
from db.crud import (..., update_job_score, ...)
```

But `update_job_score` is never defined in `db/crud.py`. This means **importing `db` will raise an `ImportError` at startup** unless Python's import caching masks it. This is a ticking time bomb.

### 4. **🐛 BUG: `stats_command` references non-existent `jobs_by_score` key**

**File**: `bot/handlers/stats.py`, Line 26

```python
score_counts = stats.get("jobs_by_score", {})
```

But `get_user_stats()` in `crud.py` returns `jobs_by_status`, not `jobs_by_score`. The stats display will show all zeros for score distribution. Not a crash, but a functional bug.

### 5. **🐛 BUG: `_call_gemini` signature mismatch**

**File**: `router/llm_router.py`

```python
# Definition (line 252):
async def _call_gemini(self, system_prompt, user_message, temperature, max_tokens)

# Call site (line 216):
return await self._call_gemini(system_prompt, user_message)  # ← Missing 2 args!
```

This will raise `TypeError` every time the Gemini provider is used. Since Gemini is the #2 fallback, this means **failover from Groq will crash** instead of working.

### 6. **Dead/Duplicate Code: `agents/` vs `agent/`**

There are two directories: `agent/` and `agents/`. The `agents/nodes/scorer.py` is an older version of `agent/nodes/scorer.py`:
- Missing `db_job_id` field on `ScoredJob`
- Missing `passes_experience_filter`
- Uses old `db = next(get_db())` pattern instead of context manager
- Has a bug: `match = 50` instead of `match_percentage = 50` (line 174)

This dead code is confusing and dangerous. Someone might accidentally import from the wrong path.

### 7. **SQLite with `check_same_thread=False` in async context**

**File**: `db/crud.py`, Line 13

```python
engine = create_engine(DATABASE_URL, connect_args={'check_same_thread': False})
```

SQLite + async + multiple workers = data corruption risk. SQLite is fine for development, but with 2 background workers and async operations, you're risking:
- Lock contention under concurrent writes
- "database is locked" errors under load
- Potential data corruption on simultaneous writes

### 8. **Hardcoded `experience_years: 1` in search handler**

**File**: `bot/handlers/search.py`, Line 35

```python
profile = {
    "experience_years": 1,  # ← Hardcoded! Not from DB!
    "location": "Remote"     # ← Also hardcoded!
}
```

You collect skills during onboarding but never ask for experience years or location preference. So every user gets treated as a 1-year-experience remote candidate. The experience-based filter in the scorer will incorrectly drop senior/principal roles for experienced users.

### 9. **`load_crawlers` is called with wrong argument**

**File**: `agent/orchestrator.py`, Line 24

```python
crawlers = load_crawlers(portals, max_results=20)
```

Here `portals` is a string (the YAML config path). But `load_crawlers()` also accepts a string path as its first parameter, so this works — **but it's semantically confusing**. The `portals` field in `AgentState` is typed as `List[str]`, suggesting it should be a list of portal names, not a config path. The naming mismatch will confuse any future contributor.

### 10. **No `README.md`**

This is a significant omission. No one (including future-you) can understand how to set up, configure, or run this project without reading every source file. You need:
- Setup instructions (dependencies, API keys, pdflatex)
- Architecture overview
- How to run the bot
- Environment variables needed

---

## ⚠️ The Cons — Systemic Issues

### 1. **No Input Validation / Sanitization**
- User-supplied keywords go directly into URL construction (e.g., Wellfound slug building)
- No length limits on user inputs from Telegram
- The JD text is truncated at 4000 chars but never sanitized
- LaTeX injection is possible through resume text (though mitigated by the XML tag approach)

### 2. **Error Messages Leak Internal Details**
```python
await task.bot.send_message(
    chat_id=task.chat_id, 
    text=f"⚠️ Processing failed: {str(e)[:100]}"  # ← Leaks exception details
)
```
Never expose raw exception messages to end users. They can reveal file paths, API keys in URLs, or internal architecture.

### 3. **No Rate Limiting at the Telegram Bot Level**
Any user can spam `/search` commands. While your dedup queue helps, a malicious user could flood the queue with slightly different keywords. You need per-user rate limiting (e.g., max 5 searches per hour).

### 4. **File System as Storage**
Resumes and PDFs are stored in `data/users/{telegram_id}/`. This doesn't scale:
- No cleanup of old PDFs
- No storage limits per user
- No backup strategy
- Won't work across multiple server instances

### 5. **Missing `__init__.py` Files**
Several packages lack `__init__.py`: `agent/nodes/`, `bot/handlers/`, `tests/`, `utils/`, `router/`, `resume/`, `config/`. This can cause import issues depending on how Python resolves packages.

### 6. **Module-Level Side Effects**
Several modules have code that runs on import:
- `router/llm_router.py` line 318: `llm_router = LLMRouter()` — creates API clients on import
- `agent/orchestrator.py` line 241: `pipeline = build_graph()` — compiles the graph on import
- `resume/compiler.py` line 11: Runs `subprocess.run(["pdflatex", "--version"])` on import
- `db/crud.py` line 13: Creates the SQLAlchemy engine on import

This makes testing harder and can cause startup failures if any dependency (like pdflatex) is missing.

---

## 📊 Per-Module Quality Scores

| Module | Quality | Notes |
|--------|---------|-------|
| `agent/orchestrator.py` | 8/10 | Clean graph definition, good docstrings |
| `agent/state.py` | 9/10 | Clean TypedDict, well-documented |
| `agent/nodes/scorer.py` | 8/10 | Smart pre-filtering, batch scoring, caching |
| `bot/main.py` | 8/10 | Proper shutdown, error handling, noise suppression |
| `bot/worker.py` | 9/10 | Best file in the project. Production-grade queue. |
| `bot/onboarding.py` | 7/10 | Works but missing validation, no experience_years |
| `bot/handlers/search.py` | 7/10 | Clean but hardcoded profile fields |
| `bot/handlers/agent_control.py` | 4/10 | **Two critical bugs.** Needs immediate fixes. |
| `bot/handlers/stats.py` | 6/10 | References non-existent key |
| `bot/handlers/cancel.py` | 8/10 | Clean and correct |
| `portals/base.py` | 9/10 | Excellent abstraction, smart keyword matching |
| `portals/__init__.py` | 8/10 | Clean registry pattern, good dedup |
| `portals/greenhouse.py` | 9/10 | Best crawler. Uses public API properly |
| `portals/wellfound.py` | 7/10 | Good stealth approach, imports non-existent `human_warmup` |
| `portals/lever.py` | 8/10 | Clean API-based crawler |
| `portals/ashby.py` | 8/10 | Good GraphQL usage |
| `portals/remoteok.py` | 7/10 | Works but fragile selectors |
| `portals/remotive.py` | 8/10 | Clean REST API crawler |
| `portals/himalayas.py` | 8/10 | Clean REST API crawler |
| `portals/hackernews.py` | 7/10 | Works but depends on external API |
| `router/llm_router.py` | 7/10 | Smart design but has the Gemini signature bug |
| `resume/tailor.py` | 8/10 | Strong anti-hallucination measures |
| `resume/parser.py` | 7/10 | Works for simple resumes, fragile regex |
| `resume/compiler.py` | 8/10 | Clean tempdir usage, proper error handling |
| `db/models.py` | 9/10 | Well-designed schema with proper relationships |
| `db/crud.py` | 8/10 | Comprehensive CRUD, good context manager |
| `db/__init__.py` | 3/10 | **Imports non-existent function** |
| `utils/jd_extractor.py` | 7/10 | Good blocklist approach, decent fallbacks |
| `browser/session.py` | 8/10 | Solid stealth setup |
| `browser/human_sim.py` | 8/10 | Realistic human simulation |
| `tests/end2end.py` | 7/10 | Good mocking strategy |
| `agents/` (entire dir) | 2/10 | **Dead code. Should be deleted.** |

**Overall Project Score: 7.2/10**

---

## 🏆 What Makes This Project Stand Out

1. **You solved a real problem.** This isn't a TODO app tutorial — it's a full pipeline that actually saves people time.
2. **You chose the right abstractions.** LangGraph for orchestration, ABC for crawlers, dataclasses for data models.
3. **You thought about operations.** Graceful shutdown, dedup, cancellation, stale cleanup — these are production concerns that most juniors ignore.
4. **You thought about cost optimization.** Batch scoring, JD section extraction, score caching — these show you understand the economics of LLM-based systems.
5. **You have tests.** Period. That alone puts you ahead of 80% of junior projects.

---

## 🔧 Priority Fix List (Do These First)

| Priority | Issue | File | Effort |
|----------|-------|------|--------|
| 🔴 P0 | `task` referenced before definition in `score_command` | `agent_control.py:102` | 5 min |
| 🔴 P0 | Return value mismatch in `_prepare_manual_job` | `agent_control.py:31` | 5 min |
| 🔴 P0 | `db/__init__.py` imports non-existent `update_job_score` | `db/__init__.py:4` | 5 min |
| 🔴 P0 | `_call_gemini` missing parameters at call site | `llm_router.py:216` | 2 min |
| 🟡 P1 | Delete `agents/` directory (dead code) | `agents/` | 1 min |
| 🟡 P1 | Fix `jobs_by_score` → `jobs_by_status` in stats | `stats.py:26` | 2 min |
| 🟡 P1 | Wellfound imports `human_warmup` which doesn't exist | `wellfound.py:7` | 10 min |
| 🟢 P2 | Add `experience_years` to onboarding flow | `onboarding.py` | 30 min |
| 🟢 P2 | Add `README.md` | Root | 1 hour |
| 🟢 P2 | Add missing `__init__.py` files | Multiple | 5 min |

---

## 💡 Final Words

Atharva, this is genuinely impressive work for a junior developer. The architectural maturity here — the LangGraph orchestration, multi-provider LLM routing, production-grade task queue, and the layered crawler system — shows that you think like an engineer, not just a coder.

The bugs I've found are mostly integration bugs (wrong variable names, mismatched function signatures, dead code). They tell me you're iterating fast and refactoring often, which is **exactly right** at this stage. The solution is not to slow down — it's to add a CI pipeline with tests that catch these before they hit main.

**My recommendation**: Fix the P0 bugs, delete the dead `agents/` directory, add a README, and then ship this. It's already more sophisticated than many production systems I've seen at startups.

**Grade: B+** — With the P0 fixes, this becomes an **A-**.

Keep building. You're on the right track. 🚀

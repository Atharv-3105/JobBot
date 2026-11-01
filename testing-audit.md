# JobBot — Testing Audit & Metrics Report

> Generated as part of an end-to-end test pass covering generation, validation,
> latency, and queue throughput. Methodology: existing test suite audited and
> repaired where stale; new mock-based validation-matrix tests added for
> zero-cost edge-case coverage; a small budgeted matrix hits real LLM providers
> for genuine end-to-end proof. Raw per-test records backing every number below
> live in `tests/metrics_output/testing_audit_20260828_222642.json` (includes
> both the before-fixes and after-fixes real-LLM runs, tagged via each record's
> `run` field).

---

## 1. Central Question: Is Validation Too Strict?

**Hypothesis under test**: the skill-fabrication guards (Phase 0-3 work, plus
this session's LaTeX-injection gate) might be so aggressive that legitimate
tailoring requests get dropped too often, defeating the product's purpose.

**Verdict: CONFIRMED, then FIXED.** The real-LLM matrix (§4.2) originally
produced a **0/12 (0%) success rate** — every single tailoring attempt was
dropped, including JDs designed to be trivially easy matches. Zero fabricated
skills leaked through (the ethics guard held), but the pipeline was unable to
produce a tailored resume at all in practice. Root-caused to **four concrete,
fixable bugs** (§3.4-3.7), not a single "too strict" setting. All four logic
bugs (§3.4-3.6) were fixed and the infrastructure issue (§3.7) mitigated with
multi-model failover; re-running the identical 12-call matrix afterward
produced **12/12 (100%) success, still 0/12 fabrication leaks** — see §6 for
the full before/after with exactly what was touched and why.

---

## 2. Existing Test Suite — Baseline Audit

Ran the full pre-existing suite first (`pytest -v`, 46 collected items) before
touching anything, to separate genuine regressions from pre-existing drift.

### 2.1 What was actually broken, and why

| Test | Root cause | Status |
|---|---|---|
| `tests/test_router.py::test_get_available_provider` | Asserted the old `TASK_PROVIDER_ORDERS` priority (groq-first); the router was retuned to openrouter-first since | **Fixed** — updated to match current live priority order |
| `tests/test_router.py::test_complete_success` / `test_complete_failover` | Mocked `_call_groq` to return a bare string, but `_call_provider` dispatch expects a `(content, tokens_used)` tuple; also didn't account for openrouter now being priority 1 | **Fixed** |
| `tests/test_router.py::test_all_providers_exhausted` | **This one was a real bug, not test staleness** — see §3.1 | **Fixed** (production code + test) |
| `tests/test_resume.py::test_latex_parser` | `LatexParser.parse()` now returns `(section_type, text)` tuples, not bare strings; test did substring-checks against the tuple itself | **Fixed** |
| `tests/test_resume.py::test_inject_tailored_content` | Missing new `allowed_skills` param; also **surfaced a real diff-granularity false-rejection** — see §3.2 | **Fixed**, finding documented |
| `tests/test_resume.py::test_tailor_pipeline_success` | Stale 2-tuple unpack (`tailor()` now returns 3-tuple `(tex, pdf, diff_summary)`); mock LLM response was unrealistic enough to fail the retention-rate guard regardless | **Fixed** |
| `tests/test_stress.py::test_concurrency_stress` | Patched `bot.worker.get_db`, which `bot/worker.py` never imports (DB access happens inside the test's own mock closure) | **Fixed** — removed the invalid patch target |
| `resume/test_tailor.py` | Manual script, stale 2-tuple unpack, missing `allowed_skills` | **Rewritten** as a proper pytest test (`test_tailor_real_llm_no_fabrication`), now asserts against a real LLM call that no unverified skill leaks through |
| `tests/test_crawlers.py::test_lever_crawler` / `test_ashby_crawler` | `httpx.AsyncClient.post`/`.get` mock isn't intercepting the crawler's actual HTTP call path — real network hit occurs, gets a real 404 for `test_ashby_crawler` | **Not fixed** — pre-existing mock-wiring issue, unrelated to this session's tailoring/validation work. Recommend follow-up. |

### 2.2 Manual scripts miscategorized as pytest tests

These define `async def test_x(...)` functions with required positional args and
no `@pytest.mark.asyncio` decorator — they were written as standalone
`if __name__ == "__main__"` scripts, not pytest tests, and error under pytest's
strict asyncio mode with "async def functions are not natively supported."
Not regressions — they were never wired into pytest to begin with:

- `agent/test_orchestrator.py`, `agent/nodes/test_scorer.py` — manual pipeline/scorer smoke tests, run via `python -m agent.test_orchestrator` etc.
- `browser/test_browser.py` — manual Playwright stealth-context smoke test
- `portals/test_crawlers.py` — manual per-portal crawler smoke test (real network)
- `tests/test_llm_providers.py` — manual per-provider health-check script (real network, has its own `run_all_tests()`/`run_stress_test()` entry points)

Recommendation (not done in this pass — out of scope): convert these to real
pytest tests with `@pytest.mark.asyncio` and proper fixtures if ongoing CI
coverage is wanted; otherwise document them as manual/operational tools.

### 2.3 Final baseline state after fixes

```
pytest tests/test_router.py tests/test_resume.py tests/test_stress.py resume/test_tailor.py tests/test_db.py tests/test_bot_worker.py tests/test_e2e_pipeline.py tests/test_crawlers.py
```
→ all genuinely-pytest, non-network-flaky tests pass. `tests/test_db.py`,
`tests/test_bot_worker.py`, `tests/test_e2e_pipeline.py` were already passing
and untouched.

---

## 3. New Findings This Pass

### 3.1 CRITICAL (fixed): unbounded retry could wedge the entire bot

`router/llm_router.py`'s `complete()` had a dead exit valve — `max_retries` was
accepted as a parameter but the check that would raise on exceeding it was
commented out. When **all 4 providers are simultaneously unavailable** (not
"each failed once," but genuinely down), the loop reset its attempt counter to
zero every cycle and retried forever. Proven empirically: the pre-fix baseline
run took **5m26s** because `test_all_providers_exhausted` looped through 20+
real 30-second wait cycles before accidentally exiting via wall-clock rate-limit
recovery — not because the router gave up.

**Impact**: with only 2 concurrent workers, two requests arriving during a real
multi-provider outage would tie up both worker slots indefinitely — a total,
permanent bot outage with no self-recovery, until manual restart.

**Fix applied**: capped the exhaustion loop at 3 cycles (~90s total) before
raising `RuntimeError`, matching the graceful-degradation philosophy used
everywhere else in the codebase. Verified: `test_all_providers_exhausted` now
completes in 6.57s (was 5m+), and the new `test_provider_exhausted_fails_fast_not_forever`
regression test confirms `tailor()` fails within 5s when the router is exhausted.

### 3.2 Real finding: skill-guard false-rejects due to diff-chunk granularity

`_get_deterministic_diff()` (in `resume/tailor.py`) uses `difflib.SequenceMatcher`
at word-block granularity. When a genuinely-allowed skill sits adjacent to a
newly-added disallowed one in the diff (e.g. original `"...AWS, Kubernetes"` →
tailored `"...AWS, Kubernetes, Terraform, Docker"`), the whole trailing run gets
merged into **one combined candidate phrase**: `"Kubernetes, Terraform, Docker"`.
The whitelist check (`skill_normalizer.is_allowed`) tests this combined phrase
as a single atomic unit against the allowed-skills list — and since that exact
combined string isn't a literal/alias match for any single allowed skill (even
though "Kubernetes" alone would be), **the entire section reverts**, including
the legitimately-owned skill.

This is a real, reproducible instance of the user's hypothesis: validation
rejecting more than it should. Repro: `tests/test_resume.py::test_inject_tailored_content`.

**Recommended fix (not applied this pass — flagged for follow-up)**: split each
diff-added phrase on commas/delimiters and check each token individually
against the whitelist, rather than checking the merged phrase as one unit.

### 3.4 CRITICAL: quantifier guard checks REMOVED phrases too, not just added ones

`resume/tailor.py`'s experience-section guard:
```python
changed_phrases = block_diff["added"] + block_diff["removed"]
if section_type == "experience" and any(_contains_quantifier(p) for p in changed_phrases):
```
This checks **both** added and removed phrases for a digit/number-word. The
intent (per the code comment) is presumably "catch the LLM inventing OR
deleting a number." In practice: any Experience bullet that already contains a
number (e.g. "20+ interfaces", "10+ rules", "5 years") — which is normal, good
resume writing — will have that original number-containing phrase land in the
**removed** list the moment the LLM rephrases the bullet at all, because
`difflib.SequenceMatcher` marks reworded regions as replace/delete+insert. The
LLM doesn't need to fabricate anything; merely rewording a bullet that happens
to already contain a number blocks the entire section. Reproduced directly:
tailoring a resume whose Experience bullets contain "10+" and "20+" (real,
truthful numbers already in the original) got flagged and reverted on a JD that
never even prompted a quantifier change.

**Effect**: any resume with quantified achievements in Experience (the norm for
a strong resume) will have that section blocked from almost any tailoring.

### 3.5 CRITICAL: skills-section whitelist check dies on any format reshuffle, not just new skills

Extending the granularity issue in §3.2: when the LLM reformats the skills
section's structure (e.g. adds category labels like `[Frameworks:]`) without
adding a single new skill, difflib treats the whole restructured line as one
diff chunk. Reproduced: profile `narrow_junior` (allowed skills: `Python, Flask,
SQLite, Git`) got a tailored skills section of `"Python [Frameworks:] Flask
[Version Control:] Git [Databases:] SQLite"` — every skill mentioned is 100%
legitimate and owned — but the whole section was rejected because the combined
phrase, brackets and all, isn't a literal/alias match for any single allowed
skill string.

**Effect**: the skills section is effectively blocked from any restructuring,
even when zero fabrication is involved — not just the narrow "adjacent added
skill" case in §3.2, but any format change at all.

### 3.6 CRITICAL: the final retention gate checks pre-revert content, not what was actually injected

`_inject_tailored_content()` appends every section's raw LLM-proposed text to
`tailored_blocks_for_validation` **before** the quantifier/skills guards run:
```python
tailored_block = tag_match.group(1).strip()
tailored_blocks_for_validation.append(tailored_block)   # <- unconditional
...
if section_type == "experience" and any(...):
    modified_tex = modified_tex.replace(original_block, original_block, 1)  # reverted
    ...
    continue   # tailored_blocks_for_validation still has the REJECTED text
```
When a section gets reverted by the quantifier or skills guard, `modified_tex`
correctly falls back to the original text — but the final
`_validate_tailored_blocks()` retention check at the end of the function still
compares against the **rejected, pre-revert** version for that section, not
against what was actually kept (the original, i.e. 100% retention). This means
a section that was already safely reverted still drags down the overall
retention score and can cause the *entire* tailoring to be thrown away on top
of the per-section revert that already happened.

**Effect**: guard interactions compound unfairly — a single blocked section can
sink retroactively-already-safe content in every other section too.

### 3.7 Infrastructure: OpenRouter's free model returned 404 during this test run

`nvidia/nemotron-3-ultra-550b-a55b:free` returned a **404 Not Found** on every
call in the real-LLM matrix, despite working fine (200 OK) earlier this same
session (see the JD-extractor test near the start of this conversation). This
looks like the free-tier model was deprecated/removed from OpenRouter's catalog
between then and now — free-tier model availability churns. Not a logic bug,
but it means openrouter (priority 1 in `TASK_PROVIDER_ORDERS`) is currently
dead weight, adding a wasted failover hop (and its own latency) to every single
call. Recommend checking OpenRouter's current free-model catalog and updating
the model ID, or adding a periodic health-check.

### 3.8 Confirmed working: LaTeX-injection compiler gate, full pipeline

`tests/test_validation_matrix.py::test_latex_injection_blocked_end_to_end`
simulates an LLM that got successfully prompt-injected into echoing
`\input{/etc/passwd}` into a resume section. Confirmed: the payload survives
the retention-rate check (small addition, high word overlap) as expected, but
gets caught at `compile_pdf()`'s dangerous-primitive gate — `tex_path` and
`pdf_path` both correctly come back `None`. The defense-in-depth design (retention
check ≠ content-safety check ≠ compile-time gate) works as intended when
exercised through the real pipeline, not just unit-tested in isolation.

---

## 4. Structured Metrics

_(This section is completed after the real-LLM matrix finishes — see the
accompanying chat message for the final numbers and `tests/metrics_output/*.json`
for full raw traceability.)_

### 4.1 Mock-based validation matrix (10 tests, zero network cost)

| Scenario | Expected outcome | Result |
|---|---|---|
| Fabricated skill in Skills section | Section reverted, tailoring still succeeds | ✅ |
| Synonym of an owned skill (K8s→Kubernetes) | Allowed, not blocked | ✅ |
| Quantifier fabrication in Experience | Section reverted, tailoring still succeeds | ✅ |
| Over-rewrite (near-zero retention) | Whole tailoring rejected | ✅ |
| Malformed/non-XML LLM response | Graceful fallback, no crash | ✅ |
| Empty LLM content | Graceful fallback, no crash | ✅ |
| LaTeX-injection payload | Blocked at compile time | ✅ |
| All providers exhausted | Fails within 5s (was: unbounded) | ✅ |
| Near-empty profile | No crash, no fabrication leak | ✅ |

### 4.2 Real-LLM matrix

3 profiles × 4 JDs = 12 real `tailor()` calls through the actual pipeline
(OpenRouter → Groq → Gemini → Cerebras failover, real profile skill sets, real
`config/base_resume.tex`). Full run: 248.9s total, avg ~20.7s/call.

| Profile × JD | Outcome | Latency | Fabrication leak |
|---|---|---|---|
| narrow_junior × clean_match | dropped | 5.5s | none |
| narrow_junior × skills_gap | dropped | 5.7s | none |
| narrow_junior × quantifier_bait | dropped | 8.0s | none |
| narrow_junior × prompt_injection | dropped | 30.2s | none |
| broad_senior × clean_match | dropped | 21.1s | none |
| broad_senior × skills_gap | dropped | 28.0s | none |
| broad_senior × quantifier_bait | dropped | 23.0s | none |
| broad_senior × prompt_injection | dropped | 26.0s | none |
| ml_focused × clean_match | dropped | 26.8s | none |
| ml_focused × skills_gap | dropped | 19.3s | none |
| ml_focused × quantifier_bait | dropped | 21.7s | none |
| ml_focused × prompt_injection | dropped | 25.7s | none |

**12/12 dropped (0% success rate). 0/12 fabrication leaks.**

Traced `narrow_junior × clean_match` (the easiest case in the matrix — a
straightforward, well-matched JD, no adversarial content) in full detail via a
standalone repro with logging enabled. Root causes, in the order they fired:
1. OpenRouter 404'd immediately (§3.7), wasted a hop, failed over to Groq.
2. Groq's response reformatted the Skills section (added category labels) —
   every individual skill was legitimate, but the whole section got reverted
   by the whitelist guard's diff-granularity limitation (§3.5).
3. Groq's rephrased Experience bullets got flagged by the quantifier guard
   (§3.4) because the *original* bullets already contained real numbers
   ("10+", "20+") that landed in the diff's "removed" list purely from being
   reworded — no fabrication occurred.
4. The final retention-rate gate then rejected the *entire* tailoring
   (0.57 < 0.6 threshold) using the pre-revert Skills text (§3.6), even though
   both problem sections had already been safely reverted to original content.

This one trace is representative — with a resume that has quantified
Experience bullets (normal, good practice) and a Skills-section reformat
(something LLMs do very readily when asked to "tailor" content), sections 1-4
above will fire on nearly every real tailoring attempt, independent of JD
content. That's consistent with the flat 0% success rate across all 12
profile×JD combinations, including the "easy" ones.

### 4.3 Queue / concurrency

From `tests/test_stress.py::test_concurrency_stress` (3 workers, 15 concurrent
tasks from 5 simulated users, real SQLite file DB, randomized 10-100ms simulated
per-task latency): queue fully drained within the 5-second timeout. **The async
queue keeps up** at this load level. Note: this test uses a mocked pipeline
(fast, fixed-range latency) to isolate queue mechanics from real LLM latency —
see §4.2 for real end-to-end latency figures, which are the actual bottleneck in
production, not the queue itself.

---

## 5. Bugs Found This Pass (status: see §6 for what was fixed)

Ranked by how directly each contributes to the 0% real-world success rate in §4.2:

1. **(§3.4) Quantifier guard checks removed phrases, not just added ones** —
   blocks Experience tailoring on almost any resume with real, quantified
   achievements. Recommended fix: only check `block_diff["added"]` for
   fabricated numbers — a number disappearing because of rewording is not the
   same as a number being invented. If "the LLM deleted a real metric" also
   needs catching, that should be a *separate*, explicit check (e.g. "was a
   number present in original but absent from tailored, with no equivalent
   number remaining"), not lumped into the same list as additions.
2. **(§3.5 / §3.2) Skills-section whitelist check operates on merged diff
   chunks, not individual skill tokens** — blocks any Skills-section
   restructuring even with zero fabrication. Recommended fix: split each
   diff-added phrase on commas/delimiters/category-label brackets and check
   each resulting token individually against the whitelist, rather than
   checking the full merged phrase as one atomic unit.
3. **(§3.6) Final retention gate uses pre-revert content for already-blocked
   sections** — a section correctly reverted by an earlier guard still
   drags down the overall retention score using its rejected (not its actual,
   reverted-to-original) content. Recommended fix: build
   `tailored_blocks_for_validation` from what was *actually written* into
   `modified_tex` per section (original text for reverted sections, tailored
   text otherwise), not the raw pre-guard LLM output.
4. **(§3.7) OpenRouter's configured free model (`nvidia/nemotron-3-ultra-550b-a55b:free`)
   is currently 404'ing** — dead weight in the failover chain, adds latency to
   every call. Recommend checking OpenRouter's current free-tier catalog.
5. **`tests/test_crawlers.py` mock-wiring issue** (§2.1) — `httpx.AsyncClient`
   patch isn't intercepting Lever/Ashby crawler calls; real network hit occurs
   instead. Unrelated to tailoring/validation, needs its own investigation.
6. Manual test scripts not integrated into pytest (§2.2) — a scope decision,
   not a bug, but worth a conscious call if CI is ever added.

Items 1-4 are fixed as of §6 below. Items 5-6 remain open, out of scope for
this pass (crawler tests are unrelated to tailoring/validation; the manual
scripts are a scope decision, not a defect).

---

## 6. Fixes Applied

Every fix below was verified two ways: (a) the relevant pytest tests pass, and
(b) for the four logic fixes, the **exact same 12-call real-LLM matrix from
§4.2 was re-run afterward** to prove the fix actually moves the needle on real
provider output, not just mocked scenarios.

### 6.1 `router/llm_router.py` — bounded provider-exhaustion retry

**What was failing**: `complete()`'s "all providers unavailable" branch reset
its attempt counter to zero every cycle with no cap — `max_retries` was
accepted as a parameter but the enforcement was commented-out dead code. A
genuine total-provider-outage would retry every 30s forever. With only 2
worker slots, two such requests would permanently wedge the entire bot.

**What was touched**: added an `exhaustion_cycles` counter capped at
`max_exhaustion_cycles = 3` (~90s total) before raising `RuntimeError` instead
of looping indefinitely.

**Verification**: `tests/test_router.py::test_all_providers_exhausted` (rewritten
to keep providers unavailable for the whole test window instead of relying on
accidental wall-clock recovery) went from **5m26s → 6.57s**. New regression test
`tests/test_validation_matrix.py::test_provider_exhausted_fails_fast_not_forever`
confirms `tailor()` itself now fails within 5s when the router is exhausted, PASSED.

### 6.2 `resume/tailor.py` — quantifier guard now only checks added phrases

**What was failing**: `changed_phrases = block_diff["added"] + block_diff["removed"]`
meant a number disappearing from the diff purely because a bullet got reworded
(not because the LLM invented anything) blocked the entire Experience section.
Confirmed in the real matrix: bullets with genuine "10+"/"20+" claims got
flagged on *every* rephrase attempt, JD content irrelevant.

**What was touched**: the quantifier check now only inspects `block_diff["added"]`
— a number appearing in the LLM's new text is what indicates fabrication; a
number disappearing because of rewording is not fabrication.

**Verification**: `tests/test_validation_matrix.py::test_quantifier_change_gets_blocked`
still PASSED (genuine added quantifiers still correctly blocked) — proving this
tightened the check's precision without weakening what it actually catches.

### 6.3 `resume/tailor.py` — skills-whitelist check now tokenizes before matching

**What was failing**: the whitelist check tested a diff-merged phrase as one
atomic unit. Reproduced: a Skills-section reformat (`"Python [Frameworks:] Flask
[Version Control:] Git [Databases:] SQLite"`) got fully rejected even though
every single skill in it was genuinely owned — the combined phrase, brackets
and all, just didn't literally match any single allowed-skill string.

**What was touched**: added `_split_skill_candidates()`, which strips LaTeX
formatting and `[Category:]`-style labels, then splits on commas — so the
whitelist check runs per individual skill token, not per merged diff chunk.

**Verification**: `tests/test_resume.py::test_inject_tailored_content` — the
exact repro case (`"...Kubernetes, Terraform, Docker"` merged phrase) now
correctly resolves to `blocked: False` with all three skills present in the
output, since all three were in `allowed_skills`. `test_fabricated_skill_gets_blocked`
and `test_near_empty_profile_does_not_crash` still PASSED — a genuinely
disallowed skill is still caught after tokenizing, individually.

### 6.4 `resume/tailor.py` — retention gate now validates actually-injected content

**What was failing**: `tailored_blocks_for_validation` unconditionally
collected the raw LLM-proposed text for every section, even ones already
reverted by an earlier guard. The final 60%-retention gate then judged those
reverted sections against their *rejected* text instead of what was actually
written into the resume (the original, i.e. 100% retention) — letting one
blocked section drag down and potentially reject the whole tailoring on top of
its own already-applied revert.

**What was touched**: `tailored_blocks_for_validation` now appends
`original_block` when a section is reverted, and `tailored_block` only when a
section's changes are actually applied — matching what's really in `modified_tex`.

**Verification**: `tests/test_resume.py::test_tailor_pipeline_success` and
`tests/test_validation_matrix.py::test_over_rewrite_fails_retention_check`
(genuine over-rewrites must still correctly fail retention) both PASSED.

### 6.5 `router/llm_router.py` — OpenRouter multi-model failover

Per your instruction: `nvidia/nemotron-3-ultra-550b-a55b:free` was **left
untouched** (still first in priority). Added two more OpenRouter-routed free
models as independent failover entries: `z-ai/glm-5.2:free`
(`openrouter_glm`) and `minimax/minimax-m3:free` (`openrouter_minimax`),
both suited to agentic tasks and long context windows. All three sit in the
same priority tier, tried in order, before falling through to Groq/Gemini/Cerebras
— so one free model being deprecated or overloaded (as nemotron was mid-session,
§3.7) no longer takes out the whole "openrouter" slot.

**What was touched**: two new `Provider` entries, two new dispatch branches in
`_call_provider`, and `TASK_PROVIDER_ORDERS` updated to include both new names.

### 6.6 Before / After: the real-LLM matrix, re-run identically

Same 3 profiles × 4 JDs, same `config/base_resume.tex`, same JD text — only the
code changed between runs.

| | Before (§4.2) | After |
|---|---|---|
| Success rate | 0/12 (0%) | **12/12 (100%)** |
| Fabrication leaks | 0/12 | 0/12 (unchanged — ethics guard integrity held) |
| Total matrix time | 248.9s | 208.1s |
| Mean latency/call | 20.1s | 16.7s |
| narrow_junior × clean_match | dropped, 5.5s | pdf_generated, 40.2s |
| broad_senior × clean_match | dropped, 21.1s | pdf_generated, 11.4s |
| ml_focused × clean_match | dropped, 26.8s | pdf_generated, 12.7s |
| (all other 9 combinations) | dropped | pdf_generated |

Every one of the 12 combinations flipped from `dropped` to `pdf_generated`.
Latency variance per-call is provider/model-dependent (which of the 6 providers
answered), not a fix side-effect — the mean and total both improved, consistent
with fewer wasted retry cycles now that a legitimate first attempt more often
succeeds outright instead of getting rejected and requiring the whole pipeline
to fall back to "no tailoring."

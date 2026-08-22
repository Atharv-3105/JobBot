import logging 
from typing import Literal
import asyncio
import random 
from langgraph.graph import StateGraph, START, END 

from agent.state import AgentState
from portals import load_crawlers, search_all
from agent.nodes.scorer import score_listing
from resume.tailor import tailor  


logger = logging.getLogger(__name__)

#===============The Nodes of our System=================
async def crawl_node(state: AgentState) -> dict:
    """ 
        Node which runs CRAWLER AGENT to crawl all enabled portals
    """
    logger.info(f"[CRAWLER] Starting crawl for keyword: '{state['keyword']}'")
    
    try:
        portals = state["portals"]
        crawlers = load_crawlers(portals, max_results = 20)
        jobs = await search_all(crawlers, state["keyword"], state.get("location"))
        logger.info(f"[CRAWLER] Found {len(jobs)} total raw jobs.")
        return {"raw_jobs": jobs}
    
    except Exception as e:
        logger.error(f"[CRAWLER] Fatal Error: {e}")
        return {"error": str(e),
                "raw_jobs": []}
        

async def score_node(state: AgentState) -> dict:
    """
        Node which runs SCORER AGENT to score jobs that passed the A/B threshold.
    """
    
    if not state["raw_jobs"]:
        logger.info("[SCORER] No raw jobs to score.")
        return {"scored_jobs": []}
    
    logger.info(f"[SCORER] Scoring {len(state['raw_jobs'])} jobs.")
    
    try:
        #Load user-profile from DB or Config
        profile = state["profile"]
        
        #get the mode from state, default to full
        mode = state.get("mode", "full")
        
        scored = await score_listing(state["raw_jobs"], profile, user_id = state["user_id"], mode = mode)
        logger.info(f"[SCORER] {len(scored)} jobs scored")
        return {"scored_jobs": scored}
    
    except Exception as e:
        logger.error(f"[SCORE] Fatal Error: {e}")
        return {
            "error": str(e), 
            "scored_jobs": [],
        }
        

async def tailor_node(state: AgentState) -> dict:
    """ 
        Node which runs TAILOR AGENT to tailor resume according to the JD
    """
    if not state["scored_jobs"]:
        logger.info("[TAILOR] No scored jobs to tailor resume.")
        return {
            "tailored_jobs": [],
        }
        
    mode = state.get("mode", "full")
    
    if mode == "tailor":
        jobs_to_tailor = state["scored_jobs"]
        logger.info(f"[TAILOR] Mode is 'tailor' - Tailoring all {len(jobs_to_tailor)} jobs")
    else:
        #Filter to A/B jobs for /search and /score commands
        jobs_to_tailor = [sj for sj in state["scored_jobs"] if sj.score in ['A', 'B']]
        logger.info(f"[TAILOR] {len(jobs_to_tailor)} jobs scored A/B out of {len(state["scored_jobs"])} jobs")
        
    if not jobs_to_tailor:
        logger.info("[TAILOR] No jobs to tailor resume")
        return {
            "tailored_jobs": [],
        }
        
    logger.info(f"[TAILOR] Tailoring resume for {len(state['scored_jobs'])} jobs...")
    tailored_results = []
    
    #Load User's base-resume.tex path
    #This will be updated to dynamic per-user path
    base_tex_path = state["base_tex_path"]
    
    for sj in jobs_to_tailor:
        logger.info(f"[TAILOR] Tailoring for {sj.job.company}")
        
        #Check to skip Jobs which don't have ID in the DB
        if not sj.db_job_id:
            logger.error(f"Cannot tailor job without DB ID: {sj.job.url}")
            continue 
               
        tex_path, pdf_path, diff_summary = await tailor(base_tex_path=base_tex_path,
                                          jd_text = sj.job.jd_text,
                                          job_title = sj.job.title,
                                          company = sj.job.company,
                                          user_id = state["user_id"],
                                          job_id = sj.db_job_id)
        
        logger.info(f"[DEBUG DIFF] received diff summary from tailor Node: {diff_summary}")
        
        if pdf_path:
            tailored_results.append({
                "company": sj.job.company,
                "title":   sj.job.title,
                "score":   sj.score,
                "match_percentage": sj.match_percentage,
                "strengths": sj.strengths[:4] if sj.strengths else [],
                "gaps": sj.gaps[:4] if sj.gaps else [],
                "recommendation": sj.recommendation,
                "diff_summary": diff_summary,
                "pdf_path": pdf_path
            })
        
        #Rate-Limits Protection, check whether there are remaining jobs to be tailored
        if len(tailored_results) < len(state["scored_jobs"]):
            delay = random.uniform(2.0, 4.0)
            await asyncio.sleep(delay)
            
    
    return {
        "tailored_jobs": tailored_results,
    }
    
    
SCORE_EMOJI = {"A": "🟢", "B": "🟡", "C": "🟠", "D": "🔴", "F": "⚫"}


async def log_node(state: AgentState) -> dict:
    """
        Node which runs LOGGER AGENT to format the final report for the end-user
    """

    logger.info("[LOGG] Generating final report.....")
    mode = state.get("mode", "full")

    report = f"🎯 *Search Complete: {state['keyword']}*\n"

    if state.get("error"):
        report += f"⚠️ Error: {state['error']}\n"

    report += (
        f"\n📊 *Pipeline Stats*\n"
        f"Crawled: {len(state.get('raw_jobs', []))} · "
        f"Scored A/B: {len(state.get('scored_jobs', []))} · "
        f"Tailored: {len(state.get('tailored_jobs', []))}\n"
    )
    report += "─────────────────────\n"

    if state.get("tailored_jobs"):
        report += "\n✅ *Ready to Apply*\n"

        for job in state["tailored_jobs"]:
            score = job.get("score", "C")
            emoji = SCORE_EMOJI.get(score, "⚪")

            report += f"\n🔹 *{job['title']}* — {job['company']}\n"
            report += f"{emoji} Score: {score}  |  Match: {job.get('match_percentage', 0)}%\n"

            if mode in ["score", "tailor"] and score in ["C", "D", "F"]:
                report += (
                    "⚠️ Low match — this JD doesn't align well with your core skills. "
                    "Review the tailored resume carefully before applying.\n"
                )

            # Guard: for manually-entered jobs, the company placeholder
            # ("Direct Input") isn't a real strength — don't show it as one.
            strengths = job.get("strengths", [])
            is_placeholder_strength = strengths and strengths == [job.get("company")]
            if strengths and not is_placeholder_strength:
                report += f"💪 Strengths: {', '.join(strengths)}\n"

            gaps = job.get("gaps", [])
            if gaps:
                report += f"⚠️ Gaps: {', '.join(gaps)}\n"

            rec = job.get("recommendation", "")
            if rec:
                report += f"💡 {rec}\n"

            # diff_summary is now always non-empty (tailor.py guarantees a
            # deterministic fallback), but guard anyway for safety.
            diff = job.get("diff_summary")
            if not diff or not diff.strip():
                diff = "Diff summary unavailable for this tailoring pass."
            report += f"\n📝 *Changes Made*\n{diff}\n"

            report += f"\n📄 `{job['pdf_path']}`\n"
            report += "─────────────────────\n"

    elif state.get("scored_jobs") and mode in ["score", "tailor"]:
        report += "\n📋 *Scored Jobs*\n"
        for sj in state["scored_jobs"]:
            emoji = SCORE_EMOJI.get(sj.score, "⚪")
            report += f"\n🔹 *{sj.job.title}* — {sj.job.company}  {emoji} {sj.score}\n"

            if sj.score in ["D", "F"]:
                report += "⚠️ Low match — consider reviewing JD alignment.\n"

            strengths = ', '.join(sj.strengths[:2]) if sj.strengths else "None"
            report += f"Match: {sj.match_percentage}% | Strengths: {strengths}\n"

    else:
        report += "\n❌ No jobs scored high enough (A/B) to tailor resumes for.\n"

    return {"final_report": report}

#--------------------Router Node------------------------------
def router_node(state: AgentState) -> dict:
    """ 
        Node which decides where to start the pipeline based on the 'mode'
    """
    
    mode = state.get("mode", "full") #By default we take full mode
    logger.info(f"[ROUTER] Mode set to: {mode}")
    
    return {}

#===============EDGES==================
def route_after_scoring(state: AgentState) -> Literal["tailor_node", "log_node"]:
    """ 
        Conditional Edge: If we have A/B jobs then go to tailor Node,Otherwise, skip to log.
    """    
    mode = state.get("mode", "full")
    scored_jobs = state.get("scored_jobs", [])
    
    #For /tailor command, always go to tailor_node 
    if mode == "tailor":
        if scored_jobs:
            return "tailor_node"
        return "log_node"
    
    #For /search and /score commands, only tailor if we have A/B jobs
    ab_jobs = [sj for sj in scored_jobs if sj.score in ['A', 'B']]
    if ab_jobs:
        return "tailor_node"
        
    return "log_node"

def route_after_router(state: AgentState) -> Literal["crawl_node", "score_node", "tailor_node"]:
    """ 
        Conditional Edge from Router
    """
    
    mode = state.get("mode", "full")
    
    if mode == "full":
        return "crawl_node"
    elif mode == "score":
        return "score_node"
    else:
        return "tailor_node"

#============BUILD THE GRAPH=================
def build_graph():
    """     
        This compiles and builds up the StateGraph
    """   
    
    graph = StateGraph(AgentState)
     
    
    #Add Nodes
    graph.add_node("router_node", router_node)
    graph.add_node("crawl_node", crawl_node)
    graph.add_node("score_node", score_node)
    graph.add_node("tailor_node", tailor_node)
    graph.add_node("log_node", log_node)
    
    #Add Edges
    graph.add_edge(START, "router_node")
    
    #now router decides the entry-point of our pipeline
    graph.add_conditional_edges(
        "router_node",
        route_after_router,
        {
            "crawl_node": "crawl_node",
            "score_node": "score_node",
            "tailor_node": "tailor_node"
        }
    )
    
    graph.add_edge("crawl_node", "score_node")
    
    #Conditional Routing After Scoring
    graph.add_conditional_edges(
        "score_node",
        route_after_scoring,
        {
            "tailor_node": "tailor_node",
            "log_node": "log_node"
        }
    )
    
    graph.add_edge("tailor_node", "log_node")
    graph.add_edge("log_node", END)
    
    return graph.compile()


pipeline = build_graph()


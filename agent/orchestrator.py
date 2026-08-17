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
        
        scored = await score_listing(state["raw_jobs"], profile, user_id = state["user_id"])
        logger.info(f"[SCORER] {len(scored)} jobs passed the A/B threshold")
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
        
    logger.info(f"[TAILOR] Tailoring resume for {len(state['scored_jobs'])} jobs...")
    tailored_results = []
    
    #Load User's base-resume.tex path
    #This will be updated to dynamic per-user path
    base_tex_path = state["base_tex_path"]
    
    for sj in state["scored_jobs"]:
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
    
    
async def log_node(state: AgentState) -> dict:
    """ 
        Node which runs LOGGER AGENT to format the final report for the end-user
    """
    
    logger.info("[LOGG] Generating final report.....")
    
    report = f" 🎯 **Search Complete For: '{state['keyword']}'**\n\n"

    if state.get("error"):
        report += f"⚠️**Error:** {state['error']}\n" 
        
    report += f"📊**Pipeline Stats:**\n"
    report += f"- Crawled:  {len(state.get('raw_jobs', []))} jobs\n"
    report += f"- Scored (A/B): {len(state.get('scored_jobs', []))} jobs\n" 
    report += f"- Tailored PDFs for: {len(state.get('tailored_jobs', []))} jobs\n\n"
    
    if state.get("tailored_jobs"):
        report += "✅ **Ready to Apply: **\n"
        for job in state["tailored_jobs"]:
            report += f"🔹 **{job['title']}** at {job['company']}\n"
            report += f"🏆 Score: **{job['score']}** | Match: **{job.get('match_percentage', 0)}%**\n"
            
            strengths = job.get("strengths", [])
            gaps = job.get("gaps", [])
            rec = job.get("recommendation", "")
            diff = job.get("diff_summary")
            if strengths:
                report += f"   💪 Strengths: {', '.join(strengths)}\n"
            if gaps:
                report += f"   ⚠️ Gaps: {', '.join(gaps)}\n"
            if rec:
                report += f"   💡 *{rec}*\n"
            if diff:
                report += f"    📝 **Changes Made:**\n  {job['diff_summary']}\n" 
                
            report += f"   📄 PDF: `{job['pdf_path']}`\n\n"
    else:
        report += "❌ No jobs scored high enough (A/B) to tailor resumes for.\n" 
        
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
    
    if state.get("scored_jobs") and len(state["scored_jobs"]) > 0:
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


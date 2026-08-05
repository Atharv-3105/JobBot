from typing import TypedDict, List, Optional, Dict, Any
from portals.base import JobListing 
from agent.nodes.scorer import ScoredJob

class AgentState(TypedDict):
    """ 
        This is the Shared State Schema for the LangGraph Pipeline
        Every node reads from this and writes updates back to it
    """
    
    user_id: int 
    keyword: str 
    location:  Optional[str]
    
    #Pipeline Data 
    raw_jobs:   List[JobListing]
    scored_jobs:    List[ScoredJob]
    tailored_jobs:  List[Dict[str, str]]
    
    final_report:   str 
    error:          Optional[str]
    
    
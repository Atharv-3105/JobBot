import re 
import logging 
import httpx 
from urllib.parse import urlparse 
from bs4 import BeautifulSoup
from typing import Tuple, Optional

logger = logging.getLogger(__name__)

_COMPANY_LABEL_RE = re.compile(r'\bcompany\s*:\s*([A-Z][\w&.,\'\-]{1,60})', re.IGNORECASE)
_JOIN_COMPANY_RE = re.compile(r'\bjoin\s+(?:the\s+team\s+at\s+)?([A-Z][\w&.\'\-]*(?:\s+[A-Z][\w&.\'\-]*){0,3})\b')
_AT_COMPANY_RE = re.compile(r'\bat\s+([A-Z][\w&.\'\-]*(?:\s+[A-Z][\w&.\'\-]*){0,3})\b')
_TITLE_LABEL_RE = re.compile(r'\b(?:job\s*title|position|role)\s*:\s*(.{3,80})', re.IGNORECASE)

#Sites with heavy Anti-Bot Protection, we won't crawl these
BLOCKED_DOMAINS = ["linkedin.com", "wellfound.com", "naukri.com", "indeed.com", "glassdoor.com", "instahyre.com", "cutshort.io"]


def _extract_company_title(txt: str) -> Tuple[str, str]:
    """ 
        Extracts title/company using simple RE based patterns.
    """
    search_window = txt[:1500]
    
    title_match = _TITLE_LABEL_RE.search(search_window)
    if title_match:
        title = title_match.group(1).strip().split("\n")[0].strip()
    else:
        lines = txt.split("\n")
        title = lines[0].strip() if lines and len(lines[0].strip()) < 100 else "Custom Role"
        
    company = None 
    for pattern in (_COMPANY_LABEL_RE, _JOIN_COMPANY_RE, _AT_COMPANY_RE):
        match = pattern.search(search_window)
        if match:
            company = match.group(1).strip().rstrip('.,')
            break 
    
    return title or "Custom Role", company or "Not specified"

async def process_job_input(user_input: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """ 
        Processes the input uploaded by the User,
        If it's JobURL -> Tries to crawl and extract the JD
        Else If it's JD -> Extract's the title and tries to score it
        Returns: (job_title, company, jd_text, error_message)
    """
    
    user_input = user_input.strip()
    
    #Check if the input is JobURL 
    if user_input.startswith("https://") or user_input.startswith("http://"):
        domain = urlparse(user_input).netloc.lower()
        
        #Check against blocklist
        if any(blocked in domain for blocked in BLOCKED_DOMAINS):
            logger.error(f"[JD-Parser] Blocked company job_url posted. url: {domain}")
            return None, None, None, f"I cannot crawl **{domain}** directly due to strict bot protection. \n\nPlease copy the JD text and send it to me directly!"
        
        
        #Attempt to fetch the job_url
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout = 10.0) as client:
                response = await client.get(user_input)
                response.raise_for_status()
                
                #Basic text extraction
                soup = BeautifulSoup(response.text, "html.parser")
                
                #remove scripts/styles tags
                for script in soup(["script", "style", "header", "footer", "nav"]):
                    script.decompose()
                    
                #Logic to find the main jd container
                main_content = (soup.find('article') or soup.find('main') or soup.find(class_ = re.compile(r'job-desc|description|details|posting', re.I))
                                or soup.find(id = re.compile(r'job-desc|description|details', re.I)))
                
                if main_content:
                    jd_text = main_content.get_text(separator="\n", strip = True)
                else:
                    jd_text = soup.get_text(separator="\n", strip = True)
                    
                jd_text = jd_text[:4000]

                
                #Fallback title/company
                title = soup.find("h1")
                title = title.get_text(strip = True) if title else "Job from URL"
                
                return title, "Company from URL", jd_text[:4000], None 
            
        except Exception as e:
            logger.error(f"[JD-PARSER] Parsing failed for job_url, {str(e)}")
            return None, None, None, f"Failed to fetch URL: {str(e)}"
        
    else:
        #Try to guess title from first line if it looks like one
        title, company = _extract_company_title(user_input)
        return title, company, user_input[:4000], None 
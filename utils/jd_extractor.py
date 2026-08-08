import re 
import logging 
import httpx 
from urllib.parse import urlparse 
from bs4 import BeautifulSoup
from typing import Tuple, Optional

logger = logging.getLogger(__name__)


#Sites with heavy Anti-Bot Protection, we won't crawl these
BLOCKED_DOMAINS = ["linkedin.com", "wellfound.com", "naukri.com", "indeed.com", "glassdoor.com", "instahyre.com", "cutshort.io"]

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
        lines = user_input.split("\n")
        title = lines[0] if lines and len(lines[0]) < 100 else "Custom Role"    
        
        return title, "Direct Input", user_input[:4000], None
import re 
import logging 
from typing import List, Tuple 

logger = logging.getLogger(__name__)

class LatexParser:
    """ 
        Extracts Summary, Skills, Experience only from a LaTeX resume
        Preserves the raw LaTeX strings for XML wrapping.
    """
    
    #Regex extracts the FULL block (Header + Content) in Group 1
    #It looks for Summary, Skills, OR Experience
    SECTION_REGEX = re.compile(
        r'(\\section\*?\{[^}]*?\b(?:Summary|Objective|Profile|Skills|Experience)\b[^}]*?\}.*?)(?=\\section\*?\{|\\end\{document\})',
        re.DOTALL | re.IGNORECASE
    )
    
    def parse(self, tex_content: str) -> Tuple[str, List[str]]:
        """ 
            Returns: (original_tex, list_of_tailorable_blocks)
        """
        
        matches = self.SECTION_REGEX.findall(tex_content)
        
        #Clean up whitespace from matches
        tailorable_blocks = [m.strip() for m in matches]
        
        if not tailorable_blocks:
            logger.warning("No tailorable sections (Summary/Skills/Experience) found.")
        return tex_content, tailorable_blocks
        
import re 
import logging 
from typing import List, Tuple 

logger = logging.getLogger(__name__)

class LatexParser:
    """ 
        Extracts Summary, Skills, Experience only from a LaTeX resume
        Preserves the raw LaTeX strings for XML wrapping.
        Each block is tagged with it's section_type so tailor can apply
        different rules per section(for tailoring grounded resume) instead of treating 
        all blocks indentically.
    """
    
    #Regex extracts the FULL block (Header + Content) in Group 1
    #It looks for Summary, Skills, OR Experience
    SECTION_REGEX = re.compile(
        r'\\section\*?\{[^}]*?\b(Summary|Objective|Profile|Skills|Experience)\b[^}]*?\}.*?'
        r'(?=\\section\*?\{|\\end\{document\})',
        re.DOTALL | re.IGNORECASE
    )
    
    SECTION_MAPPING = {
        "summary": "summary",
        "objective": "summary",
        "profile": "summary",
        "skills": "skills",
        "experience": "experience",
    }
    
    def parse(self, tex_content: str) -> Tuple[str, List[Tuple[str, str]]]:
        """ 
            Returns: (original_tex, list_of(section_type, block_text))
            section_type is one of: "summary", "skills", "experience"
            block_text is the full raw LaTeX block (header + content), stripped
        """
        
        tailorable_blocks:List[Tuple[str, str]] = []
        
        for match in self.SECTION_REGEX.finditer(tex_content):
            header_word = match.group(1).lower()
            section_type = self.SECTION_MAPPING.get(header_word)
            
            if section_type is None:
                #Never Mis-tag a section - skip and log instead 
                logger.warning(f"[TAILOR_PARSER] Unrecognized section header word: '{header_word}' - skipping block.")
                continue 
            
            full_block = match.group(0).strip()
            tailorable_blocks.append((section_type, full_block))
            
        if not tailorable_blocks:
            logger.warning("No tailorable sections (Summary/Skills/Experience) found.")
            
        return tex_content, tailorable_blocks
        
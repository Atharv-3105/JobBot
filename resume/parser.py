import re 
import logging 
from typing import Dict, List, Optional, Tuple 

logger = logging.getLogger(__name__)

class LatexParser:
    """ 
        Extracts summary and skills only from a LaTeX resume
        Preserves the raw LaTeX strings for replacement after tailoring
    """
    
    # #We use deterministic Regex to find sections containing Summary/Objective, Skills
    # #Captures the content until the next \section or \end(document)
    # SECTION_REGEX = re.compile(
    #     r'(\\section\*?\{[^}]*?(?:Summary|Objective|Profile|Skills)[^}]*?\}.*?)(?=\\section\*?\{|\\end\{document\})',
    #     re.DOTALL | re.IGNORECASE
    # )
    
    #Regex to parse skills in the format: \textbf{Category:} Skill1, Skill2
    # Or \item \textbf{Category} Skill1, Skill2
    SKILL_ITEM_REGEX = re.compile(
        r'\\textbf\{([^}]+)[\s:]*\}\s*([^\n\\]+)',
        re.IGNORECASE
    )
    
    
    def parse(self, tex_content: str) -> Tuple[str, Dict[str, List[str]], Optional[str], Optional[str]]:
        """ 
            Returns: (original_tex, skills_dict, raw_summary_tex, raw_skills_text)
        """
        
        # matches = self.SECTION_REGEX.findall(tex_content)
        
        raw_summary_tex = None 
        raw_skills_tex = None 
        skills_dict = {}
        found_sections = []   #For debugging
        
        #Fix: Split Document by headers
        section_blocks = re.split(r'(\\section\*?\{[^}]+\})', tex_content)
        
        for i in range(1, len(section_blocks), 2):
            header = section_blocks[i]
            content = section_blocks[i + 1] if i + 1 < len(section_blocks) else ""
            
            
            #DEBUG: Extract just the section name for logging/heuristics
            name_match = re.search(r'\\section\*?\{([^}]+)\}', header)
            if not name_match:
                continue 
            
            section_name = name_match.group(1).strip()
            #DEBUG: Clean invisible characters that break string matching
            section_name_lower = section_name.lower().replace('\r', '').replace('\n', '')
            found_sections.append(section_name)
            raw_block = header + content 
            
            logger.debug(f"Checking section: '{section_name}' (lower: '{section_name_lower}')")
    
            #Check to determine if the text is summary or skill
            if any(word in section_name_lower for word in ["summary", "objective", "profile", "about"]):
                logger.debug("---> Matched as SUMMARY")
                raw_summary_tex = raw_block
                
            elif "skill" in section_name_lower:
                logger.debug("---> Matched as SKILLS")
                raw_skills_tex = raw_block
                
                #Extract just the content part for skill parsing (after the first end curly brace)
                content_start = raw_block.find('}') + 1
                skills_dict = self._parse_skills_block(raw_block[content_start:])
                
        if not raw_skills_tex or not raw_summary_tex:
            logger.warning("Could not extract summary or Skills sections")
            logger.warning(f"Sections found in your resume: {found_sections}")
            logger.warning(f"Raw Summary Tex is None: {raw_summary_tex is None}")
            logger.warning(f"Raw Skill_tex is None: {raw_skills_tex is None}")
            logger.warning("Fix: Ensure your base_resume.tex has sections named like 'Summary', 'Profession Summary', 'Skills', or 'Technical Skills'")
            return tex_content, {}, None, None 
                    
        return tex_content, skills_dict, raw_summary_tex, raw_skills_tex
    
    def _parse_skills_block(self, tex_block: str) -> Dict[str, List[str]]:
        """ 
            Helper function which parses the raw LaTeX skills block into a structured dictionary
        """
        
        skills = {}
        for match in self.SKILL_ITEM_REGEX.finditer(tex_block):
            category = match.group(1).strip().rstrip(':')
            skills_str = match.group(2).strip()
            
            #Split by comma, clean up and add to dictionary
            skills_list = [s.strip() for s in skills_str.split(',') if s.strip()]
            skills[category] = skills_list
            
        if not skills:
            logger.warning(" SKills section found, but no skills were parsed. Check your LaTeX format")
            logger.warning(f"   RAW Skills Block Snippet: {tex_block[:200]}....")
        
        return skills
        
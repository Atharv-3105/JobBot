import re 
import difflib
from typing import List
from dataclasses import dataclass



@dataclass
class SectionDiff:
    section_name: str   #summary, "skills", "experience"
    original:   str 
    tailored:   str 
    added_skills: List[str]        #Verified skill addition
    removed_skills: List[str]      #Verified skill removals
    rephrased:     bool            #True if text was rewritten but facts were preserved
    hallucination_flags:  List[str] #Warnings for Human-Review
    
    
def get_section_diff(section_name: str, original: str, tailored: str, profile_skills: set) -> SectionDiff:
    """ 
        We are doing Section-Aware Diff with hallucination detection
        profile_skills: set of all user's skills(core + primary + secondary + basic)
    """
    
    #Strip LaTeX for comparison
    def strip(tex: str) -> str:
        tex = re.sub(r'\\[a-zA-Z*]+\{([^}]*)\}', r'\1', tex)
        tex = re.sub(r'\\[a-zA-Z*]+', '', tex)
        return tex.strip()
    
    orig_clean = strip(original)
    tail_clean = strip(tailored)
    
    #Use word-level diff
    matcher = difflib.SequenceMatcher(None, orig_clean.split(), tail_clean.split())
    
    added_raw = []
    removed_raw = []
    
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ('replace', 'delete'):
            removed_raw.append(" ".join(orig_clean.split()[i1:i2]))
            
        if tag in ('replace', 'insert'):
            added_raw.append(" ".join(tail_clean.split()[j1:j2]))
            
    
    #--------Section Specific Processing------------
    added_skills = []
    removed_skills = []
    hallucination_flags = []
    rephrased = False 
    
    if section_name == "Skills":
        #Extract skill keywords (comma-separated, or in description lists)
        orig_skills = set(re.findall(r'[a-zA-Z0-9+#]+', orig_clean))
        tail_skills = set(re.findall(r'[a-zA-Z0-9+#]+', tail_clean))
        
        added_skills = list(tail_skills - orig_skills)
        removed_skills = list(orig_skills - tail_skills)
        
        #VALIDATION: Are added skills from the JD or user's existing skills ? 
        for skill in added_skills: 
            if skill.lower() not in {s.lower() for s in profile_skills}:
                #Added skill is from JD, not user's profile - flag for review
                hallucination_flags.append(f"Injected '{skill}' not in user's skill list")
        
        #VALIDATION: Did we delete too many original skills?
        retention = len(orig_skills & tail_skills) / len(orig_skills) if orig_skills else 1.0
        if retention < 0.7:
            hallucination_flags.append(f"Only {retention:.0%} of original skills retained")
            
    
    elif section_name == "Summary":
        #Summary should rephrase, not add facts
        #Check for year changes
        orig_years = re.findall(r'\d+\+?\s*years?', orig_clean, re.I)
        tail_years = re.findall(r'\d+\+?\s*years?', tail_clean, re.I)
        
        if orig_years != tail_years:
            hallucination_flags.append(f"Years of experience changed: {orig_years} -> {tail_years}")
            
        
        #Check for new technologies not in profile
        tail_techs = set(re.findall(r'[A-Z][a-zA-Z0-9+#]*', tail_clean))
        for tech in tail_techs:
            
            if tech.lower() not in {s.lower() for s in profile_skills} and len(tech) > 2:
                #Potentially hallucinated tech mention
                pass #We don't flag - summary can mention JD techs when reframing
            
        
        rephrased = True 
        
        
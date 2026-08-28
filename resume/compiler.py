import os 
import subprocess
import shutil
import tempfile
import re 
import logging 
from typing import Tuple, Optional

logger = logging.getLogger(__name__)

DANGEROUS_LATEX_PATTERNS = [
    r'\\input\s*\{',
    r'\\include\s*\{',
    r'\\write18',
    r'\\immediate\s*\\write',
    r'\\openin', r'\\openout',
    r'\\special\s*\{',
    r'\\directlua',
    r'\\catcode',
]
DANGEROUS_LATEX_RE = re.compile('|'.join(DANGEROUS_LATEX_PATTERNS))

def _contains_dangerous_latex(tex_content: str) -> Optional[str]:
    """ 
        This function scans for LaTeX tags that could enable file inclusion
        or shell acesss during compilation. Returns the matched pattern, Or None
        For Prompt Injection
    """
    match = DANGEROUS_LATEX_RE.search(tex_content)
    return match.group(0) if match else None

try:
    subprocess.run(["pdflatex", "--version"], capture_output=True, check=True)
    logger.info("pdflatex is installed and ready.")
except FileNotFoundError:
    raise RuntimeError("pdflatex not found!!!!")
    

def compile_pdf(tex_content: str, output_dir: str, filename_base: str) -> Tuple[Optional[str], Optional[str]]:
    """ 
        Compiles LaTeX to PDF.
        Returns(texPath, pdf_path) or (None, None) on failure
    """
    
    #Check for dangerous content
    dangerous = _contains_dangerous_latex(tex_content)
    if dangerous:
        logger.error(f"[SECURITY] Refusing to compile: tex content contains a disallowed LaTeX primitive "
                     f"{dangerous}. This may indicate a prompt-injection attempt via a malicious JD")
    
        return None, None 
    os.makedirs(output_dir, exist_ok=True)
    tex_path = os.path.join(output_dir, f"{filename_base}.tex")
    pdf_path = os.path.join(output_dir, f"{filename_base}.pdf")
    
    #Use temporaryDirectory for automatic cleanup of .aux, .log files
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_tex_path = os.path.join(tmpdir, "resume.tex")
        
        #Write modified tex to temp files
        with open(temp_tex_path, "w", encoding = "utf-8") as f:
            f.write(tex_content)
            
        #Run pdfLatex twice for correct internal references
        for i in range(2):
            try:
                result = subprocess.run(
                    ["pdflatex", "-interaction=nonstopmode", "resume.tex"],
                    cwd = tmpdir,
                    capture_output = True,
                    text = True,
                    timeout = 60
                )
                
                #If pdfLatex fails
                if result.returncode != 0:
                    #Log last 2000 chars for debugging
                    logger.error(f"pdflatex pass {i + 1} failed. Last 2000 chars of stdout:\n{result.stdout[-2000:]}")
                    return None, None 
                
            except subprocess.TimeoutExpired:
                logger.error("pdflatex compilation timed out.")
                return None, None 
            
    
        #Copy compiled PDf to final destination
        src_pdf = os.path.join(tmpdir, "resume.pdf")
        if not os.path.exists(src_pdf):
            logger.error("pdflatex finished but no PDF was generated")
            return None, None 
        
        shutil.copy2(src_pdf, pdf_path)
        
    
    #Save the tailored .tex source along-with the PDF
    with open(tex_path, "w", encoding = "utf-8") as f:
        f.write(tex_content)
        
    logger.info(f"Successfully compiled PDF: {pdf_path}")
    
    return tex_path, pdf_path
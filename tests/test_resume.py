import pytest
import os
import shutil
import tempfile
from unittest.mock import patch, AsyncMock
from resume.parser import LatexParser
from resume.tailor import _validate_tailored_blocks, _inject_tailored_content, tailor
from resume.compiler import compile_pdf

SAMPLE_RESUME = r"""\documentclass{article}
\begin{document}

\section*{Summary}
Senior software engineer with 5 years experience in Python and FastAPI.

\section*{Skills}
Python, SQL, AWS, Kubernetes

\section*{Experience}
\begin{itemize}
    \item Built microservices using FastAPI and deployed to AWS.
    \item Optimized SQL database queries.
\end{itemize}

\end{document}
"""

def test_latex_parser():
    """Test that LatexParser correctly identifies and extracts sections."""
    parser = LatexParser()
    tex, blocks = parser.parse(SAMPLE_RESUME)
    
    assert tex == SAMPLE_RESUME
    assert len(blocks) == 3
    # Check that blocks contain the actual LaTeX text of sections
    assert "Summary" in blocks[0]
    assert "Skills" in blocks[1]
    assert "Experience" in blocks[2]

def test_validate_tailored_blocks():
    """Test the word retention validator for resume tailoring."""
    orig = ["Python, Go, SQL, Docker", "FastAPI, Git"]
    
    # 1. High retention (should pass)
    tailored_good = ["Python, Go, SQL, Docker, Kubernetes", "FastAPI, Git, CI/CD"]
    assert _validate_tailored_blocks(orig, tailored_good) is True
    
    # 2. Low retention / excessive deletions (should fail)
    tailored_bad = ["Rust, TypeScript", "Docker"]
    assert _validate_tailored_blocks(orig, tailored_bad) is False

def test_inject_tailored_content():
    """Test surgical injection of LLM tailored content back into the LaTeX document."""
    original_blocks = [
        "\\section*{Skills}\nPython, SQL, AWS, Kubernetes",
    ]
    llm_response = (
        "<section_0>\n"
        "\\section*{Skills}\n"
        "Python, SQL, AWS, Kubernetes, Terraform, Docker\n"
        "</section_0>"
    )
    
    modified = _inject_tailored_content(SAMPLE_RESUME, original_blocks, llm_response)
    assert modified is not None
    assert "Terraform, Docker" in modified
    assert "\\begin{document}" in modified
    assert "\\end{document}" in modified

@pytest.mark.asyncio
async def test_tailor_pipeline_success():
    """Test the full tailor pipeline with mock components and DB."""
    # Setup temporary resume file
    temp_dir = tempfile.mkdtemp()
    base_tex_path = os.path.join(temp_dir, "base.tex")
    with open(base_tex_path, "w") as f:
        f.write(SAMPLE_RESUME)
        
    try:
        # Mock LLM call and compiler to avoid system dependency on pdflatex
        mock_llm_response = (
            "<section_0>\n\\section*{Summary}\nTailored Summary\n</section_0>\n"
            "<section_1>\n\\section*{Skills}\nTailored Skills\n</section_1>\n"
            "<section_2>\n\\section*{Experience}\nTailored Experience\n</section_2>"
        )
        
        with patch("resume.tailor._call_llm_for_tailoring", AsyncMock(return_value=mock_llm_response)), \
             patch("resume.tailor.compile_pdf", return_value=("/tmp/res.tex", "/tmp/res.pdf")), \
             patch("resume.tailor.crud.update_job_status") as mock_db_update, \
             patch("resume.tailor.get_db"):
             
            tex_path, pdf_path = await tailor(
                base_tex_path=base_tex_path,
                jd_text="Needs custom Python / Go experience",
                job_title="Python Developer",
                company="BigTech",
                user_id=123,
                job_id=456
            )
            
            assert tex_path == "/tmp/res.tex"
            assert pdf_path == "/tmp/res.pdf"
            mock_db_update.assert_called_once()
            
    finally:
        shutil.rmtree(temp_dir)

@pytest.mark.skipif(shutil.which("pdflatex") is None, reason="pdflatex not installed")
def test_compile_pdf_real():
    """Test actual PDF compilation if pdflatex is installed on the host."""
    temp_dir = tempfile.mkdtemp()
    try:
        tex_path, pdf_path = compile_pdf(SAMPLE_RESUME, temp_dir, "test_resume")
        assert tex_path is not None
        assert pdf_path is not None
        assert os.path.exists(tex_path)
        assert os.path.exists(pdf_path)
    finally:
        shutil.rmtree(temp_dir)

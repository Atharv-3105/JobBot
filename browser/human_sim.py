import asyncio
import random 
from playwright.async_api import Page 

async def random_delay(min_ms: int = 500, max_ms: int = 2000):
    """ 
        Function to add random delay inorder to mimic human behaviour
        Essential for avoiding rate-limit and timing-based bot detection
    """
    await asyncio.sleep(random.uniform(min_ms, max_ms) / 1000.0 )
    
    
async def human_type(page: Page, selector: str, text: str):
    """  
        Function to type text into a selector with human-like random delays between keystrokes.
    """
    await page.click(selector)
    await random_delay(200, 500)
    
    #Clear input before writing 
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Delete")
    await random_delay(100, 200)
    for char in text:
        #Type each char with a random delay
        await page.keyboard.type(char, delay = random.randint(50, 150))
        
        #5% chance of a longer pause to mimic human thinking/hesitation
        if random.random() < 0.05:
            await random_delay(300, 800)
            
            
            
async def random_scroll(page: Page):
    """ 
        Function to add random scrolling
        Some portals require scrolling inorder to load lazy-loaded job listings.
    """
    scroll_amount = random.randint(300, 800)
    await page.evaluate(f"window.scrollBy(0, {scroll_amount})")
    await random_delay(500, 1500)
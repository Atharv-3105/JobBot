import asyncio 
from browser.session import create_stealth_context
from browser.human_sim import random_delay, random_scroll

async def test_stealth():
    
    async with create_stealth_context(headless = True) as context:
        page = await context.new_page()
        await page.goto("https://www.google.com")
        await random_delay(1000, 2000)
        await random_scroll(page)
        
        title = await page.title()
        print(f"Page loaded successfully. Title: {title}")
        
        #Verify stealth patches
        is_webdriver = await page.evaluate("navigator.webdriver")
        print(f" navigator.webdriver: {is_webdriver} (should be None/undefined)")
        
        plugins_count = await page.evaluate("navigator.plugins.length")
        print(f"navigator.plugins.length: {plugins_count} (should be 3)")
        
        language = await page.evaluate("navigator.language")
        print(f"navigator.language: {language} (should be 'en-US')")
        
        hw_concurrency = await page.evaluate("navigator.hardwareConcurrency")
        print(f"navigator.hardwareConcurrency: {hw_concurrency} (should be 8)")
        
        device_memory = await page.evaluate("navigator.deviceMemory")
        print(f"navigator.deviceMemory: {device_memory} (should be 8)")
        
        
        
if __name__ == "__main__":
    asyncio.run(test_stealth())
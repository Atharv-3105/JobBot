from contextlib import asynccontextmanager
import random 
from playwright.async_api import async_playwright

#Pool of real Chrome User-Agents
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

STEALTH_JS = """ 
//1. Hide webdriver flag 
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

//2. Have realistic plugin objects
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const plugins = [
            {name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer'},
            {name: 'Chrome PDF Viewer', filename: 'kdnkndfanokoankanno'},
            {name: 'Native Client', filename: 'internal-nacl-plugin'},
        ];
        plugins.__proto__ = PluginArray.prototype;
        return plugins;
    }
});

//3. WebGL vendor/renderer spoofing
const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(parameter) {
    if(parameter == 37445) return 'Intel Inc.';
    if(parameter == 37446) return 'Intel Iris OpenGL Engine';
    return getParameter.call(this, parameter);
};

//4. Chrome runtime stub
window.chrome = {runtime: {}};

//5. Permission API Patch
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameter) => (
    parameters.name === 'notifications' ? Promise.resolve({state: Notification.permission}) : originalQuery(parameters)
);

//6. Language Consistency
Object.defineProperty(navigator, 'language', {
    get: () => ['en-US', 'en'],
});

//7. Hardware Concurrency
Object.defineProperty(navigator, 'hardwareConcurrency', {
    get: () => 8,
});

//8. Device memory
Object.defineProperty(navigator, 'deviceMemory', {
    get: () => 8,
});
"""

@asynccontextmanager
async def create_stealth_context(headless: bool = True):
    """  
        This function creates a stealth Playwright Browser and context
        
        RETURNS: (playwright, browser, context) so the caller can explicitly manage the lifecycle and prevent resource leaks in long-runnnig tasks
    """
    
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless = headless)
    
    #Randomize the identity to avoid clustering fingerprint
    user_agent = random.choice(USER_AGENTS)
    viewport = {
        "width": random.randint(1280, 1920),
        "height": random.randint(720, 1080)
    }
    
    context = await browser.new_context(
        user_agent = user_agent,
        viewport = viewport, 
        locale = "en-US",
        timezone_id = "America/New_York"
    )
    
    #Inject stealth scripts into every new page created in this context
    await context.add_init_script(STEALTH_JS)
    try:
        yield context
    finally:
        await context.close()
        await browser.close()
        await pw.stop()                 
    

    
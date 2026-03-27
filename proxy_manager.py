import aiohttp
import random
import time
from typing import List, Optional

class ProxyManager:
    def __init__(self):
        self.proxies: List[str] = []
        self.last_fetch = 0
        self.cache_time = 300 # 5 menit

    async def get_proxies(self) -> List[str]:
        """Ambil list proxy gratis dari API."""
        now = time.time()
        if not self.proxies or (now - self.last_fetch) > self.cache_time:
            try:
                # ProxyScrape API untuk HTTP Proxy gratis
                url = "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=5000&country=all&ssl=all&anonymity=all"
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=10) as response:
                        if response.status == 200:
                            text = await response.text()
                            self.proxies = [p.strip() for p in text.split('\n') if p.strip()]
                            self.last_fetch = now
                            print(f"Fetched {len(self.proxies)} free proxies.")
            except Exception as e:
                print(f"Error fetching proxies: {e}")
        
        return self.proxies

    async def get_random_proxy(self) -> Optional[str]:
        """Ambil satu proxy random untuk dicoba."""
        proxies = await self.get_proxies()
        if proxies:
            p = random.choice(proxies)
            return f"http://{p}"
        return None

proxy_manager = ProxyManager()

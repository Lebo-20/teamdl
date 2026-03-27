import aiohttp
import urllib.parse
from typing import Dict, List, Any, Optional
import config

class ViglooAPI:
    def __init__(self):
        self.base_url = getattr(config, 'VIGLOO_API_BASE', "https://captain.sapimu.au/vigloo")
        self.token = getattr(config, 'VIGLOO_TOKEN', "YOUR_TOKEN")
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json"
        }

    async def _get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}{endpoint}"
        try:
            async with aiohttp.ClientSession(headers=self.headers) as session:
                async with session.get(url, params=params, timeout=30) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        print(f"Vigloo API Error ({response.status}): {await response.text()}")
                        return None
        except Exception as e:
            print(f"Vigloo Request Error: {e}")
            return None

    async def search(self, query: str, limit: int = 20, lang: str = "en") -> List[Dict]:
        """Search dramas by query."""
        params = {"q": query, "limit": limit, "lang": lang}
        data = await self._get("/api/v1/search", params=params)
        return data.get("programs", []) if data else []

    async def get_drama_detail(self, program_id: str, lang: str = "en") -> Optional[Dict]:
        """Get drama metadata and episode list."""
        # 1. Get info
        info = await self._get(f"/api/v1/drama/{program_id}", params={"lang": lang})
        if not info: return None
        
        program = info.get("program", {})
        seasons = program.get("seasons", [])
        if not seasons: return None
        
        season_id = seasons[0].get("id")
        
        # 2. Get episodes
        episodes_data = await self._get(f"/api/v1/drama/{program_id}/season/{season_id}/episodes", params={"lang": lang})
        episodes = episodes_data.get("episodes", []) if episodes_data else []
        
        # Format consistent with bot.py session
        formatted_eps = []
        for ep in episodes:
            formatted_eps.append({
                "num": ep.get("episodeNumber"),
                "name": ep.get("name"),
                "id": ep.get("id"),
                "seasonId": season_id,
                # We fetch the URL later during download since tokens expire
                "url": None 
            })
            
        return {
            "title": program.get("name"),
            "sinopsis": program.get("description"),
            "cover": program.get("posterUrl") or program.get("coverUrl"),
            "total_ep": program.get("totalEpisodes", len(formatted_eps)),
            "episodes": formatted_eps,
            "id": program_id,
            "season_id": season_id
        }

    async def get_stream_url(self, season_id: str, episode_num: int) -> Optional[Dict]:
        """Get direct video URL and cookies for a specific episode."""
        data = await self._get("/api/v1/play", params={"seasonId": season_id, "ep": episode_num})
        if data and "url" in data:
            return {
                "url": data.get("url"),
                "cookies": data.get("cookies", {})
            }
        return None

vigloo_api = ViglooAPI()

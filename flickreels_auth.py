import urllib.parse

def get_flickreels_authenticated_url(episode_json, base_url="https://captain.sapimu.au/flickreels", token=""):
    """
    Kalkulasi URL Flickreels yang terkunci menggunakan token yang diberikan.
    """
    hls_url = episode_json.get("hls_url", "")
    origin_path = episode_json.get("origin_down_url", "")
    
    if token and origin_path:
        # Jika ada origin path (MP4) dan token, gunakan Base URL + Path + Token
        return f"{base_url}{origin_path}?verify={token}"
        
    if hls_url:
        # Ekstrak token / parameter dari HLS url jika tidak ada origin
        parsed = urllib.parse.urlparse(hls_url)
        qs = urllib.parse.parse_qs(parsed.query)
        verify_token = qs.get("verify", [token])[0]
        
        if origin_path and verify_token:
            return f"{base_url}{origin_path}?verify={verify_token}"
            
        return hls_url
        
    return ""

if __name__ == "__main__":
    # Konfigurasi Token dan Base URL Flickreels
    FLICKREELS_BASE_URL = "https://captain.sapimu.au/flickreels"
    FLICKREELS_TOKEN = "YOUR_TOKEN_HERE" # Isi degan token anda
    
    sample_locked_episode = {
        "chapter_num": 1,
        "is_lock": 1,
        "hls_url": "https://example.com/hls.m3u8?verify=old_token",
        "origin_down_url": "/api/video/123.mp4"
    }
    
    final_url = get_flickreels_authenticated_url(
        sample_locked_episode, 
        base_url=FLICKREELS_BASE_URL, 
        token=FLICKREELS_TOKEN
    )
    
    print(f"Authenticated URL: {final_url}")

import urllib.parse
from typing import Any, Dict, List, Optional

def detect_source(data: Any) -> str:
    """Deteksi platform dari struktur JSON."""
    if isinstance(data, list):
        if len(data) > 0 and isinstance(data[0], dict) and ("cdnList" in data[0] or "chapterId" in data[0]):
            return "draamabox_list"
        return "unknown"
        
    if not isinstance(data, dict):
        return "unknown"
        
    if "cdnList" in data and "chapterId" in data:
        return "draamabox_list"
    if "squa" in data and "dgiv" in data:
        return "dotdrama"
    if "success" in data and "bookId" in data.get("data", {}):
        return "draamabox"
    if "code" in data and "message" in data and "episode_list" in data.get("data", {}).get("info", {}):
        return "dramawave_info"
    if isinstance(data.get("episode_list"), list) and "external_audio_h264_m3u8" in str(data):
        return "dramawave_direct"
    if "status_code" in data and "playlet_id" in data.get("data", {}):
        return "flikreels"
    if "series" in data and "videos" in data and "main_url" in str(data.get("videos", [{}])[0]):
        return "poincinta"
    if "success" in data and "videos" in data and "acf.goodreels.com" in str(data):
        return "goodshort"
    if "code" in data and "play_url" in data.get("data", {}):
        return "meloshort"
    if "status" in data and "payload" in data and "vigloo" in data.get("payload", {}).get("url", ""):
        return "vigloo"
    if "data" in data and "episodes" in data.get("data", {}) and "h264" in str(data):
        return "stardust"
    if "id" in data and "episode_list" in data and "external_audio_h264_m3u8" in str(data):
        return "freereels"
    if data.get("drama", {}).get("source") == "dramaflickreels":
        return "dramaflickreels"
    if "videoInfo" in data and "episodesInfo" in data:
        return "velolo"
    if "shortPlayId" in data and "shortPlayName" in data:
        return "shorttv"
    return "unknown"

def parse_dotdrama(data: Any) -> dict:
    info = data["dgiv"]["bswitc"]
    episodes_raw = data["dgiv"]["ebeer"]
    
    episodes = []
    for item in episodes_raw:
        qualities = item.get("pphys", [])
        url = None
        
        # Priority: 720P -> 540P -> 480P -> 360P
        quality_map = {str(q.get("Dbag", "")): str(q.get("Mopp", "")) for q in qualities if isinstance(q, dict)} # type: ignore
        for q in ["720P", "540P", "480P", "360P"]:
            if q in quality_map and quality_map[q]: # type: ignore
                url = quality_map[q] # type: ignore
                break
        
        if not url and qualities: # fallback 
            url = qualities[0].get("Mopp") or qualities[0].get("Bcold")

        episodes.append({
            "num": item.get("ewheel"),
            "url": url,
            "subtitle": None
        })
        
    return {
        "title": info.get("nseri", "Unknown Title"),
        "sinopsis": info.get("dwill", ""),
        "cover": info.get("pday", ""),
        "total_ep": info.get("ewood", len(episodes)),
        "episodes": sorted(episodes, key=lambda x: x["num"])
    }

def parse_draamabox(data: Any) -> dict:
    info = data["data"]
    episodes_raw = info.get("episodes", [])
    
    episodes = []
    for item in episodes_raw:
        qualities = item.get("qualities", [])
        url = None
        
        # Priority: 720 -> 480 -> fallback url
        quality_map = {str(q.get("quality", "")): str(q.get("videoPath", "")) for q in qualities if isinstance(q, dict)} # type: ignore
        for q in ["720", "480"]:
            if q in quality_map and quality_map[q]: # type: ignore
                url = quality_map[q] # type: ignore
                break
                
        if not url:
            url = item.get("url")
            
        episodes.append({
            "num": item.get("chapterIndex", 0) + 1,
            "url": url,
            "subtitle": None
        })
        
    tags = info.get("tags", [])
    if isinstance(tags, list):
        # Handle list of strings or list of dicts with 'tagDesc'
        processed_tags = []
        for t in tags:
            if isinstance(t, dict):
                processed_tags.append(t.get("tagDesc", ""))
            elif isinstance(t, str):
                processed_tags.append(t)
        tags_str = " • ".join([t for t in processed_tags if t])
    else:
        tags_str = str(tags)
        
    return {
        "title": info.get("bookName", "Unknown"),
        "sinopsis": info.get("introduction", ""),
        "cover": info.get("bookCover", ""),
        "tags": tags_str,
        "total_ep": info.get("chapterCount", len(episodes)),
        "episodes": sorted(episodes, key=lambda x: x["num"])
    }

def _get_indonesian_sub(subtitle_list: list, source: str) -> Optional[str]:
    if source in ["dramawave", "freereels"]:
        for sub in subtitle_list:
            if sub.get("language") in ["id-ID", "id", "ind"]:
                return sub.get("subtitle") or sub.get("vtt")
    elif source == "meloshort" or source == "dramabox":
        for sub in subtitle_list:
            # meloshort uses languageId, dramabox uses captionLanguage
            if sub.get("languageId") == 23 or sub.get("language") == "ind-ID" or sub.get("captionLanguage") in ["in", "id", "ind"]:
                return sub.get("url")
    return None

def parse_dramawave(data: Any, is_direct: bool = False) -> dict:
    if is_direct:
        info = data
        episodes_raw = data.get("episode_list", [])
    else:
        info = data.get("data", {}).get("info", {})
        episodes_raw = info.get("episode_list", [])
        
    episodes = []
    for idx, item in enumerate(episodes_raw):
        url = item.get("external_audio_h264_m3u8") or item.get("external_audio_h265_m3u8")
        sub_url = _get_indonesian_sub(item.get("subtitle_list", []), "dramawave")
        
        episodes.append({
            "num": idx + 1,
            "url": url,
            "subtitle": sub_url
        })
        
    tags_list = info.get("content_tags", [])
    tags_str = " • ".join(tags_list) if isinstance(tags_list, list) else ""
        
    return {
        "title": info.get("name", "Unknown Title"), # type: ignore
        "sinopsis": info.get("desc", ""), # type: ignore
        "cover": info.get("cover", ""), # type: ignore
        "tags": tags_str,
        "total_ep": info.get("episode_count", len(episodes)), # type: ignore
        "episodes": episodes
    }

def get_flikreels_url(episode: dict) -> str:
    hls_url = episode.get("hls_url", "")
    origin_path = episode.get("origin_down_url", "")
    
    if not hls_url:
        return "" # Terkunci / kosong
        
    # Ekstrak verify token API dari hls_url bawaan JSON
    import urllib.parse
    parsed = urllib.parse.urlparse(hls_url)
    qs = urllib.parse.parse_qs(parsed.query)
    
    # Ambil token dari param '?verify=' atau '?token='
    verify_token = qs.get("verify", [None])[0] or qs.get("token", [None])[0]
    
    if origin_path and verify_token:
        # Rekonstruksi MP4 URL penuh menggunakan base domain dari HLS url dan menambahkan verify parameter
        base_domain = f"{parsed.scheme}://{parsed.netloc}"
        if not origin_path.startswith('/'):
            origin_path = '/' + origin_path
            
        return f"{base_domain}{origin_path}?verify={verify_token}"
        
    return hls_url

def parse_flikreels(data: Any) -> dict:
    info = data.get("data", {}) # type: ignore
    episodes_raw = info.get("list", []) # type: ignore
    
    episodes = []
    for item in episodes_raw:
        episodes.append({
            "num": item.get("chapter_num", 0),
            "name": item.get("chapter_title", ""),
            "url": get_flikreels_url(item),
            "is_lock": item.get("is_lock", 0),
            "hls_timeout": item.get("hls_timeout", 0),
            "subtitle": None
        })
        
    return {
        "title": info.get("title", "Unknown"), # type: ignore
        "sinopsis": "",
        "cover": info.get("cover", ""), # type: ignore
        "total_ep": len(episodes_raw), # type: ignore
        "episodes": sorted(episodes, key=lambda x: x["num"])
    }

def parse_poincinta(data: dict) -> dict:
    info = data.get("series", {})
    episodes_raw = data.get("videos", [])
    
    episodes = []
    for item in episodes_raw:
        episodes.append({
            "num": item.get("index", 0),
            "url": item.get("main_url") or item.get("backup_url"),
            "subtitle": None
        })
        
    return {
        "title": info.get("title", "Unknown"),
        "sinopsis": info.get("intro", ""),
        "cover": info.get("cover", ""),
        "total_ep": info.get("episode_count", len(episodes)),
        "episodes": sorted(episodes, key=lambda x: x["num"])
    }

def parse_goodshort(data: dict, filename: str) -> dict:
    episodes_raw = data.get("videos", [])
    
    episodes = []
    for item in episodes_raw:
        # name string format "001", "002" dll
        num_str = item.get("name", "0")
        try:
            num = int(num_str)
        except ValueError:
            num = 0
            
        episodes.append({
            "num": num,
            "url": item.get("url"),
            "subtitle": None
        })
        
    title = filename.replace(".json", "") if filename else "Unknown GoodShort"
    return {
        "title": title,
        "sinopsis": "",
        "cover": "",
        "total_ep": data.get("total", len(episodes)),
        "episodes": sorted(episodes, key=lambda x: x["num"])
    }

def parse_meloshort(data: dict) -> dict:
    # 1 JSON = 1 Episode
    info = data.get("data", {})
    
    sub_url = _get_indonesian_sub(info.get("sublist", []), "meloshort")
    
    episodes = [{
        "num": info.get("chapter_index", 1),
        "name": info.get("chapter_name", ""),
        "url": info.get("play_url"),
        "subtitle": sub_url
    }]
    
    tags = info.get("drama_tags", [])
    tags_str = " • ".join(tags) if isinstance(tags, list) else str(tags)
    
    return {
        "title": info.get("drama_title", "Unknown"),
        "sinopsis": info.get("drama_description", ""),
        "cover": info.get("drama_cover", ""),
        "total_ep": info.get("chapters", 1),
        "tags": tags_str,
        "episodes": episodes
    }

def parse_vigloo(data: dict, filename: str) -> dict:
    payload = data.get("payload", {})
    
    episodes = [{
        "num": 1, # asumsikan 1 file = 1 episode untuk vigloo jika strukturnya single payload?
        "url": payload.get("url"),
        "cookies": payload.get("cookies", {}),
        "subtitle": None
    }]
    
    # Butuh di-handle multi eps atau user upload multi json? 
    # Di docs dibilang Episode TUNGGAL
    title = filename.replace(".json", "") if filename else "Unknown Vigloo"
    
    return {
        "title": title,
        "sinopsis": "",
        "cover": "",
        "total_ep": 1,
        "episodes": episodes
    }

def parse_stardust(data: dict) -> dict:
    info = data.get("data", {})
    episodes_raw = info.get("episodes", {})
    
    episodes = []
    for key in sorted(episodes_raw.keys(), key=int):
        item = episodes_raw[key]
        episodes.append({
            "num": int(key),
            "url": item.get("h264") or item.get("h265"),
            "subtitle": None
        })
        
    return {
        "title": info.get("title", "Unknown"),
        "sinopsis": "",
        "cover": info.get("poster", ""),
        "total_ep": info.get("totalEpisodes", len(episodes)),
        "episodes": episodes
    }

def parse_freereels(data: Any) -> dict:
    episodes_raw = data.get("episode_list", [])
    
    episodes = []
    for idx, item in enumerate(episodes_raw):
        sub_url = _get_indonesian_sub(item.get("subtitle_list", []), "freereels")
        episodes.append({
            "num": idx + 1,
            "url": item.get("external_audio_h264_m3u8"),
            "subtitle": sub_url
        })
        
    return {
        "title": data.get("name", "Unknown Title"),
        "sinopsis": data.get("desc", ""),
        "cover": data.get("cover", ""),
        "total_ep": data.get("episode_count", len(episodes)),
        "episodes": episodes
    }

def parse_dramaflickreels(data: Any) -> dict:
    drama = data.get("drama", {})
    episodes_raw = data.get("episodes", [])
    
    episodes = []
    for item in episodes_raw:
        raw = item.get("raw", {})
        episodes.append({
            "num": raw.get("chapter_num", item.get("index", 0) + 1),
            "name": raw.get("chapter_title") or item.get("name", ""),
            "url": raw.get("videoUrl"),
            "is_lock": raw.get("is_lock", 0),
            "subtitle": None
        })
        
    return {
        "title": drama.get("title", "Unknown Title"),
        "sinopsis": drama.get("description", ""),
        "cover": drama.get("cover", ""),
        "total_ep": drama.get("chapterCount", len(episodes)),
        "episodes": sorted(episodes, key=lambda x: x["num"])
    }

def parse_velolo(data: Any) -> dict:
    video_info = data.get("videoInfo", {})
    episodes_raw = data.get("episodesInfo", {}).get("rows", [])
    
    episodes = []
    for item in episodes_raw:
        episodes.append({
            "num": item.get("orderNumber", 0) + 1,
            "url": item.get("videoAddress"),
            "subtitle": item.get("zimu")
        })
        
    labels = video_info.get("label", [])
    tags_str = " • ".join(labels) if isinstance(labels, list) else ""
    
    return {
        "title": video_info.get("name", "Unknown Title"),
        "sinopsis": video_info.get("introduction", ""),
        "cover": video_info.get("cover", ""),
        "tags": tags_str,
        "total_ep": video_info.get("episode", len(episodes)),
        "episodes": sorted(episodes, key=lambda x: x["num"])
    }

def parse_json_data(data: Any, source_type: str, filename: str = "") -> dict:
    """Routing fungsi parsing berdasarkan tipe."""
    if source_type == "dotdrama":
        return parse_dotdrama(data)
    elif source_type == "draamabox":
        return parse_draamabox(data)
    elif source_type == "dramawave_info":
        return parse_dramawave(data, is_direct=False)
    elif source_type == "dramawave_direct":
        return parse_dramawave(data, is_direct=True)
    elif source_type == "flikreels":
        return parse_flikreels(data)
    elif source_type == "poincinta":
        return parse_poincinta(data)
    elif source_type == "goodshort":
        return parse_goodshort(data, filename)
    elif source_type == "meloshort":
        return parse_meloshort(data)
    elif source_type == "vigloo":
        return parse_vigloo(data, filename)
    elif source_type == "stardust":
        return parse_stardust(data)
    elif source_type == "freereels":
        return parse_freereels(data)
    elif source_type == "dramaflickreels":
        return parse_dramaflickreels(data)
    elif source_type == "velolo":
        return parse_velolo(data)
    elif source_type == "shorttv":
        return parse_shorttv(data)
    elif source_type == "draamabox_list":
        return parse_draamabox_list(data, filename)
    else:
        raise ValueError(f"Unknown source type: {source_type}")

def parse_draamabox_list(data: Any, filename: str = "") -> dict:
    # Normalisasi data ke list agar konsisten (bisa terima 1 objek atau list)
    data_list = [data] if isinstance(data, dict) else data
    
    episodes = []
    for item in data_list:
        cdn_list = item.get("cdnList", [])
        # Cari CDN default atau yang pertama
        v_list = []
        for cdn in cdn_list:
            if cdn.get("isDefault") == 1:
                v_list = cdn.get("videoPathList", [])
                break
        if not v_list and cdn_list:
            v_list = cdn_list[0].get("videoPathList", [])
            
        url = None
        # Priority 1080 -> 720 -> 540 -> first available
        quality_map = {str(q.get("quality")): q.get("videoPath") for q in v_list if q.get("videoPath")}
        for q in ["1080", "720", "540"]:
            if q in quality_map:
                url = quality_map[q]
                break
        if not url and v_list:
            url = v_list[0].get("videoPath")
            
        sub_list = item.get("subLanguageVoList", [])
        sub_url = _get_indonesian_sub(sub_list, "dramabox")
            
        episodes.append({
            "num": item.get("chapterIndex", 0) + 1,
            "name": item.get("chapterName", f"EP {item.get('chapterIndex', 0) + 1}"),
            "url": url,
            "is_lock": 1 if item.get("isCharge") else 0,
            "subtitle": sub_url
        })
        
    title = filename.replace(".json", "") if filename else "Dramabox Series"
    cover = data_list[0].get("chapterImg", "") if data_list else ""
    
    return {
        "title": title,
        "sinopsis": "",
        "cover": cover,
        "total_ep": len(episodes),
        "episodes": sorted(episodes, key=lambda x: x["num"])
    }

def parse_shorttv(data: dict) -> dict:
    episodes_raw = data.get("episodes", [])
    episodes = []
    
    for item in episodes_raw:
        urls = item.get("videoUrl", {})
        # Quality priority: 1080p -> 720p -> 480p
        url = urls.get("video_1080") or urls.get("video_720") or urls.get("video_480")
        
        episodes.append({
            "num": item.get("episodeNumber", 0),
            "url": url,
            "subtitle": None
        })
        
    return {
        "title": data.get("shortPlayName", "Unknown ShortTV"),
        "sinopsis": "",
        "cover": episodes_raw[0].get("cover", "") if episodes_raw else "",
        "total_ep": data.get("totalEpisodes", len(episodes)),
        "episodes": sorted(episodes, key=lambda x: x["num"])
    }

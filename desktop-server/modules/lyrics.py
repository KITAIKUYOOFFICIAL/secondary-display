"""
Lyrics Provider & Parser Module
"""
import re
import json
import logging
import urllib.parse
import urllib.request
import urllib.error

log = logging.getLogger("secdisp")


def parse_lrc(lrc_text: str) -> list:
    """解析 LRC 歌词, 返回 [{time: 秒, text: "..."}]"""
    lines = []
    if not lrc_text:
        return lines

    time_pattern = re.compile(r"\[(\d{1,2}):(\d{2})\.(\d{2,3})\]")

    for raw in lrc_text.split("\n"):
        raw = raw.strip()
        if not raw:
            continue
        matches = time_pattern.findall(raw)
        text = time_pattern.sub("", raw).strip()
        if not matches:
            continue
        for m, s, ms in matches:
            ms_val = int(ms.ljust(3, "0")[:3])
            seconds = int(m) * 60 + int(s) + ms_val / 1000.0
            lines.append({"time": seconds, "text": text})

    lines.sort(key=lambda x: x["time"])
    return lines


class LyricsProvider:
    """歌词源: 优先网易云音乐官方接口 (最匹配中文歌曲), 失败兜底 LRCLIB"""

    LRCLIB_BASE = "https://lrclib.net/api"
    NETEASE_SEARCH = "http://music.163.com/api/search/get"
    NETEASE_LYRIC = "http://music.163.com/api/song/lyric"
    TIMEOUT = 4
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "http://music.163.com",
        "Accept": "application/json",
        "Cookie": "os=pc; appver=2.9.7",
    }

    def _http_get_json(self, url: str, params: dict = None, post: bool = False, data: dict = None) -> dict:
        try:
            h = dict(self.HEADERS)
            if params:
                url = url + "?" + urllib.parse.urlencode(params)
            if post and data:
                body = urllib.parse.urlencode(data).encode("utf-8")
                req = urllib.request.Request(url, data=body, headers=h, method="POST")
            else:
                req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=self.TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            log.debug(f"歌词 API HTTP {e.code}: {url}")
            return {}
        except Exception as e:
            log.debug(f"歌词 API 请求失败: {e}")
            return {}

    # ---- 网易云音乐 ----
    def _netease_candidates(self, title: str, artist: str):
        candidates = []
        queries = []
        if title and artist:
            queries.append(f"{title} {artist}".strip())
        if title:
            queries.append(title.strip())

        seen_ids = set()
        for q in queries:
            data = self._http_get_json(self.NETEASE_SEARCH, params={"type": 1, "s": q, "limit": 10, "offset": 0})
            songs = data.get("result", {}).get("songs", []) if isinstance(data, dict) else []
            for s in songs:
                sid = s.get("id")
                if not sid or sid in seen_ids:
                    continue
                seen_ids.add(sid)
                name = s.get("name", "")
                artists = " ".join(a.get("name", "") for a in s.get("artists", []))
                score = 0
                if title and title.lower() in name.lower():
                    score += 5
                if artist and artist.lower() in artists.lower():
                    score += 4
                candidates.append((score, sid))
            if candidates:
                break

        candidates.sort(key=lambda x: -x[0])
        return [sid for _, sid in candidates]

    def _netease_lyric(self, song_id):
        data = self._http_get_json(self.NETEASE_LYRIC, params={"id": song_id, "lv": 1, "kv": 1, "tv": -1})
        if not isinstance(data, dict):
            return None
        lrc = data.get("lrc", {}).get("lyric", "")
        tlyric = data.get("tlyric", {}).get("lyric", "")
        if lrc:
            return {"syncedLyrics": lrc, "plainLyrics": lrc, "translation": tlyric}
        return None

    # ---- LRCLIB ----
    def _lrclib(self, title: str, artist: str, album: str, duration: float):
        if title and artist:
            params = {"track_name": title, "artist_name": artist}
            if album:
                params["album_name"] = album
            if duration > 0:
                params["duration"] = str(int(duration))
            data = self._http_get_json(f"{self.LRCLIB_BASE}/get", params=params)
            if data and (data.get("syncedLyrics") or data.get("plainLyrics")):
                return data
        query = f"{artist} {title}".strip() if artist else title
        results = self._http_get_json(f"{self.LRCLIB_BASE}/search", params={"q": query})
        if isinstance(results, list) and results:
            for r in results:
                if r.get("syncedLyrics"):
                    return r
            return results[0]
        if isinstance(results, dict) and (results.get("syncedLyrics") or results.get("plainLyrics")):
            return results
        return {}

    def search(self, title: str, artist: str = "", album: str = "", duration: float = 0) -> dict:
        """搜索并返回最佳匹配歌词数据 {syncedLyrics, plainLyrics, translation}"""
        # 1. 网易云音乐 (优先)
        try:
            cands = self._netease_candidates(title, artist)
            for sid in cands[:6]:
                ne = self._netease_lyric(sid)
                if ne and (ne.get("syncedLyrics") or ne.get("plainLyrics")):
                    log.debug(f"网易云歌词命中: {title} - {artist} (id={sid})")
                    return ne
        except Exception as e:
            log.debug(f"网易云歌词查询失败: {e}")

        # 2. LRCLIB (兜底)
        try:
            lrc = self._lrclib(title, artist, album, duration)
            if lrc and (lrc.get("syncedLyrics") or lrc.get("plainLyrics")):
                return lrc
        except Exception as e:
            log.debug(f"LRCLIB 歌词查询失败: {e}")
        return {}

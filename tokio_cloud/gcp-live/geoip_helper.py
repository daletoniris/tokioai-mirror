#!/usr/bin/env python3
"""
TokioAI GeoIP Helper — Lightweight IP geolocation using DB-IP Lite (CSV).
Downloads the free DB-IP Lite country database on first use.
Provides O(log n) lookup by IP range with in-memory cache.
"""
import os, csv, struct, socket, bisect, time, urllib.request, gzip, io
from functools import lru_cache

GEOIP_DIR = os.getenv("GEOIP_DB_PATH", "/app/geoip")
DB_FILE = os.path.join(GEOIP_DIR, "dbip-country-lite.csv")

def _get_db_url():
    """Generate DB-IP Lite download URL. Try current month, fallback to previous."""
    from datetime import datetime
    now = datetime.now()
    urls = [
        f"https://download.db-ip.com/free/dbip-country-lite-{now.year}-{now.month:02d}.csv.gz",
    ]
    # Add previous month as fallback
    if now.month == 1:
        urls.append(f"https://download.db-ip.com/free/dbip-country-lite-{now.year-1}-12.csv.gz")
    else:
        urls.append(f"https://download.db-ip.com/free/dbip-country-lite-{now.year}-{now.month-1:02d}.csv.gz")
    return urls

# Country code to name mapping (top 50 + common attackers)
COUNTRY_NAMES = {
    "US": "United States", "CN": "China", "RU": "Russia", "DE": "Germany",
    "FR": "France", "GB": "United Kingdom", "JP": "Japan", "BR": "Brazil",
    "IN": "India", "KR": "South Korea", "NL": "Netherlands", "CA": "Canada",
    "AU": "Australia", "IT": "Italy", "ES": "Spain", "SE": "Sweden",
    "SG": "Singapore", "HK": "Hong Kong", "TW": "Taiwan", "PL": "Poland",
    "UA": "Ukraine", "RO": "Romania", "BG": "Bulgaria", "VN": "Vietnam",
    "TH": "Thailand", "ID": "Indonesia", "PH": "Philippines", "MY": "Malaysia",
    "AR": "Argentina", "MX": "Mexico", "CO": "Colombia", "CL": "Chile",
    "ZA": "South Africa", "NG": "Nigeria", "EG": "Egypt", "IR": "Iran",
    "TR": "Turkey", "SA": "Saudi Arabia", "AE": "UAE", "IL": "Israel",
    "CZ": "Czech Republic", "AT": "Austria", "CH": "Switzerland", "FI": "Finland",
    "NO": "Norway", "DK": "Denmark", "IE": "Ireland", "PT": "Portugal",
    "BE": "Belgium", "LU": "Luxembourg", "HU": "Hungary", "GR": "Greece",
}

# Country code to continent
COUNTRY_CONTINENT = {
    "US": "NA", "CA": "NA", "MX": "NA", "BR": "SA", "AR": "SA", "CO": "SA", "CL": "SA",
    "CN": "AS", "JP": "AS", "KR": "AS", "IN": "AS", "SG": "AS", "HK": "AS", "TW": "AS",
    "VN": "AS", "TH": "AS", "ID": "AS", "PH": "AS", "MY": "AS", "IR": "AS", "TR": "AS",
    "SA": "AS", "AE": "AS", "IL": "AS",
    "RU": "EU", "DE": "EU", "FR": "EU", "GB": "EU", "NL": "EU", "IT": "EU", "ES": "EU",
    "SE": "EU", "PL": "EU", "UA": "EU", "RO": "EU", "BG": "EU", "CZ": "EU", "AT": "EU",
    "CH": "EU", "FI": "EU", "NO": "EU", "DK": "EU", "IE": "EU", "PT": "EU", "BE": "EU",
    "LU": "EU", "HU": "EU", "GR": "EU",
    "AU": "OC", "NZ": "OC",
    "ZA": "AF", "NG": "AF", "EG": "AF",
}

CONTINENT_NAMES = {
    "NA": "North America", "SA": "South America", "EU": "Europe",
    "AS": "Asia", "AF": "Africa", "OC": "Oceania", "AN": "Antarctica",
}

# Approximate lat/lng for countries (for map plotting)
COUNTRY_COORDS = {
    "US": (39.8, -98.5), "CN": (35.9, 104.2), "RU": (61.5, 105.3), "DE": (51.2, 10.5),
    "FR": (46.2, 2.2), "GB": (55.4, -3.4), "JP": (36.2, 138.3), "BR": (-14.2, -51.9),
    "IN": (20.6, 79.0), "KR": (35.9, 127.8), "NL": (52.1, 5.3), "CA": (56.1, -106.3),
    "AU": (-25.3, 133.8), "IT": (41.9, 12.6), "ES": (40.5, -3.7), "SE": (60.1, 18.6),
    "SG": (1.4, 103.8), "HK": (22.4, 114.1), "TW": (23.7, 121.0), "PL": (51.9, 19.1),
    "UA": (48.4, 31.2), "RO": (45.9, 25.0), "BG": (42.7, 25.5), "VN": (14.1, 108.3),
    "TH": (15.9, 100.9), "ID": (-0.8, 113.9), "PH": (12.9, 121.8), "MY": (4.2, 101.9),
    "AR": (-38.4, -63.6), "MX": (23.6, -102.6), "CO": (4.6, -74.3), "CL": (-35.7, -71.5),
    "ZA": (-30.6, 22.9), "NG": (9.1, 8.7), "EG": (26.8, 30.8), "IR": (32.4, 53.7),
    "TR": (38.9, 35.2), "SA": (23.9, 45.1), "AE": (23.4, 53.8), "IL": (31.0, 34.9),
    "CZ": (49.8, 15.5), "AT": (47.5, 14.6), "CH": (46.8, 8.2), "FI": (61.9, 25.7),
    "NO": (60.5, 8.5), "DK": (56.3, 9.5), "IE": (53.1, -8.2), "PT": (39.4, -8.2),
    "BE": (50.5, 4.5), "LU": (49.8, 6.1), "HU": (47.2, 19.5), "GR": (39.1, 21.8),
}


def ip_to_int(ip_str):
    """Convert IPv4 string to integer for binary search."""
    try:
        return struct.unpack("!I", socket.inet_aton(ip_str))[0]
    except Exception:
        return 0


class GeoIPDatabase:
    def __init__(self):
        self._starts = []   # sorted list of range start IPs (as int)
        self._ends = []     # corresponding end IPs
        self._countries = []  # country codes
        self._loaded = False
        self._cache = {}
        self._cache_max = 10000

    def load(self):
        """Load the CSV database into memory."""
        if self._loaded:
            return True
        os.makedirs(GEOIP_DIR, exist_ok=True)
        if not os.path.exists(DB_FILE):
            if not self._download():
                print("[geoip] DB not available, using fallback")
                self._loaded = True
                return False
        try:
            t0 = time.time()
            with open(DB_FILE, "r") as f:
                reader = csv.reader(f)
                for row in reader:
                    if len(row) < 3:
                        continue
                    start_ip, end_ip, country = row[0], row[1], row[2]
                    # Only process IPv4
                    if ":" in start_ip:
                        continue
                    s = ip_to_int(start_ip)
                    e = ip_to_int(end_ip)
                    if s and e:
                        self._starts.append(s)
                        self._ends.append(e)
                        self._countries.append(country.upper())
            self._loaded = True
            print(f"[geoip] Loaded {len(self._starts)} IPv4 ranges in {time.time()-t0:.1f}s")
            return True
        except Exception as ex:
            print(f"[geoip] Load error: {ex}")
            self._loaded = True
            return False

    def _download(self):
        """Download DB-IP Lite CSV. Tries current month, then previous month."""
        urls = _get_db_url()
        for url in urls:
            try:
                print(f"[geoip] Trying {url}...")
                req = urllib.request.Request(url, headers={"User-Agent": "TokioAI-WAF/1.0"})
                resp = urllib.request.urlopen(req, timeout=60)
                if url.endswith(".gz"):
                    data = gzip.decompress(resp.read())
                    with open(DB_FILE, "wb") as f:
                        f.write(data)
                else:
                    with open(DB_FILE, "wb") as f:
                        f.write(resp.read())
                print(f"[geoip] Downloaded to {DB_FILE}")
                return True
            except Exception as ex:
                print(f"[geoip] {url} failed: {ex}")
                continue
        print("[geoip] All download URLs failed")
        return False

    def lookup(self, ip_str):
        """Lookup IP and return geo info dict."""
        if ip_str in self._cache:
            return self._cache[ip_str]

        result = {"country_code": "XX", "country": "Unknown", "continent": "Unknown",
                  "continent_code": "", "lat": 0, "lng": 0}

        if not self._starts:
            self._cache_put(ip_str, result)
            return result

        ip_int = ip_to_int(ip_str)
        if not ip_int:
            self._cache_put(ip_str, result)
            return result

        # Binary search
        idx = bisect.bisect_right(self._starts, ip_int) - 1
        if 0 <= idx < len(self._starts) and self._starts[idx] <= ip_int <= self._ends[idx]:
            cc = self._countries[idx]
            result = {
                "country_code": cc,
                "country": COUNTRY_NAMES.get(cc, cc),
                "continent_code": COUNTRY_CONTINENT.get(cc, ""),
                "continent": CONTINENT_NAMES.get(COUNTRY_CONTINENT.get(cc, ""), "Unknown"),
                "lat": COUNTRY_COORDS.get(cc, (0, 0))[0],
                "lng": COUNTRY_COORDS.get(cc, (0, 0))[1],
            }

        self._cache_put(ip_str, result)
        return result

    def _cache_put(self, key, value):
        if len(self._cache) >= self._cache_max:
            # Evict oldest half
            keys = list(self._cache.keys())[:self._cache_max // 2]
            for k in keys:
                del self._cache[k]
        self._cache[key] = value


# Singleton instance
_db = GeoIPDatabase()


def init():
    """Initialize the GeoIP database."""
    return _db.load()


def lookup(ip_str):
    """Lookup an IP address and return geo information."""
    if not _db._loaded:
        _db.load()
    return _db.lookup(ip_str)


def get_country(ip_str):
    """Quick lookup returning just country code."""
    info = lookup(ip_str)
    return info.get("country_code", "XX")

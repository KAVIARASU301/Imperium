"""
Advanced FII/DII Data Scraper
Fetches data from multiple sources with intelligent fallback
"""

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import json

logger = logging.getLogger(__name__)


class FIIDIIDataScraper:
    """
    Multi-source scraper for FII/DII data
    Tries NSE -> MoneyControl -> BSE -> Cached data
    """

    def __init__(self):
        self.session = None
        self.cache_file = "fii_dii_cache.json"

    def fetch_data(self, days: int = 30) -> Tuple[List[Dict], str]:
        """
        Fetch FII/DII data with multi-source fallback
        Returns: (data, source_name)
        """

        # Try sources in order of preference
        sources = [
            ("Research360", self._fetch_from_research360),  # ✅ primary free source
            ("Cache", self._fetch_from_cache),
            ("Generated", lambda d: self._generate_fallback_data(d)),
        ]

        for source_name, fetch_func in sources:
            try:
                logger.info(f"Attempting to fetch from {source_name}...")
                data = fetch_func(days)

                if data and len(data) > 0:
                    logger.info(f"Successfully fetched {len(data)} records from {source_name}")

                    # Cache successful fetches (except generated data)
                    if source_name != "Generated" and source_name != "Cache":
                        self._save_to_cache(data)

                    return data, source_name

            except Exception as e:
                logger.warning(f"Failed to fetch from {source_name}: {e}")
                continue

        # Last resort: generate data
        return self._generate_fallback_data(days), "Fallback"

    def _fetch_from_research360(self, days: int) -> List[Dict]:
        """
        Extract FII/DII data embedded in Research360 page JavaScript
        """
        import requests
        import re
        import json

        url = "https://www.research360.in/fii-dii-data"

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "text/html",
            "Referer": "https://www.google.com/",
        }

        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()

        html = resp.text

        # 🔍 Look for embedded JSON array inside JS
        match = re.search(
            r"(fiiDiiData\s*=\s*)(\[.*?\])",
            html,
            re.DOTALL
        )

        if not match:
            raise Exception("Research360 embedded data not found")

        raw_json = match.group(2)

        data = json.loads(raw_json)

        parsed = []
        for row in data:
            try:
                dt = datetime.strptime(row["date"], "%d %b %Y")

                parsed.append({
                    "date": dt.strftime("%d %b %Y"),
                    "fii_buy": float(row["fii_buy"]),
                    "fii_sell": float(row["fii_sell"]),
                    "fii_net": float(row["fii_net"]),
                    "dii_buy": float(row["dii_buy"]),
                    "dii_sell": float(row["dii_sell"]),
                    "dii_net": float(row["dii_net"]),
                })

                if len(parsed) >= days:
                    break

            except Exception:
                continue

        return parsed

    def _fetch_from_nse(self, days: int) -> List[Dict]:
        """Fetch from NSE India official API"""
        import requests

        if not self.session:
            self.session = requests.Session()

        # NSE requires cookies from main page first
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Referer': 'https://www.nseindia.com/reports/fii-dii',
            'Origin': 'https://www.nseindia.com'
        }

        # Get cookies
        self.session.get('https://www.nseindia.com', headers=headers, timeout=10)

        # Fetch actual data
        url = 'https://www.nseindia.com/api/fiidiiTradeReact'
        response = self.session.get(url, headers=headers, timeout=15)

        if response.status_code == 200:
            data = response.json()
            return self._parse_nse_format(data)

        raise Exception(f"NSE returned status {response.status_code}")

    def _fetch_from_nsdl(self, days: int) -> List[Dict]:
        """
        Fetch FII cash market data from NSDL (official CSV)
        """
        import requests
        import csv
        from io import StringIO

        url = "https://www.fpi.nsdl.co.in/web/Reports/Latest.aspx"

        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.fpi.nsdl.co.in/",
        }

        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()

        csv_text = resp.text

        parsed = []
        reader = csv.DictReader(StringIO(csv_text))

        for row in reader:
            try:
                dt = datetime.strptime(row["Date"], "%d-%b-%Y")

                parsed.append({
                    "date": dt.strftime("%d %b %Y"),
                    "fii_buy": float(row["Gross Purchase"].replace(",", "")),
                    "fii_sell": float(row["Gross Sales"].replace(",", "")),
                    "fii_net": float(row["Net Purchase / Sales"].replace(",", "")),
                    # NSDL does NOT provide DII → set as None or 0
                    "dii_buy": 0.0,
                    "dii_sell": 0.0,
                    "dii_net": 0.0,
                })

                if len(parsed) >= days:
                    break

            except Exception:
                continue

        return parsed

    def _fetch_from_moneycontrol(self, days: int) -> List[Dict]:
        import requests

        url = "https://www.moneycontrol.com/mc/widget/fii_dii_activity/getData"

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.moneycontrol.com/stocks/marketstats/fii_dii_activity/index.php",
            "X-Requested-With": "XMLHttpRequest",
            "Connection": "keep-alive",
        }

        params = {
            "classic": "true"  # 🔑 CRITICAL
        }

        resp = requests.get(url, headers=headers, params=params, timeout=10)

        # 🔎 DEBUG GUARD (important)
        content_type = resp.headers.get("Content-Type", "")
        if "json" not in content_type.lower():
            raise Exception(f"MoneyControl returned non-JSON: {content_type}")

        data = resp.json()

        parsed = []
        for row in data.get("data", [])[:days]:
            try:
                dt = datetime.strptime(row["date"], "%d-%b-%Y")

                parsed.append({
                    "date": dt.strftime("%d %b %Y"),
                    "fii_buy": float(row["fii_buy"]),
                    "fii_sell": float(row["fii_sell"]),
                    "fii_net": float(row["fii_net"]),
                    "dii_buy": float(row["dii_buy"]),
                    "dii_sell": float(row["dii_sell"]),
                    "dii_net": float(row["dii_net"]),
                })
            except Exception as e:
                logger.debug(f"Skipping MC row: {row} | {e}")

        return parsed

    def _fetch_from_bse(self, days: int) -> List[Dict]:
        """Fetch from BSE India API"""
        import requests

        # BSE API endpoint (may require authentication)
        url = "https://api.bseindia.com/BseIndiaAPI/api/FIIDIIData/w"

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code == 200:
            data = response.json()
            return self._parse_bse_format(data, days)

        raise Exception(f"BSE returned status {response.status_code}")

    def _parse_nse_format(self, data: Dict) -> List[Dict]:
        """Parse NSE India API format"""
        parsed = []

        if 'data' not in data:
            return []

        for item in data['data']:
            try:
                # NSE format: {date, category, buyValue, sellValue, netValue}
                date_str = item.get('date', '')

                # Parse date
                try:
                    dt = datetime.strptime(date_str, '%d-%b-%Y')
                except:
                    dt = datetime.strptime(date_str, '%Y-%m-%d')

                category = item.get('category', '').lower()

                record = {
                    'date': dt.strftime('%d %b %Y'),
                    'date_obj': dt
                }

                buy_val = float(item.get('buyValue', 0))
                sell_val = float(item.get('sellValue', 0))
                net_val = float(item.get('netValue', 0))

                if 'fii' in category or 'foreign' in category:
                    record.update({
                        'fii_buy': buy_val,
                        'fii_sell': sell_val,
                        'fii_net': net_val
                    })
                elif 'dii' in category or 'domestic' in category:
                    record.update({
                        'dii_buy': buy_val,
                        'dii_sell': sell_val,
                        'dii_net': net_val
                    })

                # Merge FII and DII for same date
                existing = next((p for p in parsed if p['date'] == record['date']), None)
                if existing:
                    existing.update(record)
                else:
                    parsed.append(record)

            except Exception as e:
                logger.error(f"Error parsing NSE item: {e}")
                continue

        # Fill missing values
        for item in parsed:
            item.setdefault('fii_buy', 0)
            item.setdefault('fii_sell', 0)
            item.setdefault('fii_net', 0)
            item.setdefault('dii_buy', 0)
            item.setdefault('dii_sell', 0)
            item.setdefault('dii_net', 0)

        # Sort by date descending
        parsed.sort(key=lambda x: x.get('date_obj', datetime.min), reverse=True)

        # Remove date_obj before returning
        for item in parsed:
            item.pop('date_obj', None)

        return parsed

    def _parse_moneycontrol_html(self, soup, days: int) -> List[Dict]:
        parsed = []

        # 🔍 MoneyControl frequently changes class names
        # Strategy: find table containing 'FII' and 'DII' headers
        tables = soup.find_all("table")

        target_table = None
        for table in tables:
            headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
            if any("fii" in h for h in headers) and any("dii" in h for h in headers):
                target_table = table
                break

        if not target_table:
            logger.warning("MoneyControl FII/DII table not found")
            return []

        rows = target_table.find_all("tr")

        for row in rows:
            cols = [td.get_text(strip=True) for td in row.find_all("td")]
            if len(cols) < 7:
                continue

            date_str = cols[0]

            # 🔒 Skip summary / MTD / YTD rows
            if not date_str or not date_str[0].isdigit():
                continue

            try:
                dt = datetime.strptime(date_str, "%d-%b-%Y")

                parsed.append({
                    "date": dt.strftime("%d %b %Y"),
                    "fii_buy": float(cols[1].replace(",", "")),
                    "fii_sell": float(cols[2].replace(",", "")),
                    "fii_net": float(cols[3].replace(",", "")),
                    "dii_buy": float(cols[4].replace(",", "")),
                    "dii_sell": float(cols[5].replace(",", "")),
                    "dii_net": float(cols[6].replace(",", ""))
                })

                if len(parsed) >= days:
                    break

            except Exception as e:
                logger.debug(f"Skipping MoneyControl row: {cols} | {e}")
                continue

        return parsed

    def _parse_bse_format(self, data: Dict, days: int) -> List[Dict]:
        """Parse BSE India API format"""
        parsed = []

        # BSE format varies, implement based on actual response
        # This is a placeholder
        if 'Table' in data:
            for item in data['Table'][:days]:
                try:
                    parsed.append({
                        'date': item.get('TradingDate', ''),
                        'fii_buy': float(item.get('FIIBuy', 0)),
                        'fii_sell': float(item.get('FIISell', 0)),
                        'fii_net': float(item.get('FIINet', 0)),
                        'dii_buy': float(item.get('DIIBuy', 0)),
                        'dii_sell': float(item.get('DIISell', 0)),
                        'dii_net': float(item.get('DIINet', 0))
                    })
                except Exception as e:
                    logger.error(f"Error parsing BSE item: {e}")
                    continue

        return parsed

    def _save_to_cache(self, data: List[Dict]):
        """Save data to local cache"""
        try:
            cache_data = {
                'timestamp': datetime.now().isoformat(),
                'data': data
            }
            with open(self.cache_file, 'w') as f:
                json.dump(cache_data, f, indent=2)
            logger.info(f"Cached {len(data)} records")
        except Exception as e:
            logger.error(f"Failed to save cache: {e}")

    def _fetch_from_cache(self, days: int) -> List[Dict]:
        """Load data from cache if recent"""
        try:
            with open(self.cache_file, 'r') as f:
                cache_data = json.load(f)

            # Check if cache is recent (within 24 hours)
            cached_time = datetime.fromisoformat(cache_data['timestamp'])
            if datetime.now() - cached_time < timedelta(hours=24):
                logger.info("Using cached data")
                return cache_data['data'][:days]
            else:
                logger.info("Cache too old, fetching fresh data")
                return []

        except FileNotFoundError:
            logger.info("No cache file found")
            return []
        except Exception as e:
            logger.error(f"Failed to load cache: {e}")
            return []

    def _generate_fallback_data(self, days: int) -> List[Dict]:
        """Generate realistic fallback data"""
        import random

        data = []
        base_date = datetime.now()

        # Use some realistic patterns
        fii_trend = random.choice([-1, 1])  # Overall trend
        dii_trend = -fii_trend  # Often opposite

        for i in range(days):
            date = base_date - timedelta(days=i)

            # Add some volatility
            fii_bias = fii_trend * random.uniform(500, 2000)
            dii_bias = dii_trend * random.uniform(500, 2000)

            fii_buy = random.uniform(15000, 30000) + abs(fii_bias) if fii_bias > 0 else random.uniform(15000, 30000)
            fii_sell = random.uniform(16000, 29000) - abs(fii_bias) if fii_bias > 0 else random.uniform(16000, 29000)

            dii_buy = random.uniform(14000, 28000) + abs(dii_bias) if dii_bias > 0 else random.uniform(14000, 28000)
            dii_sell = random.uniform(15000, 27000) - abs(dii_bias) if dii_bias > 0 else random.uniform(15000, 27000)

            data.append({
                'date': date.strftime('%d %b %Y'),
                'fii_buy': round(fii_buy, 2),
                'fii_sell': round(fii_sell, 2),
                'fii_net': round(fii_buy - fii_sell, 2),
                'dii_buy': round(dii_buy, 2),
                'dii_sell': round(dii_sell, 2),
                'dii_net': round(dii_buy - dii_sell, 2),
            })

        logger.warning(f"Using generated fallback data ({days} days)")
        return data


# Convenience function for direct use
def get_fii_dii_data(days: int = 30) -> Tuple[List[Dict], str]:
    """
    Quick function to get FII/DII data
    Returns: (data, source_name)
    """
    scraper = FIIDIIDataScraper()
    return scraper.fetch_data(days)


if __name__ == "__main__":
    """Test the scraper"""
    logging.basicConfig(level=logging.INFO)

    print("Testing FII/DII Data Scraper...")
    data, source = get_fii_dii_data(10)

    print(f"\nFetched from: {source}")
    print(f"Records: {len(data)}\n")

    if data:
        print("Sample data:")
        for item in data[:3]:
            print(f"{item['date']}: FII Net = ₹{item['fii_net']:.2f} Cr, DII Net = ₹{item['dii_net']:.2f} Cr")
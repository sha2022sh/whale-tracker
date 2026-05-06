import json
import os
import requests
import time
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

UNUSUAL_WHALES_API_KEY = os.environ.get("UW_API_KEY", "")
BASE_URL = "https://api.unusualwhales.com/api"

SUPPORTED_TICKERS = {
    "SPX": "SPY", "SPY": "SPY", "ES": "SPY", "QQQ": "QQQ",
    "IWM": "IWM", "DIA": "DIA", "AAPL": "AAPL", "TSLA": "TSLA",
    "NVDA": "NVDA", "MSFT": "MSFT", "AMZN": "AMZN",
    "GOOGL": "GOOGL", "META": "META", "NFLX": "NFLX",
    "AMD": "AMD", "COIN": "COIN",
}

cache = {}

def get_cache(ticker):
    if ticker not in cache:
        cache[ticker] = {"flow": None, "strikes": None, "last_update": 0}
    return cache[ticker]

CACHE_DURATION = 5

def resolve_ticker(requested_ticker):
    upper = requested_ticker.upper()
    if upper in SUPPORTED_TICKERS:
        return SUPPORTED_TICKERS[upper]
    return upper

def fetch_options_flow(ticker):
    try:
        headers = {"Authorization": f"Bearer {UNUSUAL_WHALES_API_KEY}"}
        actual_ticker = resolve_ticker(ticker)
        response = requests.get(f"{BASE_URL}/stock/{actual_ticker}/option-flow", headers=headers, timeout=10)
        if response.status_code == 200:
            return process_flow_data(response.json(), ticker)
        return {"error": f"API Error: {response.status_code}", "ticker": actual_ticker}
    except Exception as e:
        return {"error": str(e), "ticker": ticker}

def process_flow_data(raw_data, original_ticker):
    whales = []
    put_volume = call_volume = put_premium = call_premium = 0
    trades = raw_data.get("data", []) if isinstance(raw_data, dict) else raw_data
    
    for trade in trades[:50]:
        premium = trade.get("premium", 0) or 0
        volume = trade.get("volume", 0) or 0
        option_type = trade.get("option_type", "") or trade.get("type", "")
        is_call = str(option_type).upper() == "CALL"
        
        if premium >= 100000:
            whale_type = "MEGA" if premium >= 500000 else "WHALE"
            whales.append({
                "type": whale_type, "option": "CALL" if is_call else "PUT",
                "strike": trade.get("strike", 0), "premium": premium,
                "volume": volume, "price": trade.get("price", trade.get("spot", 0)),
                "time": trade.get("time", trade.get("created_at", "")),
                "sentiment": trade.get("sentiment", "neutral"),
                "size": "LARGE" if premium >= 500000 else "MEDIUM"
            })
        
        if is_call:
            call_volume += volume; call_premium += premium
        else:
            put_volume += volume; put_premium += premium
    
    total_premium = call_premium + put_premium
    put_call_ratio = put_premium / total_premium if total_premium > 0 else 0.5
    
    return {
        "ticker": original_ticker, "actual_ticker": resolve_ticker(original_ticker),
        "whales": whales,
        "summary": {
            "put_call_ratio": round(put_call_ratio, 3),
            "call_ratio": round(1 - put_call_ratio, 3),
            "total_premium": total_premium, "call_premium": call_premium,
            "put_premium": put_premium, "call_volume": call_volume,
            "put_volume": put_volume, "whale_count": len(whales)
        },
        "timestamp": datetime.now().isoformat()
    }

def fetch_strike_data(ticker):
    try:
        headers = {"Authorization": f"Bearer {UNUSUAL_WHALES_API_KEY}"}
        actual_ticker = resolve_ticker(ticker)
        response = requests.get(f"{BASE_URL}/stock/{actual_ticker}/option-strikes", headers=headers, timeout=10)
        if response.status_code == 200:
            return process_strikes_data(response.json(), ticker)
        return {"error": f"API Error: {response.status_code}", "ticker": actual_ticker}
    except Exception as e:
        return {"error": str(e), "ticker": ticker}

def process_strikes_data(raw_data, original_ticker):
    strikes = {}; current_price = raw_data.get("current_price", 0)
    strikes_data = raw_data.get("strikes", []) if isinstance(raw_data, dict) else []
    
    for strike_data in strikes_data:
        strike = strike_data.get("strike", 0)
        call_oi = strike_data.get("call_open_interest", 0) or 0
        put_oi = strike_data.get("put_open_interest", 0) or 0
        call_volume = strike_data.get("call_volume", 0) or 0
        put_volume = strike_data.get("put_volume", 0) or 0
        total_oi = call_oi + put_oi; total_volume = call_volume + put_volume
        
        if total_oi > 0 or total_volume > 0:
            whale_score = (total_oi * 0.6 + total_volume * 0.4) / 1000
            strikes[str(strike)] = {
                "call_oi": call_oi, "put_oi": put_oi, "call_volume": call_volume,
                "put_volume": put_volume, "total_oi": total_oi, "total_volume": total_volume,
                "whale_score": round(whale_score, 2), "distance_from_price": abs(strike - current_price),
                "is_itm_call": strike < current_price, "is_itm_put": strike > current_price
            }
    
    sorted_strikes = dict(sorted(strikes.items(), key=lambda x: x[1]["whale_score"], reverse=True)[:20])
    return {
        "ticker": original_ticker, "actual_ticker": resolve_ticker(original_ticker),
        "strikes": sorted_strikes, "current_price": current_price,
        "timestamp": datetime.now().isoformat()
    }

class RequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global cache
        path = self.path; parsed = urllib.parse.urlparse(path)
        query = urllib.parse.parse_qs(parsed.query)
        ticker = query.get("ticker", ["SPY"])[0]
        ticker_cache = get_cache(ticker); now = time.time()
        
        if now - ticker_cache["last_update"] > CACHE_DURATION or ticker_cache["flow"] is None:
            ticker_cache["flow"] = fetch_options_flow(ticker)
            ticker_cache["strikes"] = fetch_strike_data(ticker)
            ticker_cache["last_update"] = now
        
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.end_headers()
        
        endpoint = parsed.path
        if "/whales" in endpoint:
            response = {"status": "success", "data": ticker_cache["flow"]}
        elif "/strikes" in endpoint:
            response = {"status": "success", "data": ticker_cache["strikes"]}
        elif "/combined" in endpoint:
            response = {
                "status": "success", "ticker": ticker,
                "whales": ticker_cache["flow"], "strikes": ticker_cache["strikes"],
                "update_interval": CACHE_DURATION,
                "next_update_in": round(CACHE_DURATION - (now - ticker_cache["last_update"]), 1)
            }
        else:
            response = {
                "status": "info", "supported_tickers": list(SUPPORTED_TICKERS.keys()),
                "usage": {"/api/combined?ticker=SPX": "SPX بيانات"}
            }
        
        self.wfile.write(json.dumps(response, ensure_ascii=False).encode('utf-8'))
    
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

def run_server():
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(('0.0.0.0', port), RequestHandler)
    print(f"🐳 Whale Server running on port {port}")
    server.serve_forever()

if __name__ == "__main__":
    run_server()

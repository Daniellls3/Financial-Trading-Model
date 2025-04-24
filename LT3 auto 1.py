# -*- coding: utf-8 -*-
"""
Created on Wed Apr  2 06:12:11 2025

@author: supre
"""

# -*- coding: utf-8 -*-
"""
Created on Wed Apr  2 02:42:36 2025

LT3 Tender Handling Algorithm

Automatically accepts or declines a tender order based on the following logic:
- Do not accept any tender in the last 30 seconds.
- For a SELL tender (institution selling to you):
    Accept (i.e. POST to the tender's endpoint) only if the last price is at least $0.05 higher 
    than the tender price.
- For a BUY tender (institution buying from you):
    Accept (i.e. POST to the tender's endpoint) only if the tender price is at least $0.05 higher 
    than the last price.

Otherwise, decline the tender by sending a DELETE to the tender's endpoint.

Before accepting a tender, ensure any open position is unwound (balanced to 0) using MARKET orders.
After tender acceptance, unwind the tender-induced position using LIMIT orders at the best bid/ask,
but only if market conditions are favorable relative to the tender price plus/minus the commission.

Refer to:
https://rit.306w.ca/RIT-REST-API/1.0.3/#/
for API instructions.
"""

import requests
from time import sleep
import signal
import json

# -------------------------------------------------------------------------------------
# Exception & Shutdown Handling
# -------------------------------------------------------------------------------------
class ApiException(Exception):
    pass

shutdown = False

def signal_handler(signum, frame):
    global shutdown
    # Restore default signal handler so Ctrl+C can still stop the program
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    shutdown = True

# -------------------------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------------------------
API_KEY = {'X-API-key': 'NYVYJ53X'}  # Replace with your actual API key
SLEEP_TIME = 0.2                   # Seconds between checks
UNWIND_CHUNK = 1500                # Shares per unwind order
LAST_SECONDS = 30                  # Don't accept tender if tick >= 300 - LAST_SECONDS
PRICE_THRESHOLD = 0.05             # $0.05 threshold for acceptance
COMMISSION = 0.02                  # Transaction fee per share (2 cents)

# -------------------------------------------------------------------------------------
# Helper Functions
# -------------------------------------------------------------------------------------

def get_tick(session):
    """Returns the current simulation tick."""
    resp = session.get("http://localhost:9999/v1/case")
    if resp.status_code == 401:
        raise ApiException("Invalid API key. Check your credentials.")
    case_info = resp.json()
    return case_info.get("tick", 0)

def check_position(session, ticker):
    """Returns the current position (int) for a given ticker."""
    params = {"ticker": ticker}
    resp = session.get("http://localhost:9999/v1/securities", params=params)
    if resp.status_code == 401:
        raise ApiException("Invalid API key. Check your credentials.")
    security = resp.json()[0]
    return security.get("position", 0)

def check_tender(session):
    """Returns the first active tender (dict) if available, else None."""
    resp = session.get("http://localhost:9999/v1/tenders")
    if resp.status_code == 401:
        raise ApiException("Invalid API key. Check your credentials.")
    tenders = resp.json()
    if tenders:
        return tenders[0]
    return None

def get_last_price(session, ticker):
    """Retrieves the 'last' price for the given ticker."""
    params = {"ticker": ticker}
    resp = session.get("http://localhost:9999/v1/securities", params=params)
    if resp.status_code != 200:
        print(f"Error retrieving securities info for {ticker}. Status code {resp.status_code}")
        return None
    try:
        security = resp.json()[0]
    except (IndexError, json.JSONDecodeError) as e:
        print("Error parsing last price for", ticker, e)
        return None
    return security.get("last", None)

def get_market_info(session, ticker):
    """
    Retrieves the order book for the given ticker.
    Returns a dict with keys: 'best_bid' and 'best_ask'.
    """
    params = {"ticker": ticker}
    resp = session.get("http://localhost:9999/v1/securities/book", params=params)
    if resp.status_code != 200:
        print(f"Error retrieving order book for {ticker}.")
        return {"best_bid": None, "best_ask": None}
    try:
        book = resp.json()
    except json.JSONDecodeError as e:
        print("JSON decode error while parsing order book:", e)
        return {"best_bid": None, "best_ask": None}

    bids = book.get("bids", [])
    asks = book.get("asks", [])
    best_bid = float(bids[0]["price"]) if (bids and "price" in bids[0]) else None
    best_ask = float(asks[0]["price"]) if (asks and "price" in asks[0]) else None
    return {"best_bid": best_bid, "best_ask": best_ask}

# -------------------------------------------------------------------------------------
# Unwinding Positions using MARKET Orders
# -------------------------------------------------------------------------------------

def unwind_position(session, ticker):
    """
    Unwinds the current position using MARKET orders in chunks of UNWIND_CHUNK.
    """
    pos = check_position(session, ticker)
    if pos > 0:
        # If long, sell
        qty = min(UNWIND_CHUNK, pos)
        payload = {
            "ticker": ticker,
            "type": "MARKET",
            "quantity": qty,
            "action": "SELL"
        }
        resp = session.post("http://localhost:9999/v1/orders", params=payload)
        if resp.ok:
            print(f"[UNWIND MARKET] Sold {qty} of {ticker}.")
        else:
            print("Market sell failed:", resp.json())
    elif pos < 0:
        # If short, buy
        qty = min(UNWIND_CHUNK, abs(pos))
        payload = {
            "ticker": ticker,
            "type": "MARKET",
            "quantity": qty,
            "action": "BUY"
        }
        resp = session.post("http://localhost:9999/v1/orders", params=payload)
        if resp.ok:
            print(f"[UNWIND MARKET] Bought {qty} of {ticker} to cover short.")
        else:
            print("Market buy failed:", resp.json())
    else:
        print(f"[UNWIND MARKET] {ticker} position is already 0.")

def ensure_balanced(session, ticker):
    """
    Loops until the ticker's position is 0, using MARKET orders.
    """
    pos = check_position(session, ticker)
    while pos != 0 and not shutdown:
        print(f"Balancing {ticker} with market orders. Position = {pos}")
        unwind_position(session, ticker)
        sleep(SLEEP_TIME)
        pos = check_position(session, ticker)
    print(f"{ticker} is balanced at 0 (market).")

# -------------------------------------------------------------------------------------
# Unwinding Positions using LIMIT Orders with Market Condition Checks
# -------------------------------------------------------------------------------------

def unwind_position_limit(session, ticker, cost, commission):
    """
    Unwinds the current position using LIMIT orders at best bid/ask,
    but only posts an order if the market offers a favorable price relative
    to the tender cost (cost) plus/minus commission.
    """
    pos = check_position(session, ticker)
    info = get_market_info(session, ticker)
    best_bid = info["best_bid"]
    best_ask = info["best_ask"]

    if pos > 0:
        # For long positions (we are selling):
        if best_ask is None:
            print("No best ask available. Cannot evaluate condition for limit sell.")
            return
        # Only post a sell limit order if the best ask is above cost + commission.
        if best_ask <= cost + commission:
            print(f"Not posting limit sell order because best ask ({best_ask:.2f}) <= tender cost+commission ({cost+commission:.2f}).")
            return
        qty = min(UNWIND_CHUNK, pos)
        payload = {
            "ticker": ticker,
            "type": "LIMIT",
            "quantity": qty,
            "price": best_bid,  # selling at best_bid
            "action": "SELL"
        }
        resp = session.post("http://localhost:9999/v1/orders", params=payload)
        if resp.ok:
            print(f"[UNWIND LIMIT] Sell {qty} of {ticker} at best_bid {best_bid:.2f}")
        else:
            print("Limit sell failed:", resp.json())

    elif pos < 0:
        # For short positions (we are buying to cover):
        if best_bid is None:
            print("No best bid available. Cannot evaluate condition for limit buy.")
            return
        # Only post a buy limit order if the best bid is below cost - commission.
        if best_bid >= cost - commission:
            print(f"Not posting limit buy order because best bid ({best_bid:.2f}) >= tender cost-commission ({cost-commission:.2f}).")
            return
        qty = min(UNWIND_CHUNK, abs(pos))
        payload = {
            "ticker": ticker,
            "type": "LIMIT",
            "quantity": qty,
            "price": best_ask,  # buying at best_ask
            "action": "BUY"
        }
        resp = session.post("http://localhost:9999/v1/orders", params=payload)
        if resp.ok:
            print(f"[UNWIND LIMIT] Buy {qty} of {ticker} at best_ask {best_ask:.2f}")
        else:
            print("Limit buy failed:", resp.json())
    else:
        print(f"[UNWIND LIMIT] {ticker} position is already 0.")

def ensure_balanced_limit(session, ticker, cost, commission):
    """
    Loops until the ticker's position is 0, using LIMIT orders at best bid/ask.
    Stops posting additional orders if market conditions indicate that further orders
    would not be favorable relative to the tender cost.
    """
    pos = check_position(session, ticker)
    while pos != 0 and not shutdown:
        print(f"Balancing {ticker} with limit orders. Position = {pos}")
        unwind_position_limit(session, ticker, cost, commission)
        sleep(SLEEP_TIME)
        pos = check_position(session, ticker)
    print(f"{ticker} is balanced at 0 (limit).")

# -------------------------------------------------------------------------------------
# Tender Acceptance/Decline
# -------------------------------------------------------------------------------------

def accept_tender(session, tender):
    """
    Accepts a tender by POST to /v1/tenders/{tender_id}.
    """
    t_id = tender.get("tender_id")
    if t_id is None:
        print("Cannot accept tender: no tender_id.")
        return
    url = f"http://localhost:9999/v1/tenders/{t_id}"
    resp = session.post(url)
    if resp.ok:
        print(f"Tender {t_id} accepted for {tender.get('ticker')}")
    else:
        print("Failed to accept tender:", resp.json())

def decline_tender(session, tender):
    """
    Declines a tender by DELETE to /v1/tenders/{tender_id}.
    """
    t_id = tender.get("tender_id")
    if t_id is None:
        print("Cannot decline tender: no tender_id. Possibly auto-declined.")
        return
    url = f"http://localhost:9999/v1/tenders/{t_id}"
    resp = session.delete(url)
    if resp.ok:
        print(f"Tender {t_id} declined for {tender.get('ticker')}")
    else:
        print("Failed to decline tender:", resp.json())

# -------------------------------------------------------------------------------------
# Main Trading Loop
# -------------------------------------------------------------------------------------

def main():
    global shutdown
    with requests.Session() as s:
        s.headers.update(API_KEY)
        tick = get_tick(s)
        print(f"Starting simulation at tick {tick}...")

        while not shutdown and tick > 5 and tick < 295:
            tender = check_tender(s)

            if tender is not None:
                ticker = tender.get("ticker", "UNKNOWN")
                print(f"Active tender detected for {ticker}: {tender}")

                # Check if we are in the last 30 seconds
                if tick >= 300 - LAST_SECONDS:
                    print("Within last 30 seconds. Declining tender.")
                    decline_tender(s, tender)

                else:
                    last_price = get_last_price(s, ticker)
                    if last_price is None:
                        print("No last price available; declining.")
                        decline_tender(s, tender)
                    else:
                        print(f"Last price for {ticker}: ${last_price:.2f}")
                        tender_price = tender.get("price", 0)
                        accept_flag = False

                        # SELL tender => institution sells to you => we buy if condition
                        if tender.get("action", "").upper() == "BUY":
                            if (last_price - tender_price) >= PRICE_THRESHOLD:
                                print(f"Criteria met for BUY tender: last={last_price:.2f}, tender={tender_price:.2f}")
                                accept_flag = True

                        # BUY tender => institution buys from you => we sell if condition
                        elif tender.get("action", "").upper() == "SELL":
                            if (tender_price - last_price) >= PRICE_THRESHOLD:
                                print(f"Criteria met for SELL tender: last={last_price:.2f}, tender={tender_price:.2f}")
                                accept_flag = True

                        if accept_flag:
                            # 1) Unwind current position with MARKET orders
                            ensure_balanced(s, ticker)

                            # 2) Accept the tender
                            accept_tender(s, tender)

                            # 3) Unwind the newly acquired position with LIMIT orders,
                            #     using tender price (cost) and commission to check market conditions.
                            ensure_balanced_limit(s, ticker, tender_price, COMMISSION)

                        else:
                            print("Tender does not meet criteria; declining.")
                            decline_tender(s, tender)

            else:
                # No tender => ensure CRZY/TAME positions remain at 0 with MARKET unwinds
                for tkr in ["CRZY", "TAME"]:
                    pos = check_position(s, tkr)
                    if pos != 0:
                        print(f"No active tender, but {tkr} position is {pos}. Unwinding (market).")
                        ensure_balanced(s, tkr)
                sleep(SLEEP_TIME)

            # Refresh tick at end of loop
            tick = get_tick(s)
            print(f"Tick updated: {tick}")

        print("Trading period ended or shutdown requested.")

# -------------------------------------------------------------------------------------
# Run the Script
# -------------------------------------------------------------------------------------
if __name__ == '__main__':
    signal.signal(signal.SIGINT, signal_handler)
    main()

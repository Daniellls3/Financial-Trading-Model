# -*- coding: utf-8 -*-
"""
Created on Wed Mar 26 05:40:58 2025

@author: supre
"""

# -*- coding: utf-8 -*-
"""
Created on Wed Mar 26 05:10:22 2025

@author: supre
"""

import signal
import requests
import time
from time import sleep

class ApiException(Exception):
    pass

def signal_handler(signum, frame):
    global shutdown
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    shutdown = True

# =============================================================================
# USER PARAMETERS (Adjust as needed)
# =============================================================================
API_KEY = {'X-API-Key': 'NYVYJ53X'}  # Replace with your actual API key

# Market-making parameters
# Adjusted to capture a larger effective spread:
SPREAD = 0.03          # New spread offset (in dollars)
BUY_VOLUME = 1000        # Reduced volume per BUY order
SELL_VOLUME = 1000       # Reduced volume per SELL order
POSITION_THRESHOLD = 500 # Maximum net position before corrective action

# Speed-bump parameters
ORDER_LIMIT = 5          # Target orders per second
DESIRED_DELAY = 1.0 / ORDER_LIMIT

# Sleep time between main loop checks (in seconds)
LOOP_SLEEP = 0.2

# =============================================================================
# GLOBALS for Speed Bump
# =============================================================================
shutdown = False
placed_orders = 0
total_speedbumps = 0.0
total_transaction_time = 0.0

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_tick(session):
    """
    Returns the current tick/time in the simulation.
    """
    resp = session.get('http://localhost:9999/v1/case')
    if not resp.ok:
        raise ApiException(f"Failed to get case info: {resp.status_code} {resp.reason}")
    return resp.json()['tick']

def get_last_close(session, ticker):
    """
    Returns the last close price for the given ticker from the /v1/securities/history endpoint.
    Falls back to 0 if no data is found.
    """
    params = {'ticker': ticker, 'limit': 1}
    resp = session.get('http://localhost:9999/v1/securities/history', params=params)
    if not resp.ok:
        raise ApiException(f"Failed to get history for {ticker}: {resp.status_code} {resp.reason}")
    history = resp.json()
    if history:
        return history[0]['close']
    return 0

def get_open_orders(session):
    """
    Returns a list of all open orders from the /v1/orders endpoint.
    """
    params = {'status': 'OPEN'}
    resp = session.get('http://localhost:9999/v1/orders', params=params)
    if not resp.ok:
        raise ApiException(f"Failed to get open orders: {resp.status_code} {resp.reason}")
    return resp.json()

def get_position(session, ticker):
    """
    Computes net position by summing filled (transacted) orders for the given ticker.
    Net Position = Sum(BUY filled quantities) - Sum(SELL filled quantities)
    This approach avoids the issue where /v1/trader returns a net position of 0.
    """
    params = {'status': 'TRANSACTED', 'ticker': ticker}
    resp = session.get('http://localhost:9999/v1/orders', params=params)
    if not resp.ok:
        raise ApiException(f"Failed to get transacted orders: {resp.status_code} {resp.reason}")
    orders = resp.json()
    net = 0
    for order in orders:
        filled = order.get('quantity_filled', 0)
        if order.get('action') == 'BUY':
            net += filled
        elif order.get('action') == 'SELL':
            net -= filled
    return net

def cancel_all_orders(session):
    """
    Cancels all open orders.
    """
    resp = session.post('http://localhost:9999/v1/commands/cancel', params={'all': 1})
    if resp.ok:
        cancelled_ids = resp.json().get('cancelled_order_ids', [])
        print("Cancelled orders:", cancelled_ids)
    else:
        print("Order cancellation failed:", resp.json())

# -----------------------------------------------------------------------------
# SPEED BUMP LOGIC
# -----------------------------------------------------------------------------

def dynamic_speedbump(transaction_time):
    """
    Dynamically adjust how long we sleep to maintain the target ORDER_LIMIT.
    We measure each order's transaction time, then compute a 'speed bump' so that, on average,
    we stay near ORDER_LIMIT orders/second.
    """
    global placed_orders, total_speedbumps, total_transaction_time

    order_speedbump = DESIRED_DELAY - transaction_time
    total_speedbumps += order_speedbump
    total_transaction_time += transaction_time
    placed_orders += 1

    avg_speedbump = total_speedbumps / placed_orders
    if avg_speedbump > 0:
        sleep(avg_speedbump)
    return order_speedbump, avg_speedbump

def place_order(session, payload):
    """
    Places a single order using the given payload. Measures transaction time,
    applies dynamic speed bump, and returns the response.
    """
    start_time = time.time()
    resp = session.post('http://localhost:9999/v1/orders', params=payload)
    transaction_time = time.time() - start_time

    current_sb, avg_sb = dynamic_speedbump(transaction_time)

    if resp.ok:
        print(f"Order placed: {payload['action']} {payload['quantity']}@{payload.get('price','MKT')} | "
              f"TxTime={transaction_time:.4f}s | SB={current_sb:.4f}s | AvgSB={avg_sb:.4f}s")
    else:
        try:
            error_data = resp.json()
        except:
            error_data = resp.text
        print(f"Order error: {error_data}")
    return resp

# -----------------------------------------------------------------------------
# ALGO2 MARKET MAKING LOGIC
# -----------------------------------------------------------------------------

def submit_order_pair(session, last_price):
    """
    Submits a BUY and a SELL limit order around the last_price ± SPREAD.
    With the new parameters, the effective profit per share increases.
    """
    buy_price = last_price - SPREAD
    sell_price = last_price + SPREAD

    buy_payload = {
        'ticker': 'ALGO',
        'type': 'LIMIT',
        'quantity': BUY_VOLUME,
        'action': 'BUY',
        'price': buy_price
    }
    sell_payload = {
        'ticker': 'ALGO',
        'type': 'LIMIT',
        'quantity': SELL_VOLUME,
        'action': 'SELL',
        'price': sell_price
    }

    place_order(session, buy_payload)
    place_order(session, sell_payload)

def main():
    global shutdown
    with requests.Session() as s:
        s.headers.update(API_KEY)
        tick = get_tick(s)

        print("Starting ALGO2 with dynamic speed bump & position control...")

        while tick > 5 and tick < 295 and not shutdown:
            try:
                net_position = get_position(s, 'ALGO')
                orders = get_open_orders(s)
                last_price = get_last_close(s, 'ALGO')

                print(f"Tick: {tick} | Net Pos: {net_position} | Open Orders: {len(orders)}")

                # If net position is too long, place SELL to reduce
                if net_position > POSITION_THRESHOLD:
                    print("Position too long, reducing inventory...")
                    cancel_all_orders(s)
                    sell_payload = {
                        'ticker': 'ALGO',
                        'type': 'LIMIT',
                        'quantity': SELL_VOLUME,
                        'action': 'SELL',
                        'price': last_price + SPREAD
                    }
                    place_order(s, sell_payload)

                # If net position is too short, place BUY to cover
                elif net_position < -POSITION_THRESHOLD:
                    print("Position too short, covering...")
                    cancel_all_orders(s)
                    buy_payload = {
                        'ticker': 'ALGO',
                        'type': 'LIMIT',
                        'quantity': BUY_VOLUME,
                        'action': 'BUY',
                        'price': last_price - SPREAD
                    }
                    place_order(s, buy_payload)

                else:
                    # Position is within threshold
                    if len(orders) == 0:
                        print("No open orders, submitting a pair...")
                        submit_order_pair(s, last_price)
                    elif len(orders) != 2:
                        print("Unbalanced orders, resetting...")
                        cancel_all_orders(s)
                    else:
                        print("Balanced pair in market; continuing...")

                sleep(LOOP_SLEEP)
                tick = get_tick(s)

            except ApiException as e:
                print("API Error:", e)
                break

        if placed_orders > 0:
            avg_tx_time = total_transaction_time / placed_orders
            avg_sb = total_speedbumps / placed_orders
            print("\n=== FINAL SPEED BUMP STATS ===")
            print(f"Orders Placed      : {placed_orders}")
            print(f"Avg TransactionTime: {avg_tx_time:.4f}s")
            print(f"Avg SpeedBump Delay: {avg_sb:.4f}s")
        else:
            print("No orders placed; no stats available.")

if __name__ == '__main__':
    signal.signal(signal.SIGINT, signal_handler)
    main()

# -*- coding: utf-8 -*-
"""
Created on Wed Mar 26 05:04:03 2025

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

# Replace with your actual API key
API_KEY = {'X-API-Key': 'NYVYJ53X'}
shutdown = False

# Test parameters
order_limit = 5          # Target orders per second (desired rate)
max_size = 1000          # Shares per order
target_total_volume = 20000  # Total shares to trade in this test
num_orders = target_total_volume // max_size  # Total number of orders to submit

# Global counters for logging
placed_orders = 0
total_speedbumps = 0.0   # Sum of speed bump delays applied
total_transaction_time = 0.0  # Sum of observed transaction times

def speedbump(transaction_time):
    """
    Computes and applies a dynamic delay (speed bump) so that orders
    are spaced out to approximately achieve the target order_limit.
    Returns the current order's speed bump and average speed bump so far.
    """
    global total_speedbumps, placed_orders, total_transaction_time

    # Calculate the desired delay (seconds per order)
    desired_delay = 1.0 / order_limit
    # Difference between desired delay and actual transaction time
    order_speedbump = desired_delay - transaction_time

    total_speedbumps += order_speedbump
    total_transaction_time += transaction_time
    placed_orders += 1

    avg_speedbump = total_speedbumps / placed_orders

    if avg_speedbump > 0:
        sleep(avg_speedbump)

    return order_speedbump, avg_speedbump

def main():
    global placed_orders
    with requests.Session() as s:
        s.headers.update(API_KEY)
        print("Starting speed bump test for ALGO2...")

        while placed_orders < num_orders and not shutdown:
            start_time = time.time()
            
            # Submit a simple LIMIT BUY order for testing
            buy_payload = {
                'ticker': 'ALGO',
                'type': 'LIMIT',
                'quantity': max_size,
                'price': 19,  # Test price (adjust as needed)
                'action': 'BUY'
            }
            resp = s.post('http://localhost:9999/v1/orders', params=buy_payload)
            
            if resp.ok:
                transaction_time = time.time() - start_time
                current_sb, avg_sb = speedbump(transaction_time)
                print(f"Order #{placed_orders:3d}: Transaction Time = {transaction_time:.4f} s | "
                      f"Current Speedbump = {current_sb:.4f} s | Average Speedbump = {avg_sb:.4f} s")
            else:
                try:
                    error_data = resp.json()
                except Exception:
                    error_data = resp.text
                print(f"Error placing order: {error_data}")
                break

        if placed_orders > 0:
            avg_transaction_time = total_transaction_time / placed_orders
            avg_speedbump_final = total_speedbumps / placed_orders
            print("\n=== Speed Bump Test Summary ===")
            print(f"Total Orders Placed      : {placed_orders}")
            print(f"Average Transaction Time : {avg_transaction_time:.4f} s")
            print(f"Average Speedbump Delay  : {avg_speedbump_final:.4f} s")
        else:
            print("No orders were placed during the test.")

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    main()

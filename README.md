# Financial-Trading-Model
Liability Trading, Algorithmic Arbitrage, Speed bump test

Liability trading:
Automatically accepts or declines a tender order based on the following logic:
- Do not accept any tender in the last 30 seconds.
- For a SELL tender (institution selling to you):
    Accept (i.e. POST to the tender's endpoint) only if the last price is at least $0.05 higher 
    than the tender price.
- For a BUY tender (institution buying from you):
    Accept (i.e. POST to the tender's endpoint) only if the tender price is at least $0.05 higher 
    than the last price.
Otherwise, decline the tender by sending a DELETE to the tender's endpoint.

Algorithmic Arbitrage trading:
The target of the trading execution is to submit a paired bid and offer and have the two orders filled over time; 
the trader (algorithm) earns the price differential. 
When markets aren’t trending, this is a reasonably effective strategy.
- Constantly check to see if you have any orders in the order book. If you do not, then you
should submit your bid and ask. If you only have one order in the order book, cancel it.
- Check your inventory and adjust your bid/ask prices or quantities to try to balance your
inventory.

Speed Bump Test:
- Each time we submit an order we will first calculate the ‘transaction time’. The ‘transaction time’ is
how long it takes an order to get submitted successfully to the market. We then calculate our speed
bump by determining how long of a speed bump is needed between each order for us to submit the
maximum orders per second given our ‘transaction time’.

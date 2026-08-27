import trade.config as cfg
from trade.kalshi_client import fill_fee


def on_serve(score, p1_serves):
    """True while play is unbroken — the state the receiver-roll strategy trades.

    `score` is (p1, p2): games in the current set, or POINTS when a tiebreak is
    in progress. The same two conditions decide both, because the invariant is
    the same. Play is on serve when the gap is at most MAX_GAME_DIFF and the
    player about to serve is not ahead.

    Games: with no breaks the first server holds ceil(n/2) after n games and the
    second floor(n/2), so the next server is either level (n even) or exactly one
    down (n odd). A leading server can only have got there by breaking.

    Tiebreaks: serve goes 1 point then 2 at a time, but the player who takes a
    2-point block also gives one away, so an unbroken tiebreak runs
    1-0, 1-1, 1-2, 2-2, 3-2, 3-3 ... and the upcoming server is never ahead
    there either. So a mini-break always shows up as off-serve, and no separate
    mini-break detection is needed.
    """
    if score is None:
        return False
    p1, p2 = score
    if abs(p1 - p2) > cfg.MAX_GAME_DIFF:
        return False
    server, receiver = (p1, p2) if p1_serves else (p2, p1)
    return server <= receiver


def compute_entry(price, budget_remaining, ticker):
    """
    Fixed-size entry: buy CONTRACTS_PER_TRADE of the receiver at `price`.

    No edge test and no Kelly — while the set is on serve, every game boundary
    trades. Returns None only when the price is unusable or the budget cannot
    cover the stake plus the fill fee, which is charged on top and depends on
    the execution mode and the market's series.
    """
    if not 0 < price < 1:
        return None
    count = float(cfg.CONTRACTS_PER_TRADE)
    needed = count * price + fill_fee(count, price, ticker)
    if needed > budget_remaining:
        return None
    return {
        "price_cents": round(price * 100),
        "count":       count,
        "entry_price": price,
        "cost":        count * price,
    }

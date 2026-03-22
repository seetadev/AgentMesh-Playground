"""View transaction history from SQLite databases."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common.db import init_alpha_db, init_trader_db, get_recent_transactions


def main():
    import argparse
    parser = argparse.ArgumentParser(description="View AOIN transaction history")
    parser.add_argument("agent", choices=["alpha", "trader"], help="Which agent's history")
    parser.add_argument("-n", "--limit", type=int, default=20, help="Number of records")
    args = parser.parse_args()

    if args.agent == "alpha":
        conn = init_alpha_db()
    else:
        conn = init_trader_db()

    txs = get_recent_transactions(conn, args.limit)

    if not txs:
        print("No transactions found.")
        return

    print(f"\n{'='*80}")
    print(f"  {args.agent.upper()} AGENT — Last {len(txs)} transactions")
    print(f"{'='*80}")

    for tx in txs:
        status_icon = "OK" if tx["status"] == "success" else "FAIL"
        print(f"\n  [{status_icon}] {tx['timestamp']}")
        print(f"  Asset: {tx['asset']}")

        if tx.get("tx_hash"):
            print(f"  TX:    {tx['tx_hash']}")

        if args.agent == "alpha":
            if tx.get("payer_address"):
                print(f"  Payer: {tx['payer_address']}")
        else:
            if tx.get("price"):
                print(f"  Price: ${tx['price']}")

        if tx.get("direction"):
            print(f"  Signal: {tx['direction']} ({tx.get('confidence', '?')}%)")

        if tx.get("error"):
            print(f"  Error: {tx['error']}")

    print(f"\n{'='*80}\n")


if __name__ == "__main__":
    main()

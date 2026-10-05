import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cellarbrations import scrape_with_playwright, write_csv


def main():
    parser = argparse.ArgumentParser(
        description="Scrape Cellarbrations whisky (all pages + descriptions)"
    )
    parser.add_argument("--max-products", type=int, default=None)
    parser.add_argument(
        "--no-descriptions",
        action="store_true",
        help="Skip description extraction (faster)",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show the browser window (pretty much required for Cloudflare + descriptions)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/cellarbrations_whisky.csv"),
    )
    args = parser.parse_args()

    if not args.headed:
        print(
            "WARNING: without --headed, Cloudflare often blocks pages and "
            "descriptions stay empty. Prefer: --headed",
            file=sys.stderr,
        )

    print(
        "Cellarbrations scraper: Playwright + pagination + product descriptions.\n"
        "  Use --headed on a home network for best results.",
        file=sys.stderr,
    )

    try:
        rows = scrape_with_playwright(
            max_products=args.max_products,
            fetch_descriptions=not args.no_descriptions,
            headless=not args.headed,
        )
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1

    try:
        write_csv(rows, args.output)
    except PermissionError as e:
        fallback = args.output.with_name(
            f"{args.output.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
            f"{args.output.suffix or '.csv'}"
        )
        try:
            write_csv(rows, fallback)
        except PermissionError as fallback_error:
            print(
                f"Cannot write output file {args.output}: {e}. "
                f"Fallback file {fallback} is also not writable: {fallback_error}",
                file=sys.stderr,
            )
            return 1
        print(
            f"Cannot overwrite {args.output}; saved to {fallback} instead. "
            "Close the original file to overwrite it on the next run.",
            file=sys.stderr,
        )
        args.output = fallback

    with_desc = sum(1 for r in rows if (r.get("description") or "").strip())
    print(
        f"Wrote {len(rows)} rows to {args.output} ({with_desc} with description)",
        file=sys.stderr,
    )
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
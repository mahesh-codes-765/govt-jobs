import argparse, json
from app.logging_config import setup_logging
from app.db import init_db
from app.services.crawler import crawl

setup_logging()

p=argparse.ArgumentParser(description="Crawl official government recruitment notifications")
p.add_argument('--source', default='tgpsc')
g=p.add_mutually_exclusive_group()
g.add_argument('--year', type=int, help='Only ingest documents associated with this year')
g.add_argument('--all-years', action='store_true', help='Ingest all years discoverable from the official source')
args=p.parse_args(); init_db()
print(json.dumps(crawl(args.source, year=args.year, all_years=args.all_years), indent=2, default=str))

import sys
from config import *
from src.db import Database
from src.web.load import load

async def main(args):
	db = Database()
	await db.connect(db_path)

	await load(db, force_flags=args.force)


if __name__ == "__main__":
	import asyncio
	import argparse
	import logging

	args_parser = argparse.ArgumentParser()
	args_parser.add_argument("--log-level", default="INFO")
	args_parser.add_argument("--force", default=[], metavar="load_flag", nargs="+", type=str)
	args = args_parser.parse_args()

	logging.basicConfig(level=logging.WARNING)

	logger = logging.getLogger(__name__)
	try:
		logger.setLevel(args.log_level)
		logging.getLogger("src.web.load").setLevel(args.log_level)
	except ValueError:
		logger.fatal(f"Invalid log level {args.log_level}")
		sys.exit(1)

	try:
		asyncio.run(main(args))
	except KeyboardInterrupt:
		logger.info("Exiting")


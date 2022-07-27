import sys
from config import *
from src.db import Database
from src.web.load import load

async def main(args):
	db = Database()
	await db.connect(db_path)

	await load(db)


if __name__ == "__main__":
	import asyncio
	import argparse
	import logging

	args_parser = argparse.ArgumentParser()
	args_parser.add_argument("--log_level", default="INFO")
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


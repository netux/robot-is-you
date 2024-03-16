from __future__ import annotations

import asyncio
import base64
import logging
import math
import re
import tempfile
import dataclasses
from pathlib import Path
from datetime import datetime
from typing import Optional

import quart
from quart import render_template, request, make_response, send_from_directory
from markupsafe import Markup, escape

from config import *
from src import errors
from src.web.load import load
from src.web.util import RenderTilesOptions, render_tiles
from src.web.context import get_database, get_operation_macros, get_variant_handlers, teardown_appcontext
from src.web.middleware.path_prefix import RoutePrefixMiddleware

root_path = Path(__file__).parent.resolve()

app = quart.Quart(__name__)

if webapp_route_prefix:
	app.asgi_app = RoutePrefixMiddleware(app.asgi_app, prefix=webapp_route_prefix)

@app.teardown_appcontext
async def teardown(err):
	await teardown_appcontext(err)

DISCORD_MARKDOWN_INLINE_CODE_REGEXP: re.Pattern = re.compile(r"`([^`]+?)`")

@app.template_filter("replace_discord_markdown")
async def template_filter__replace_discord_markdown(value: str):
	"""
	Helper to render some very simple markdown from strings originally made for ROBOT.

	! THIS IS NAIVE AND UNSAFE. DO NOT PROVIDE USER INPUT. !

	Implemented so far:
	- `inline-code` => <code>inline-code</code>
	"""

	value = escape(value)
	value = DISCORD_MARKDOWN_INLINE_CODE_REGEXP.sub(lambda m: f"<code>{m[1]}</code>", value)
	return Markup(value)

load_done = False

def not_ready_fallback(f):
	async def wrapper(*args, **kwargs):
		global load_done

		if load_done:
			return await f(*args, **kwargs)
		else:
			return { "error": "Woah there! We are loading the resources needed to make this site work, and will get back to you in a bit." }, 503
	return wrapper


@dataclasses.dataclass(frozen=True)
class GeneratedTiles:
	input_hash: int
	tmp: tempfile.TemporaryFile
	generated_at: datetime = dataclasses.field(default_factory=lambda: datetime.now())

	@property
	def result_url_hash(self) -> str:
		input_hash_bytes = self.input_hash.to_bytes(length=8, byteorder="big", signed=True)
		return base64.urlsafe_b64encode(input_hash_bytes).decode("utf-8")

	@property
	def expires_at(self) -> datetime:
		return self.generated_at + webapp_max_result_life

	def __hash__(self):
		return self.input_hash

	@staticmethod
	def result_url_hash_to_input_hash(result_url_hash: str):
		hash_bytes = base64.urlsafe_b64decode(result_url_hash.encode("utf-8"))
		input_hash = int.from_bytes(hash_bytes, byteorder="big", signed=True)
		return input_hash

input_hash_to_generated_tiles_map: dict[int, GeneratedTiles] = {}

scheduled_to_remove: set[str] = set()

@app.route("/results/<string:result_url_hash>.gif", methods=["GET"])
async def results(result_url_hash: str):
	input_hash = GeneratedTiles.result_url_hash_to_input_hash(result_url_hash)
	generated_tiles = input_hash_to_generated_tiles_map.get(input_hash, None)

	if generated_tiles is None:
		response = await make_response({ "error": "Unknown hash" })
		response.status_code = 400
		return response


	blob = generated_tiles.tmp.read()
	generated_tiles.tmp.seek(0)
	scheduled_to_remove.add(input_hash)

	response = await make_response(blob)
	response.content_type = "image/gif"
	response.cache_control.max_age = math.floor((generated_tiles.expires_at - datetime.now()).total_seconds())
	return response

@dataclasses.dataclass(frozen=True)
class WebappRenderTilesOptions:
	use_bg: Optional[bool] = None
	bg_tx: Optional[int] = None
	bg_ty: Optional[int] = None
	palette: Optional[str] = None
	default_to_letters: Optional[bool] = None
	delay: Optional[int] = None
	frame_count: Optional[int] = None

	def to_base_options(self):
		opts = {}
		if self.use_bg:
			opts["background"] = (
				self.bg_tx if self.bg_tx is not None else 1,
				self.bg_ty if self.bg_ty is not None else 4
			)
		if self.palette is not None:
			opts["palette"] = self.palette
		if self.default_to_letters is not None:
			opts["default_to_letters"] = self.default_to_letters
		if self.delay is not None:
			opts["delay"] = self.delay
		if self.frame_count is not None:
			opts["frame_count"] = self.frame_count

		return RenderTilesOptions(**opts)

@app.route("/api/list/variants")
async def list_variants():
	all_variants = (await get_variant_handlers()).handlers
	grouped_variants: dict[str, list[str]] = {}
	for variant in all_variants:
		group = variant.group or "Uncategorized"
		if group not in grouped_variants:
			grouped_variants[group] = []
		grouped_variants[group].extend(variant.hints.values())
	return await render_template("list_variants.html",
		variants=grouped_variants
	)

@app.route("/api/list/operations")
async def list_operations():
	operation_macros = await get_operation_macros()
	return operation_macros.get_all()

@app.route("/api/text", methods=["POST"])
@not_ready_fallback
async def text():
	generated_tiles = None
	body_json: dict = await request.get_json(force=True)
	prompt = body_json.pop("prompt", None)
	options = WebappRenderTilesOptions(**body_json)
	error_msg = None
	status_code = 200

	if prompt is not None:
		input_hash = hash((prompt, options))

		if input_hash not in input_hash_to_generated_tiles_map:
			try:
				r = await render_tiles(prompt, is_rule=True, options=options.to_base_options())
				buffer = r.buffer
			except errors.WebappUserError as err:
				error_msg = err.args[0]
				status_code = 400
			else:
				tmp = tempfile.TemporaryFile("br+")
				tmp.write(buffer.read())
				tmp.seek(0)

				generated_tiles = GeneratedTiles(
					input_hash=input_hash,
					tmp=tmp
				)
				input_hash_to_generated_tiles_map[input_hash] = generated_tiles
		else:
			generated_tiles = input_hash_to_generated_tiles_map[input_hash]
	else:
		return { "error": "missing 'prompt' in request body" }, 400

	if error_msg is not None:
		return { "error": error_msg }, status_code

	return {
		'prompt': prompt,
		'resultURLHash': generated_tiles.result_url_hash
	}

# Reverse proxy to frontend
if quart.helpers.get_debug_flag():
	import httpx
	frontend_reverse_proxy_client = httpx.AsyncClient(base_url=f"http://{webapp_frontend_host}/")

	@app.route("/", methods=["GET"])
	@app.route("/<path:path>", methods=["GET"])
	async def frontend_reverse_proxy(path: str = "index.html"):
		# Based on https://stackoverflow.com/a/74556972

		# TODO(netux): 	The websocket Svelte uses for hot reloading is definitely not going to work with this setup
		# 							Find a reverse proxy middleware for Flask/Quart, or implement websockets myself somehow?

		url = httpx.URL(path=path, query=request.query_string)

		frontend_request = frontend_reverse_proxy_client.build_request(
			request.method,
			url,
			headers=dict(request.headers),
		)
		frontend_response = await frontend_reverse_proxy_client.send(frontend_request, stream=True)

		async def stream_response():
			async for chunk in frontend_response.aiter_raw():
				yield chunk
			await frontend_response.aclose()

		return (stream_response(), frontend_response.status_code, dict(frontend_response.headers))
else:
	@app.route("/", methods=["GET"])
	@app.route("/<path:path>", methods=["GET"])
	async def frontend_serve_static(path: str = "index.html"):
		return await send_from_directory(
			directory=str(root_path / "frontend/dist"),
			file_name=path
		)

async def do_load():
	global load_done

	app.logger.debug("Loading..")

	load_done = False
	async with app.app_context():
		db = await get_database()
		await load(db)
	load_done = True

	app.logger.debug("Load done")

async def remove_scheduled_loop():
	global scheduled_to_remove

	while True:
		await asyncio.sleep(1)

		now = datetime.now()

		new_scheduled_to_remove = { *scheduled_to_remove }

		for input_hash in scheduled_to_remove:
			generated_tile = input_hash_to_generated_tiles_map[input_hash]
			if now > generated_tile.expires_at:
				app.logger.info(f"Removing {repr(generated_tile)}")
				input_hash_to_generated_tiles_map.pop(input_hash)
				generated_tile.tmp.close()

				new_scheduled_to_remove.remove(input_hash)

		scheduled_to_remove = new_scheduled_to_remove

if __name__ == "__main__":
	loop = asyncio.new_event_loop()
	asyncio.set_event_loop(loop)

	loop.create_task(do_load())
	loop.create_task(remove_scheduled_loop())

	app.logger.setLevel(
		logging.DEBUG
		if quart.helpers.get_debug_flag()
		else logging.INFO
	)

	try:
		# TODO(netux): With this setup, the reloader in debug mode just exits the process.
		# Figure out a way to reload the module instead.
		app.run(
			host=webapp_host,
			port=webapp_port,
			loop=loop,
			use_reloader=False
		)
	except KeyboardInterrupt:
		pass
	finally:
		app.logger.info("Shutting down...")
		loop.stop()
		loop.close()


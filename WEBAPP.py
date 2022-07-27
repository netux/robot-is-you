from __future__ import annotations

import asyncio
import base64
import logging
import math
import re
import tempfile
import dataclasses
from datetime import datetime
from typing import Optional

import quart
from quart import render_template, request, make_response
from markupsafe import Markup, escape

from config import *
from src import errors
from src.web.load import load
from src.web.util import RenderTilesOptions, render_tiles
from src.web.context import get_database, get_operation_macros, get_variant_handlers, teardown_appcontext
from src.web.middleware.path_prefix import RoutePrefixMiddleware

app = quart.Quart(__name__,
	template_folder="src/web/templates",
	static_folder="src/web/static"
)

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
			return await render_template("loading.html")
	return wrapper


def coerce_request_arg_to_bool(value: str | None) -> bool | None:
	# None => False
	# len(value) > 0 => True
	# len(value) == 0 => False
	return bool(value)

def coerce_request_arg_to_int(value: str | None) -> int | None:
	# None => None
	# "<integer>" => <integer>
	# "<integer>.<decimals>" => <integer>
	# "<non-number>" => None
	try:
		return int(value) if value is not None else None
	except ValueError:
		return None

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
		response = await make_response("Unknown hash")
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

	@classmethod
	def from_request(Cls, request: quart.Request):
		get = lambda name: request.args.get(name, None)

		return Cls(
			use_bg=coerce_request_arg_to_bool(get("use_bg")),
			bg_tx=coerce_request_arg_to_int(get("bg_tx")),
			bg_ty=coerce_request_arg_to_int(get("bg_ty")),
			palette=get("palette"),
			default_to_letters=coerce_request_arg_to_bool(get("default_to_letters")),
			delay=coerce_request_arg_to_int(get("delay")),
			frame_count=coerce_request_arg_to_int(get("frame_count"))
		)

@app.route("/list/variants")
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

@app.route("/list/operations")
async def list_operations():
	operation_macros = await get_operation_macros()
	return await render_template("list_operations.html",
		operations=operation_macros.get_all()
	)

@app.route("/text", methods=["GET", "POST"])
@not_ready_fallback
async def text():
	generated_tiles = None
	prompt = request.args.get("prompt", None)
	options = WebappRenderTilesOptions.from_request(request)
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

	response = await make_response(await render_template("text.html",
		prompt=prompt,
		generated_tiles=generated_tiles,
		options=options,
		error_msg=error_msg
	))
	response.status_code = status_code
	return response

async def do_load():
	global load_done

	load_done = False
	async with app.app_context():
		db = await get_database()
		await load(db)
	load_done = True

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


from __future__ import annotations

import asyncio
import uuid
import tempfile
import dataclasses
from datetime import datetime, timedelta
from typing import Optional

import quart
from quart import Quart, render_template, request, make_response

from src import errors
from src.web.load import load
from src.web.util import RenderTilesOptions, render_tiles
from src.web.context import teardown_appcontext

app = Quart(__name__, template_folder="src/web/templates", static_folder="src/web/static")
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

@dataclasses.dataclass
class GeneratedTiles:
	prompt: str
	uuid: str
	tmp: tempfile.TemporaryFile
	generated_at: datetime = dataclasses.field(default_factory=lambda: datetime.now())

	def __hash__(self) -> int:
		return self.uuid.__hash__()

uuid_to_generated_tiles_map: dict[uuid.UUID, GeneratedTiles] = {}
prompt_to_last_uuid_map: dict[str, uuid.UUID] = {}

remove_scheduled: set[GeneratedTiles] = set()

@app.route("/results/<uuid:uuid>", methods=["GET"])
async def results(uuid: str):
	generated_tile = uuid_to_generated_tiles_map.get(uuid, None)

	if generated_tile is None:
		response = await make_response("Invalid UUID")
		response.status_code = 400
		return response


	blob = generated_tile.tmp.read()
	generated_tile.tmp.seek(0)
	remove_scheduled.add(generated_tile)

	response = await make_response(blob)
	response.headers.add('Content-Type', 'image/gif')
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
			opts["background"] = (self.bg_tx, self.bg_ty)
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

@app.route("/text", methods=["GET", "POST"])
@not_ready_fallback
async def text():
	generated_tiles = None
	prompt = request.args.get("objects", None)
	options = WebappRenderTilesOptions.from_request(request)
	error_msg = None
	status_code = 200

	if prompt is not None:
		if prompt not in prompt_to_last_uuid_map or prompt_to_last_uuid_map[prompt] not in uuid_to_generated_tiles_map:
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
					prompt=prompt,
					uuid=uuid.uuid4(),
					tmp=tmp
				)
				uuid_to_generated_tiles_map[generated_tiles.uuid] = generated_tiles
				prompt_to_last_uuid_map[prompt] = generated_tiles.uuid
		else:
			generated_tiles = uuid_to_generated_tiles_map[prompt_to_last_uuid_map[prompt]]

	response = await make_response(await render_template("text.html",
		objects=prompt,
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
		await load()
	load_done = True

async def remove_scheduled_loop():
	global remove_scheduled

	# TODO(netux): make configurable
	MAX_LIFE = timedelta(hours=1)

	while True:
		await asyncio.sleep(1)

		now = datetime.now()

		for generated_tile in remove_scheduled:
			if (generated_tile.generated_at + MAX_LIFE) > now:
				print("Removing", generated_tile)
				uuid_to_generated_tiles_map.pop(generated_tile.uuid)
				prompt_to_last_uuid_map.pop(generated_tile.prompt)
				generated_tile.tmp.close()
				remove_scheduled.pop(generated_tile)

if __name__ == "__main__":
	loop = asyncio.new_event_loop()

	loop.create_task(do_load())
	loop.create_task(remove_scheduled_loop())

	@app.teardown_appcontext
	async def teardown(err):
		await teardown_appcontext(err)

	try:
		# TODO(netux): With this setup, the reloader in debug mode just exits the process.
		# Figure out a way to reload the module instead.
		app.run(loop=loop, use_reloader=False)
	except KeyboardInterrupt:
		pass
	finally:
		print("Shutting down...")
		loop.stop()
		loop.close()


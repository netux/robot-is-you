from __future__ import annotations

import asyncio
import atexit
import uuid
import tempfile
import dataclasses
from time import sleep
from typing import Optional

import flask
from flask import Flask, g, render_template, request, make_response
from flaskthreads import AppContextThread

from src import errors
from src.web.load import load
from src.web.util import RenderTilesOptions, render_tiles
from src.web.context import teardown_global_appcontext

app = Flask(__name__, template_folder="src/web/templates", static_folder="src/web/static")
load_thread = None
load_done = False

def teardown():
	global load_thread, remove_thread

	print("Running teardown")
	with app.app_context() as ctx:
		teardown_global_appcontext()
	print("Global appcontext teardown'd")
	if load_thread is not None:
		load_thread.join(0)
		print("load_thread join'd")
	if remove_thread is not None:
		remove_thread.join(0)
		print("remove_thread join'd")
	print("Bye")

atexit.register(teardown)


def not_ready_fallback(f):
	async def wrapper(*args, **kwargs):
		global load_done

		if load_done:
			return await f(*args, **kwargs)
		else:
			return render_template("loading.html")
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

results_map: dict[str, tempfile.TemporaryFile] = {}
remove_thread = None
remove_scheduled = set()

@app.route("/results/<uuid:uuid>", methods=["GET"])
def results(uuid: str):
	tmp = results_map.get(uuid, None)

	if tmp is None:
		response = make_response("Invalid UUID")
		response.status_code = 400
		return response


	blob = tmp.read()
	tmp.seek(0)
	remove_scheduled.add(uuid)

	response = make_response(blob)
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
	def from_request(Cls, request: flask.Request):
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
	image_uuid = None
	objects = request.args.get("objects", None)
	options = WebappRenderTilesOptions.from_request(request)
	error_msg = None
	status_code = 200

	if objects is not None:
		try:
			r = await render_tiles(objects, is_rule=True, options=options.to_base_options())
			buffer = r.buffer
		except errors.WebappUserError as err:
			error_msg = err.args[0]
			status_code = 400
		else:
			tmp = tempfile.TemporaryFile("br+")
			tmp.write(buffer.read())
			tmp.seek(0)

			image_uuid = uuid.uuid4()
			results_map[image_uuid] = tmp

	response = make_response(render_template("text.html",
		objects=objects,
		image_uuid=image_uuid,
		options=options,
		error_msg=error_msg
	))
	response.status_code = status_code
	return response

def load_sync():
	global load_done

	load_done = False
	asyncio.run(load())
	load_done = True

# This is terrible
# Is there a better way to do this?
def remove_loop():
	global remove_scheduled

	cnt = 0

	while True:
		sleep(1)
		cnt += 1

		if cnt >= 60 * 60:
			cnt = 0
			tmp_scheduled = remove_scheduled
			remove_scheduled = set()
			try:
				while (uuid := tmp_scheduled.pop()):
					tmp = results_map.pop(uuid)
					tmp.close()
			except KeyError:
				# set is empty
				pass

with app.app_context():
	load_thread = AppContextThread(target=load_sync)
	load_thread.start()

	remove_thread = AppContextThread(target=remove_loop)
	remove_thread.start()

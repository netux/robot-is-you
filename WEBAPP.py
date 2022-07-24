from __future__ import annotations

import asyncio
import atexit
import uuid
import tempfile
from time import sleep

from flask import Flask, g, render_template, request, make_response
from flaskthreads import AppContextThread

from src import errors
from src.web.load import load
from src.web.util import render_tiles
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


@app.route("/text", methods=["GET", "POST"])
@not_ready_fallback
async def text():
	image_uuid = None
	objects = request.args.get("objects", None)

	if objects is not None:
		try:
			r = await render_tiles(objects, is_rule=True)
			(buffer, _extra_buffer) = r
		except errors.WebappUserError as err:
			response = make_response(err.args[0])
			response.status_code = 400
			return response

		tmp = tempfile.TemporaryFile("br+")
		tmp.write(buffer.read())
		tmp.seek(0)

		image_uuid = uuid.uuid4()
		results_map[image_uuid] = tmp

	return render_template("text.html", objects=objects, image_uuid=image_uuid)

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

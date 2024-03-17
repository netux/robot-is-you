from __future__ import annotations

import os
import asyncio
import base64
import logging
import math
import tempfile
import dataclasses
from io import BytesIO
from pathlib import Path
from datetime import datetime
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Annotated, Optional

from pydantic import dataclasses as pyd_dataclasses, Field
from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.staticfiles import StaticFiles

from src import errors
from src.cogs.operations import OperationMacros
from src.cogs.variants import VariantHandlers
from src.web.load import load
from src.web.util import RenderTilesOptions, render_tiles
from src.web.dependencies import get_database, get_operation_macros, get_variant_handlers
from config import *

if TYPE_CHECKING:
	from io import TextIOWrapper
	from typing import AsyncIterator

root_path = Path(__file__).parent.resolve()
env = os.environ.get("PYTHON_ENV", default="production")

logging.basicConfig()

frontend_reverse_http_proxy = None
frontend_reverse_websocket_proxy = None

@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
	# Load data
	await load(await get_database())

	# Start clean up task
	dispose_expired_generated_tiles_task = asyncio.ensure_future(dispose_expired_generated_tiles_loop())

	yield # wait until closure

	time_since_close_started = datetime.now()
	dispose_expired_generated_tiles_task.cancel()

	while dispose_expired_generated_tiles_task.cancelled():
		if datetime.now() - time_since_close_started > timedelta(seconds=10):
			app.logger.warn("Could not cancel dispose_expired_generated_tiles task")
			break

	leftover_generated_tiles = list(cached_generated_tiles.values()) # copy
	for generated_tiles in leftover_generated_tiles:
		generated_tiles.force_dispose()
	app.logger.info(f"Disposed {len(leftover_generated_tiles)} left over GeneratedTiles")

	if frontend_reverse_http_proxy is not None:
		await frontend_reverse_http_proxy.aclose()

	if frontend_reverse_websocket_proxy is not None:
		await frontend_reverse_websocket_proxy.aclose()

app = FastAPI(
	root_path=webapp_route_prefix,
	lifespan=lifespan,
	debug=env == "development"
)
app.logger: logging.Logger = logging.getLogger("WEBAPP") # type: ignore
app.logger.setLevel(logging.DEBUG if env == "development" else logging.INFO)


#region Generated Tiles
cached_generated_tiles: dict[int, GeneratedTiles] = dict()

class GeneratedTiles:
	input_hash: int
	generated_at: datetime = dataclasses.field(default_factory=lambda: datetime.now())

	tmp: TextIOWrapper

	def __init__(self, input_hash: str, buffer: BytesIO, generated_at = None):
		if generated_at is None:
			generated_at = datetime.now()

		self.input_hash = input_hash
		self.generated_at = generated_at

		self.tmp = tempfile.TemporaryFile("br+")
		self.tmp.write(buffer.read())
		self.tmp.seek(0)

		if self.input_hash in cached_generated_tiles:
			raise Exception(f"Collision when creating GeneratedTiles: there is already a generated tile with hash {self.input_hash}")
		cached_generated_tiles[self.input_hash] = self

	@property
	def result_url_hash(self) -> str:
		input_hash_bytes = self.input_hash.to_bytes(length=8, byteorder="big", signed=True)
		return base64.urlsafe_b64encode(input_hash_bytes).decode("utf-8")

	@property
	def expires_at(self) -> datetime:
		return self.generated_at + webapp_max_result_life

	"""
	Dispose only when the GeneratedTiles has expired.
	Returns whether the GeneratedTiles was disposed or not.
	"""
	def try_dispose(self) -> bool:
		if datetime.now() > self.expires_at:
			cached_generated_tiles_at_input_hash = cached_generated_tiles.get(self.input_hash, None)
			if cached_generated_tiles_at_input_hash != self:
				raise Exception(f"Cannot dispose of {self!r}: cached GeneratedTiles at {self.input_hash!r} is not this instance (is {cached_generated_tiles_at_input_hash!r})")

			self.force_dispose()

			return True

		return False

	def force_dispose(self):
		self.tmp.close()
		cached_generated_tiles.pop(self.input_hash)

	def read(self) -> BytesIO:
		blob = self.tmp.read()
		self.tmp.seek(0)

		return blob

	def __hash__(self):
		return self.input_hash

	def __repr__(self) -> str:
		return f'<GeneratedTiles input_hash={self.input_hash!r} generated_at={self.generated_at!r} tmp={self.tmp!r}>'

	@staticmethod
	def result_url_hash_to_input_hash(result_url_hash: str):
		hash_bytes = base64.urlsafe_b64decode(result_url_hash.encode("utf-8"))
		input_hash = int.from_bytes(hash_bytes, byteorder="big", signed=True)
		return input_hash

async def dispose_expired_generated_tiles_loop():
	global cached_generated_tiles

	logger = logging.getLogger("WEBAPP.dispose_expired_generated_tiles_loop")

	while True:
		await asyncio.sleep(1)

		cached_generated_tiles_copy = dict(cached_generated_tiles)

		for generated_tiles in cached_generated_tiles_copy.values():
			if generated_tiles.try_dispose():
				logger.info(f"Removing expired {generated_tiles!r}")

#endregion Generated Tiles

#region API handlers
@app.get("/api/list/variants")
async def handle_list_variants(variant_handlers: Annotated[VariantHandlers, Depends(get_variant_handlers)]):
	# TODO(netux): memoize (use functools.cache?)
	all_variants = variant_handlers.handlers
	grouped_variants: dict[str, list[str]] = {}
	for variant in all_variants:
		group = variant.group or "Uncategorized"
		if group not in grouped_variants:
			grouped_variants[group] = []
		grouped_variants[group].extend(variant.hints.values())

	return grouped_variants

@app.get("/api/list/operations")
async def handle_list_operations(operation_macros: Annotated[OperationMacros, Depends(get_operation_macros)]):
	# TODO(netux): memoize (use functools.cache?)
	return operation_macros.get_all()

@pyd_dataclasses.dataclass(frozen=True)
class UserRenderTilesOptions:
	_: dataclasses.KW_ONLY
	use_bg: Optional[bool] = Field(default=False, serialization_alias="useBackground")
	bg_tx: Optional[int] = Field(default=1, serialization_alias="backgroundX")
	bg_ty: Optional[int] = Field(default=4, serialization_alias="backgroundY")
	palette: Optional[str] = Field(default=None, examples=[ None ])
	default_to_letters: Optional[bool] = Field(default=False, serialization_alias="defaultToLetters")
	delay: Optional[int] = 200
	frame_count: Optional[int] = Field(default=3, serialization_alias="frameCount")

	def to_base_options(self) -> RenderTilesOptions:
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

@pyd_dataclasses.dataclass(frozen=True)
class RenderTilesUserInput(UserRenderTilesOptions):
	prompt: str = Field(examples=[ "baba is you" ])

@pyd_dataclasses.dataclass(frozen=True)
class RenderTilesResponse:
	prompt: str
	result_url_hash: str = Field(serialization_alias="resultURLHash")

@app.post("/api/render/tiles")
async def handle_render_tiles(input: RenderTilesUserInput) -> RenderTilesResponse:
	global app

	generated_tiles: GeneratedTiles | None = None
	input_hash = hash(input)

	if input_hash not in cached_generated_tiles:
		app.logger.info(f"Generating tiles for {input!r} (hash={input_hash!r})")
		try:
			r = await render_tiles(input.prompt, is_rule=True, options=input.to_base_options())
			buffer = r.buffer
		except errors.WebappUserError as err:
			raise HTTPException(status_code=400, detail=err.args[0])
		else:
			generated_tiles = GeneratedTiles(input_hash, buffer)
	else:
		generated_tiles = cached_generated_tiles[input_hash]

	return RenderTilesResponse(
		prompt=input.prompt,
		result_url_hash=generated_tiles.result_url_hash
	)

# TODO(netux): handle_render_level / handle_render_custom_level?

@app.get("/results/{result_url_hash}.gif")
async def handle_send_result(result_url_hash: str):
	input_hash = GeneratedTiles.result_url_hash_to_input_hash(result_url_hash)
	generated_tiles = cached_generated_tiles.get(input_hash, None)

	if generated_tiles is None:
		raise HTTPException(status_code=404, detail="Unknown hash")

	max_age = math.floor((generated_tiles.expires_at - datetime.now()).total_seconds())
	content = generated_tiles.read()
	return Response(
		content=content,
		media_type="image/gif",
		headers={
			"cache-control": f"max-age={max_age}"
		}
	)
#endregion API handlers

#region Serve frontend
if env == "development":
	# Reverse proxy to frontend
	from fastapi import Request
	from fastapi_proxy_lib.core.http import ReverseHttpProxy
	from fastapi_proxy_lib.core.websocket import ReverseWebSocketProxy
	from httpx import AsyncClient
	from starlette.websockets import WebSocket

	frontend_reverse_proxy_client = AsyncClient()

	frontend_reverse_http_proxy = ReverseHttpProxy(frontend_reverse_proxy_client, base_url=f"http://{webapp_local_frontend_host}/")
	frontend_reverse_websocket_proxy = ReverseWebSocketProxy(frontend_reverse_proxy_client, base_url=f"ws://{webapp_local_frontend_host}/")

	@app.get("/{path:path}", include_in_schema=False)
	async def frontend_reverse_http_proxy_handler(request: Request, path: str = ""):
		return await frontend_reverse_http_proxy.proxy(request=request, path=path)

	@app.websocket_route("/{path:path}")
	async def frontend_reverse_ws_proxy_handler(websocket: WebSocket, path: str = ""):
		return await frontend_reverse_websocket_proxy.proxy(websocket=websocket, path=path)
else:
	# Mount static frontend built files
	app.mount("/", StaticFiles(directory=str(root_path / "frontend/dist"), html=True), name="frontend-static")
#endregion Serve frontend

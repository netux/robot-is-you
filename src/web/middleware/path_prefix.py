from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from typing import Callable

	from hypercorn.typing import Scope, ASGISendCallable, ASGIReceiveCallable

class RoutePrefixMiddleware:
	"""
	Make all requests prefixed by a single path.
	E.g. when prefix="/v1", requests to "/v1/path" will act as requests to route "/path"
	"""

	def __init__(self, app: Callable, prefix: str = ""):
		self.app: Callable = app
		self.prefix: str = prefix

	async def __call__(self, scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable):
		if scope["type"] == "http":
			scope["root_path"] = self.prefix

		return await self.app(scope, receive, send)

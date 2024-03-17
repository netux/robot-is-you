from lark import Lark

from ..db import Database
from ..cogs.operations import OperationMacros, setup_default_macros
from ..cogs.variants import VariantHandlers, setup_default_variant_handlers
from ..cogs.render import Renderer

from config import db_path


_db: Database = None
async def get_database() -> Database:
	global _db

	if _db is None:
		_db = Database()
		await _db.connect(db_path)

	return _db

_operation_macros: OperationMacros = None
async def get_operation_macros() -> OperationMacros:
	global _operation_macros

	if _operation_macros is None:
		_operation_macros = OperationMacros()
		setup_default_macros(_operation_macros)

	return _operation_macros

_variant_handlers: VariantHandlers = None
async def get_variant_handlers() -> VariantHandlers:
	global _variant_handlers

	if _variant_handlers is None:
		_variant_handlers = VariantHandlers(await get_database())
		setup_default_variant_handlers(_variant_handlers)

	return _variant_handlers

_renderer: Renderer = None
async def get_renderer() -> Renderer:
	global _renderer

	if _renderer is None:
		_renderer = Renderer(await get_database())

	return _renderer

_lark: Lark = None
async def get_lark_parser() -> Lark:
	global _lark

	if _lark is None:
		with open("src/tile_grammar.lark") as _f:
			_lark = Lark(_f.read(), start="row", parser="lalr")

	return _lark


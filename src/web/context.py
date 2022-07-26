from quart import g
from lark import Lark

from ..db import Database
from ..cogs.operations import OperationMacros, setup_default_macros
from ..cogs.variants import VariantHandlers, setup_default_variant_handlers
from ..cogs.render import Renderer

from config import db_path

async def get_database() -> Database:
	if 'db' not in g:
		g.db = Database()
		await g.db.connect(db_path)

	return g.db

async def get_operation_macros() -> OperationMacros:
	if 'operation_macros' not in g:
		g.operation_macros = OperationMacros()
		setup_default_macros(g.operation_macros)

	return g.operation_macros

async def get_variant_handlers() -> VariantHandlers:
	if 'variant_handlers' not in g:
		g.variant_handlers = VariantHandlers(await get_database())
		setup_default_variant_handlers(g.variant_handlers)

	return g.variant_handlers

async def get_renderer() -> Renderer:
	if 'renderer' not in g:
		g.renderer = Renderer(await get_database())

	return g.renderer

async def get_lark_parser() -> Lark:
	if 'lark' not in g:
		with open("src/tile_grammar.lark") as _f:
			g.lark = Lark(_f.read(), start="row", parser="lalr")

	return g.lark

# ---

async def teardown_appcontext(_err):
	if 'db' in g:
		db: Database = g.db
		await db.close()


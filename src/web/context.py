import asyncio

from flask import g, current_app
from flask.ctx import AppContext
from lark import Lark

from ..db import Database
from ..cogs.operations import OperationMacros, setup_default_macros
from ..cogs.variants import VariantHandlers, setup_default_variant_handlers
from ..cogs.render import Renderer

from config import db_path

async def get_database():
	if 'db' not in g:
		g.db = Database()
		await g.db.connect(db_path)

	return g.db

def teardown_global_appcontext():
	if 'db' in g:
		current_app.ensure_sync(g.db.close())

async def get_operation_macros():
	if 'operation_macros' not in g:
		g.operation_macros = OperationMacros()
		setup_default_macros(g.operation_macros)

	return g.operation_macros

async def get_variant_handlers():
	if 'variant_handlers' not in g:
		g.variant_handlers = VariantHandlers(await get_database())
		setup_default_variant_handlers(g.variant_handlers)

	return g.variant_handlers

async def get_renderer():
	if 'renderer' not in g:
		g.renderer = Renderer(await get_database())

	return g.renderer

async def get_lark():
	if 'lark' not in g:
		with open("src/tile_grammar.lark") as _f:
			g.lark = Lark(_f.read(), start="row", parser="lalr")

	return g.lark



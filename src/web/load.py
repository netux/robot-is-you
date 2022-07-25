import collections
import configparser
import itertools
import json
import re
import pathlib
from typing import Any

from quart import current_app

from .. import constants
from ..db import Database
from .context import get_database

async def load_initial_tiles(db: Database):
	'''Loads tile data from `data/values.lua` and `.ld` files.'''

	# values.lua contains the data about which color (on the palette) is associated with each tile.
	with open("data/values.lua", encoding="utf-8", errors="replace") as fp:
		data = fp.read()

	start = data.find("tileslist =\n")
	end = data.find("\n}\n", start)

	assert start > 0 and end > 0
	spanned = data[start:end]

	def prepare(d: dict[str, Any]) -> dict[str, Any]:
		'''From game format into DB format'''
		if d.get("type") is not None:
			d["text_type"] = d.pop("type")
		if d.get("image") is not None:
			d["sprite"] = d.pop("image")
		if d.get("colour") is not None:
			inactive = d.pop("colour").split(",")
			d["inactive_color_x"] = int(inactive[0])
			d["inactive_color_y"] = int(inactive[1])
		if d.get("activecolour") is not None:
			active = d.pop("activecolour").split(",")
			d["active_color_x"] = int(active[0])
			d["active_color_y"] = int(active[1])
		return d

	object_pattern = re.compile(
		r"(object\d+) =\n\t\{"
		r"\n\s*name = \"([^\"]*)\","
		r"\n\s*sprite = \"([^\"]*)\","
		r"\n.*\n.*\n\s*tiling = (-1|\d),"
		r"\n\s*type = (\d),"
		r"\n\s*(?:argextra = .*,\n\s*)?(?:argtype = .*,\n\s*)?"
		r"colour = \{(\d), (\d)\},"
		r"\n\s*(?:active = \{(\d), (\d)\},\n\s*)?"
		r".*\n.*\n.*\n\s*\}",
	)
	initial_objects: dict[str, dict[str, Any]] = {}
	for match in re.finditer(object_pattern, spanned):
		obj, name, sprite, tiling, type, c_x, c_y, a_x, a_y = match.groups()
		if a_x is None or a_y is None:
			inactive_x = active_x = int(c_x)
			inactive_y = active_y = int(c_y)
		else:
			inactive_x = int(c_x)
			inactive_y = int(c_y)
			active_x = int(a_x)
			active_y = int(a_y)
		tiling = int(tiling)
		type = int(type)
		initial_objects[obj] = dict(
			name=name,
			sprite=sprite,
			tiling=tiling,
			text_type=type,
			inactive_color_x=inactive_x,
			inactive_color_y=inactive_y,
			active_color_x=active_x,
			active_color_y=active_y,
		)

	changed_objects: list[dict[str, Any]] = []
	for path in pathlib.Path(f"data/levels/{constants.BABA_WORLD}").glob("*.ld"):

		parser = configparser.ConfigParser()
		parser.read(path, encoding="utf-8")
		changed_ids = parser.get("tiles", "changed", fallback=",").split(",")[:-1]

		fields = ("name", "image", "tiling", "colour", "activecolour", "type")
		for id in changed_ids:
			changes: dict[str, Any] = {}
			for field in fields:
				change = parser.get("tiles", f"{id}_{field}", fallback=None)
				if change is not None:
					changes[field] = change
			# Ignore blank changes (identical to values.lua objects)
			# Ignore changes without a name (the same name but a different color, etc)
			if changes and changes.get("name") is not None:
				changed_objects.append({**initial_objects[id], **prepare(changes)})

	with open("config/editortileignore.json") as f:
		ignored_names = json.load(f)
	by_name = filter(lambda x: x[0] not in ignored_names, itertools.groupby(
		sorted(changed_objects, key=lambda x: x["name"]),
		key=lambda x: x["name"]
	))
	ready: list[dict[str, Any]] = []
	for name, duplicates in by_name:
		def freeze_dict(d: dict[str, Any]) -> tuple[tuple[str, Any], ...]:
			'''Hashable (frozen) dict'''
			return tuple(d.items())
		counts = collections.Counter(map(freeze_dict, duplicates))
		most_common, _ = counts.most_common(1)[0]
		ready.append(dict(most_common))

	await db.conn.executemany(
		f'''
		INSERT INTO tiles(
			name,
			sprite,
			source,
			version,
			inactive_color_x,
			inactive_color_y,
			active_color_x,
			active_color_y,
			tiling,
			text_type
		)
		VALUES (
			:name,
			:sprite,
			{repr(constants.BABA_WORLD)},
			0,
			:inactive_color_x,
			:inactive_color_y,
			:active_color_x,
			:active_color_y,
			:tiling,
			:text_type
		)
		ON CONFLICT(name, version)
		DO UPDATE SET
			sprite=excluded.sprite,
			source={repr(constants.BABA_WORLD)},
			inactive_color_x=excluded.inactive_color_x,
			inactive_color_y=excluded.inactive_color_y,
			active_color_x=excluded.active_color_x,
			active_color_y=excluded.active_color_y,
			tiling=excluded.tiling,
			text_type=excluded.text_type;
		''',
		initial_objects.values()
	)

	await db.conn.executemany(
		f'''
		INSERT INTO tiles
		VALUES (
			:name,
			:sprite,
			{repr(constants.BABA_WORLD)},
			0,
			:inactive_color_x,
			:inactive_color_y,
			:active_color_x,
			:active_color_y,
			:tiling,
			:text_type,
			NULL,
			""
		)
		ON CONFLICT(name, version) DO NOTHING;
		''',
		ready
	)

async def load_editor_tiles(db: Database):
	'''Loads tile data from `data/editor_objectlist.lua`.'''

	with open("data/editor_objectlist.lua", encoding="utf-8", errors="replace") as fp:
		data = fp.read()

	start = data.find("editor_objlist = {")
	end = data.find("\n}", start)
	assert start > 0 and end > 0
	spanned = data[start:end]

	object_pattern = re.compile(
		r"\[\d+\] = \{"
		r"\n\s*name = \"([^\"]*)\","
		r"(?:\n\s*sprite = \"([^\"]*)\",)?"
		r"\n.*"
		r"\n\s*tags = \{((?:\"[^\"]*?\"(?:,\"[^\"]*?\")*)?)\},"
		r"\n\s*tiling = (-1|\d),"
		r"\n\s*type = (\d),"
		r"\n.*"
		r"\n\s*colour = \{(\d), (\d)\},"
		r"(?:\n\s*colour_active = \{(\d), (\d)\})?"
	)
	tag_pattern = re.compile(r"\"([^\"]*?)\"")
	objects = []
	for match in re.finditer(object_pattern, spanned):
		name, sprite, raw_tags, tiling, text_type, c_x, c_y, a_x, a_y = match.groups()
		sprite = name if sprite is None else sprite
		a_x = c_x if a_x is None else a_x
		a_y = c_y if a_y is None else a_y
		active_x = int(a_x)
		active_y = int(a_y)
		inactive_x = int(c_x)
		inactive_y = int(c_y)
		tiling = int(tiling)
		text_type = int(text_type)
		tag_list = []
		for tag in re.finditer(tag_pattern, raw_tags):
			tag_list.append(tag.group(0))
		tags = "\t".join(tag_list)

		objects.append(dict(
			name=name,
			sprite=sprite,
			tiling=tiling,
			text_type=text_type,
			inactive_color_x=inactive_x,
			inactive_color_y=inactive_y,
			active_color_x=active_x,
			active_color_y=active_y,
			tags=tags
		))

	await db.conn.executemany(
		f'''
		INSERT INTO tiles
		VALUES (
			:name,
			:sprite,
			{repr(constants.BABA_WORLD)},
			1,
			:inactive_color_x,
			:inactive_color_y,
			:active_color_x,
			:active_color_y,
			:tiling,
			:text_type,
			NULL,
			:tags
		)
		ON CONFLICT(name, version)
		DO UPDATE SET
			sprite=excluded.sprite,
			source={repr(constants.BABA_WORLD)},
			inactive_color_x=excluded.inactive_color_x,
			inactive_color_y=excluded.inactive_color_y,
			active_color_x=excluded.active_color_x,
			active_color_y=excluded.active_color_y,
			tiling=excluded.tiling,
			text_type=excluded.text_type,
			tags=:tags;
		''',
		objects
	)

async def load_custom_tiles(db: Database):
	'''Loads custom tile data from `data/custom/*.json`'''

	def prepare(source: str, d: dict[str, Any]) -> dict[str, Any]:
		'''From config format to db format'''
		inactive = d.pop("color")
		if d.get("active") is not None:
			d["inactive_color_x"] = inactive[0]
			d["inactive_color_y"] = inactive[1]
			d["active_color_x"] = d["active"][0]
			d["active_color_y"] = d["active"][1]
		else:
			d["inactive_color_x"] = d["active_color_x"] = inactive[0]
			d["inactive_color_y"] = d["active_color_y"] = inactive[1]
		d["source"] = d.get("source", source)
		d["tiling"] = d.get("tiling", -1)
		d["text_type"] = d.get("text_type", 0)
		d["text_direction"] = d.get("text_direction")
		d["tags"] = d.get("tags", "")
		return d

	async with db.conn.cursor() as cur:
		for path in pathlib.Path("data/custom").glob("*.json"):
			source = path.parts[-1].split(".")[0]
			with open(path, errors="replace", encoding="utf-8") as fp:
				objects = [prepare(source, obj) for obj in json.load(fp)]

			await cur.executemany(
				'''
				INSERT INTO tiles
				VALUES (
					:name,
					:sprite,
					:source,
					2,
					:inactive_color_x,
					:inactive_color_y,
					:active_color_x,
					:active_color_y,
					:tiling,
					:text_type,
					:text_direction,
					:tags
				)
				ON CONFLICT(name, version)
				DO UPDATE SET
					sprite=excluded.sprite,
					source=excluded.source,
					inactive_color_x=excluded.inactive_color_x,
					inactive_color_y=excluded.inactive_color_y,
					active_color_x=excluded.active_color_x,
					active_color_y=excluded.active_color_y,
					tiling=excluded.tiling,
					text_type=excluded.text_type,
					text_direction=excluded.text_direction,
					tags=excluded.tags;
				''',
				objects
			)
			# this is a mega HACK, but I'm keeping it because the alternative is a headache
			hacks = [x for x in objects if "baba_special" in x["tags"].split("\t")]
			await cur.executemany(
				'''
				INSERT INTO tiles
				VALUES (
					:name,
					:sprite,
					:source,
					0,
					:inactive_color_x,
					:inactive_color_y,
					:active_color_x,
					:active_color_y,
					:tiling,
					:text_type,
					:text_direction,
					:tags
				)
				ON CONFLICT(name, version)
				DO UPDATE SET
					sprite=excluded.sprite,
					source=excluded.source,
					inactive_color_x=excluded.inactive_color_x,
					inactive_color_y=excluded.inactive_color_y,
					active_color_x=excluded.active_color_x,
					active_color_y=excluded.active_color_y,
					tiling=excluded.tiling,
					text_type=excluded.text_type,
					text_direction=excluded.text_direction,
					tags=excluded.tags;
				''',
				hacks
			)

async def load():
	current_app.logger.info("Loading...")
	db = await get_database()
	current_app.logger.info("Got database")
	await load_initial_tiles(db)
	current_app.logger.info("Loaded initial tiles")
	await load_editor_tiles(db)
	current_app.logger.info("Loaded editor tiles")
	await load_custom_tiles(db)
	current_app.logger.info("Loaded custom tiles")
	current_app.logger.info("Ready!")


from __future__ import annotations

import re
from time import time
from io import BytesIO
from os import listdir
from datetime import datetime

from lark.lexer import Token
from lark.tree import Tree

from .. import constants, errors
from ..tile import RawTile

from .context import get_database, get_operation_macros, get_variant_handlers, get_renderer, get_lark

async def handle_variant_errors(err: errors.VariantError):
	'''Handle errors raised in a command context by variant handlers'''
	word, variant, *rest = err.args
	msg = f"The variant `{variant}` for `{word}` is invalid"
	if isinstance(err, errors.BadTilingVariant):
		tiling = rest[0]
		return errors.WebappUserError(
			f"{msg}, since it can't be applied to tiles with tiling type `{tiling}`."
		)
	elif isinstance(err, errors.TileNotText):
		return errors.WebappUserError(
			f"{msg}, since the tile is not text."
		)
	elif isinstance(err, errors.BadPaletteIndex):
		return errors.WebappUserError(
			f"{msg}, since the color is outside the palette."
		)
	elif isinstance(err, errors.BadLetterVariant):
		return errors.WebappUserError(
			f"{msg}, since letter-style text can only be 1 or 2 letters wide."
		)
	elif isinstance(err, errors.BadMetaVariant):
		depth = rest[0]
		return errors.WebappUserError(
			f"{msg}. `{depth}` is greater than the maximum meta depth, which is `{constants.MAX_META_DEPTH}`."
		)
	elif isinstance(err, errors.TileDoesntExist):
		return errors.WebappUserError(
			f"{msg}, since the tile doesn't exist in the database."
		)
	elif isinstance(err, errors.UnknownVariant):
		return errors.WebappUserError(
			f"The variant `{variant}` is not valid."
		)
	else:
		return errors.WebappUserError(f"{msg}.")

async def handle_custom_text_errors(err: errors.TextGenerationError):
	'''Handle errors raised in a command context by variant handlers'''
	text, *rest = err.args
	msg = f"The text {text} couldn't be generated automatically"
	if isinstance(err, errors.BadLetterStyle):
		return errors.WebappUserError(
			f"{msg}, since letter style can only applied to a single row of text."
		)
	elif isinstance(err, errors.TooManyLines):
		return errors.WebappUserError(
			f"{msg}, since it has too many lines."
		)
	elif isinstance(err, errors.LeadingTrailingLineBreaks):
		return errors.WebappUserError(
			f"{msg}, since there's `/` characters at the start or end of the text."
		)
	elif isinstance(err, errors.BadCharacter):
		mode, char = rest
		return errors.WebappUserError(
			f"{msg}, since the letter {char} doesn't exist in '{mode}' mode."
		)
	elif isinstance(err, errors.CustomTextTooLong):
		return errors.WebappUserError(
			f"{msg}, since it's too long ({len(text)})."
		)
	else:
		return errors.WebappUserError(f"{msg}.")

async def handle_operation_errors(err: errors.OperationError):
	'''Handle errors raised in a command context by operation macros'''
	operation, pos, tile, *rest = err.args
	msg = f"The operation {operation} is not valid"
	if isinstance(err, errors.OperationNotFound):
		return errors.WebappUserError(
			f"The operation `{operation}` for `{tile.name}` could not be found."
		)
	elif isinstance(err, errors.MovementOutOfFrame):
		return errors.WebappUserError(
			f"Tried to move out of bounds with the `{operation}` operation for `{tile.name}`."
		)
	else:
		return errors.WebappUserError(f"The operation `{operation}` failed for `{tile.name}`.")

async def render_tiles(
	objects: str,
	is_rule: bool,
	*,
	# TODO(netux): use kwargs
	background = None,
	palette = None,
	raw = None,
	letter = None,
	delay = None,
	frames = None
):
	database = await get_database()
	operation_macros = await get_operation_macros()
	variant_handlers = await get_variant_handlers()
	renderer = await get_renderer()
	lark = await get_lark()

	'''Performs the bulk work for both `tile` and `rule` commands.'''
	start = time()
	tiles = objects.lower().strip()

	# replace emoji with their :text: representation
	builtin_emoji = {
		ord("\u24dc"): ":m:", # lower case circled m
		ord("\u24c2"): ":m:", # upper case circled m
		ord("\U0001f199"): ":up:", # up! emoji
		ord("\U0001f637"): ":mask:", # mask emoji
		ord("\ufe0f"): None
	}
	tiles = tiles.translate(builtin_emoji)
	tiles = re.sub(r'<a?(:[a-zA-Z0-9_]{2,32}:)\d{1,21}>', r'\1', tiles)

	# ignore all these
	tiles = tiles.replace("```\n", "").replace("\\", "").replace("`", "")

	# Determines if this should be a spoiler
	spoiler = tiles.count("||") >= 2
	tiles = tiles.replace("|", "")

	# Check for empty input
	if not tiles:
		raise errors.WebappUserError("Input cannot be blank.")
		# return await ctx.error("Input cannot be blank.")

	# Handle flags *first*, before even splitting
	# TODO(netux): move these to kwargs
	flag_patterns = (
		r"(?:^|\s)(?:--background|-b)(?:=(\d)/(\d))?(?:$|\s)",
		r"(?:^|\s)(?:--palette=|-p=|palette:)(\w+)(?:$|\s)",
		r"(?:^|\s)(?:--raw|-r)(?:=([a-zA-Z_0-9]+))?(?:$|\s)",
		r"(?:^|\s)(?:--letter|-l)(?:$|\s)",
		r"(?:^|\s)(?:(--delay=|-d=)(\d+))(?:$|\s)",
		r"(?:^|\s)(?:(--frames=|-f=)(\d))(?:$|\s)",
	)
	background = None
	for match in re.finditer(flag_patterns[0], tiles):
		if match.group(1) is not None:
			tx, ty = int(match.group(1)), int(match.group(2))
			if not (0 <= tx <= 7 and 0 <= ty <= 5):
				raise errors.WebappUserError("The provided background color is invalid.")
				# return await ctx.error("The provided background color is invalid.")
			background = tx, ty
		else:
			background = (0, 4)
	palette = "default"
	for match in re.finditer(flag_patterns[1], tiles):
		palette = match.group(1)
		if palette + ".png" not in listdir("data/palettes"):
			raise errors.WebappUserError(f"Could not find a palette with name \"{palette}\".")
			# return await ctx.error(f"Could not find a palette with name \"{palette}\".")
	raw_output = False
	raw_name = ""
	for match in re.finditer(flag_patterns[2], tiles):
		raw_output = True
		if match.group(1) is not None:
			raw_name = match.group(1)
	default_to_letters = False
	for match in re.finditer(flag_patterns[3], tiles):
		default_to_letters = True
	delay = 200
	for match in re.finditer(flag_patterns[4], tiles):
		delay = int(match.group(2))
		if delay < 1 or delay > 1000:
			raise errors.WebappUserError(f"Delay must be between 1 and 1000 milliseconds.")
			# return await ctx.error(f"Delay must be between 1 and 1000 milliseconds.")
	frame_count = 3
	for match in re.finditer(flag_patterns[5], tiles):
		frame_count = int(match.group(2))
		if frame_count < 1 or frame_count > 3:
			raise errors.WebappUserError(f"The frame count must be 1, 2 or 3.")
			# return await ctx.error(f"The frame count must be 1, 2 or 3.")

	# Clean up
	for pattern in flag_patterns:
		tiles = re.sub(pattern, " ", tiles)

	# read from file if nothing (beyond flags) is provided
	# TODO(netux): remove
	# if not tiles.strip():
	# 	attachments = ctx.message.attachments
	# 	if len(attachments) > 0:
	# 		file = attachments[0]
	# 		if file.size > constants.MAX_INPUT_FILE_SIZE:
	# 			await ctx.error(f"The file is too large ({file.size} bytes). Maximum: {constants.MAX_INPUT_FILE_SIZE} bytes")
	# 		try:
	# 			tiles = (await file.read()).decode("utf-8").lower().strip()
	# 			if file.filename != "message.txt":
	# 				raw_name = file.filename.split(".")[0]
	# 		except UnicodeDecodeError:
	# 			await ctx.error("The file contains invalid UTF-8. Make sure it's not corrupt.")


	# Split input into lines
	rows = tiles.splitlines()

	expanded_tiles: dict[tuple[int, int, int], list[RawTile]] = {}
	previous_tile: list[RawTile] = []
	# Do the bulk of the parsing here:
	for y, row in enumerate(rows):
		x = 0
		try:
			row_maybe = row.strip()
			if not row_maybe:
				continue
			tree = lark.parse(row_maybe)
		except lark.UnexpectedCharacters as e:
			raise errors.WebappUserError(f"Invalid character `{e.char}` in row {y}, around `... {row[e.column - 5: e.column + 5]} ...`")
			# return await ctx.error(f"Invalid character `{e.char}` in row {y}, around `... {row[e.column - 5: e.column + 5]} ...`")
		except lark.UnexpectedToken as e:
			mistake_kind = e.match_examples(
				lark.parse,
				{
					"unclosed": [
						"(baba",
						"[this ",
						"\"rule",
					],
					"missing": [
						":red",
						"baba :red",
						"&baba",
						"baba& keke",
						">baba",
						"baba> keke"
					],
					"variant": [
						"baba: keke",
					]
				}
			)
			around = f"`... {row[e.column - 5 : e.column + 5]} ...`"
			if mistake_kind == "unclosed":
				raise errors.WebappUserError(f"Unclosed brackets or quotes! Expected them to close around {around}.")
				# return await ctx.error(f"Unclosed brackets or quotes! Expected them to close around {around}.")
			elif mistake_kind == "missing":
				raise errors.WebappUserError(f"Missing a tile in row {y}! Make sure not to have spaces between `&`, `:`, or `>`!\nError occurred around {around}.")
				# return await ctx.error(f"Missing a tile in row {y}! Make sure not to have spaces between `&`, `:`, or `>`!\nError occurred around {around}.")
			elif mistake_kind == "variant":
				raise errors.WebappUserError(f"Empty variant in row {y}, around {around}.")
				# return await ctx.error(f"Empty variant in row {y}, around {around}.")
			else:
				raise errors.WebappUserError(f"Invalid syntax in row {y}, around {around}.")
				# return await ctx.error(f"Invalid syntax in row {y}, around {around}.")
		except lark.UnexpectedEOF as e:
			raise errors.WebappUserError(f"Unexpected end of input in row {y}.")
			# return await ctx.error(f"Unexpected end of input in row {y}.")
		for line in tree.children:
			line: Tree
			line_text_mode: bool | None = None
			line_variants: list[str] = []

			if line.data == "text_chain":
				line_text_mode = True
			elif line.data == "tile_chain":
				line_text_mode = False
			elif line.data == "text_block":
				line_text_mode = True
			elif line.data == "tile_block":
				line_text_mode = False

			if line.data in ("text_block", "tile_block", "any_block"):
				*stacks, variants = line.children
				variants: Tree
				for variant in variants.children:
					variant: Token
					line_variants.append(variant.value)
			else:
				stacks = line.children

			for stack in stacks:
				stack: Tree

				blobs: list[tuple[bool | None, list[str], Tree]] = []

				if stack.data == "blob_stack":
					for variant_blob in stack.children:
						blob, variants = variant_blob.children
						blob: Tree
						variants: Tree

						blob_text_mode: bool | None = None

						stack_variants = []
						for variant in variants.children:
							variant: Token
							stack_variants.append(variant.value)
						if blob.data == "text_blob":
							blob_text_mode = True
						elif blob.data == "tile_blob":
							blob_text_mode = False

						blobs.append((blob_text_mode, stack_variants, blob))
				else:
					blobs = [(None, [], stack)]

				for blob_text_mode, stack_variants, blob in blobs:
					for process in blob.children:
						process: Tree
						t = 0

						unit, *changes = process.children
						unit: Tree
						changes: list[Tree]

						object, variants = unit.children
						object: Token
						obj = object.value
						variants: Tree

						final_variants: list[str] = [
							var.value
							for var in variants.children
						]

						def append_extra_variants(final_variants: list[str]):
							'''IN PLACE'''
							final_variants.extend(stack_variants)
							final_variants.extend(line_variants)

						def handle_text_mode(obj: str) -> str:
							'''RETURNS COPY'''
							text_delta = -1 if blob_text_mode is False else blob_text_mode or 0
							text_delta += -1 if line_text_mode is False else line_text_mode or 0
							text_delta += is_rule
							if text_delta == 0:
								return obj
							elif text_delta > 0:
								for _ in range(text_delta):
									if obj.startswith("tile_"):
										obj = obj[5:]
									else:
										obj = f"text_{obj}"
								return obj
							else:
								for _ in range(text_delta):
									if obj.startswith("text_"):
										obj = obj[5:]
									else:
										raise RuntimeError("this should never happen")
										# TODO: handle this explicitly
								return obj

						obj = handle_text_mode(obj)
						append_extra_variants(final_variants)

						dx = dy = 0
						temp_tile: list[RawTile] = [RawTile(obj, final_variants, ephemeral=False)]
						last_hack = False
						for change in changes:
							if change.data == "transform":
								last_hack = False
								seq, unit = change.children
								seq: str

								count = len(seq)
								still = temp_tile.pop()
								still.ephemeral = True
								if still.is_previous:
									still = previous_tile[-1]
								else:
									previous_tile[-1:] = [still]

								for dt in range(count):
									expanded_tiles.setdefault((x + dx, y + dy, t + dt), []).append(still)

								object, variants = unit.children
								object: Token
								obj = object.value
								obj = handle_text_mode(obj)

								final_variants = [var.value for var in variants.children]
								append_extra_variants(final_variants)

								temp_tile.append(
									RawTile(
										obj,
										final_variants,
										ephemeral=False
									)
								)
								t += count

							elif change.data == "operation":
								last_hack = True
								oper = change.children[0]
								oper: Token
								try:
									ddx, ddy, dt = operation_macros.expand_into(
										expanded_tiles,
										temp_tile,
										(x + dx, y + dy, t),
										oper.value
									)
								except errors.OperationError as err:
									raise handle_operation_errors(err) from err
								dx += ddx
								dy += ddy
								t += dt
						# somewhat monadic behavior
						if not last_hack:
							expanded_tiles.setdefault((x + dx, y + dy, t), []).extend(temp_tile[:])
				x += 1

	# Get the dimensions of the grid
	width = max(expanded_tiles, key=lambda pos: pos[0])[0] + 1
	height = max(expanded_tiles, key=lambda pos: pos[1])[1] + 1
	duration = 1 + max(t for _, _, t in expanded_tiles)

	temporal_maxima: dict[tuple[int, int], tuple[int, list[RawTile]]] = {}
	for (x, y, t), tile_stack in expanded_tiles.items():
		if (x, y) in temporal_maxima and temporal_maxima[x, y][0] < t:
			persistent = [tile for tile in tile_stack if not tile.ephemeral]
			if len(persistent) != 0:
				temporal_maxima[x, y] = t, persistent
		elif (x, y) not in temporal_maxima:
			persistent = [tile for tile in tile_stack if not tile.ephemeral]
			if len(persistent) != 0:
				temporal_maxima[x, y] = t, persistent
	# Pad the grid across time
	for (x, y), (t_star, tile_stack) in temporal_maxima.items():
		for t in range(t_star, duration - 1):
			if (x, y, t + 1) not in expanded_tiles:
				expanded_tiles[x, y, t + 1] = tile_stack
			else:
				expanded_tiles[x, y, t + 1] = tile_stack + expanded_tiles[x, y, t + 1]

	# filter out blanks before rendering
	expanded_tiles = {index: [tile for tile in stack if not tile.is_empty] for index, stack in expanded_tiles.items()}
	expanded_tiles = {index: stack for index, stack in expanded_tiles.items() if len(stack) != 0}

	# Don't proceed if the request is too large.
	# (It shouldn't be that long to begin with because of Discord's 2000 character limit)
	if width * height * duration > constants.MAX_VOLUME:
		raise errors.WebappUserError(f"Too large of an animation ({width * height * duration}). An animation may have up to {constants.MAX_VOLUME} tiles, including tiles repeated in frames.")
		# return await ctx.error(f"Too large of an animation ({width * height * duration}). An animation may have up to {constants.MAX_VOLUME} tiles, including tiles repeated in frames.")
	if width > constants.MAX_WIDTH:
		raise errors.WebappUserError(f"Too wide ({width}). You may only render scenes up to {constants.MAX_WIDTH} tiles wide.")
		# return await ctx.error(f"Too wide ({width}). You may only render scenes up to {constants.MAX_WIDTH} tiles wide.")
	if height > constants.MAX_HEIGHT:
		raise errors.WebappUserError(f"Too high ({height}). You may only render scenes up to {constants.MAX_HEIGHT} tiles tall.")
		# return await ctx.error(f"Too high ({height}). You may only render scenes up to {constants.MAX_HEIGHT} tiles tall.")
	if duration > constants.MAX_DURATION:
		raise errors.WebappUserError(f"Too many frames ({duration}). You may only render scenes with up to {constants.MAX_DURATION} animation frames.")
		# return await ctx.error(f"Too many frames ({duration}). You may only render scenes with up to {constants.MAX_DURATION} animation frames.")

	try:
		# Handles variants based on `:` affixes
		buffer = BytesIO()
		extra_buffer = BytesIO() if raw_output else None
		extra_names = [] if raw_output else None
		full_objects = await variant_handlers.handle_grid(
			expanded_tiles,
			(width, height),
			raw_output=raw_output,
			extra_names=extra_names,
			default_to_letters=default_to_letters
		)
		if extra_names is not None and not raw_name:
			if len(extra_names) == 1:
				raw_name = extra_names[0]
			else:
				raw_name = constants.DEFAULT_RENDER_ZIP_NAME
		full_tiles = await renderer.render_full_tiles(
			full_objects,
			palette=palette,
			random_animations=True
		)
		await renderer.render(
			full_tiles,
			grid_size=(width, height),
			duration=duration,
			palette=palette,
			background=background,
			out=buffer,
			delay=delay,
			frame_count=frame_count,
			upscale=not raw_output,
			extra_out=extra_buffer,
			extra_name=raw_name,
		)
	except errors.TileNotFound as e:
		word = e.args[0]
		name = word.name
		if word.name.startswith("tile_") and await database.tile(name[5:]) is not None:
			raise errors.WebappUserError(f"The tile `{name}` could not be found. Perhaps you meant `{name[5:]}`?")
			# return await ctx.error(f"The tile `{name}` could not be found. Perhaps you meant `{name[5:]}`?")
		if await database.tile("text_" + name) is not None:
			raise errors.WebappUserError(f"The tile `{name}` could not be found. Perhaps you meant `{'text_' + name}`?")
			# return await ctx.error(f"The tile `{name}` could not be found. Perhaps you meant `{'text_' + name}`?")
		raise errors.WebappUserError(f"The tile `{name}` could not be found.")
		# return await ctx.error(f"The tile `{name}` could not be found.")
	except errors.BadTileProperty as e:
		word, (w, h) = e.args
		raise errors.WebappUserError(f"The tile `{word.name}` could not be made into a property, because it's too big (`{w} by {h}`).")
		# return await ctx.error(f"The tile `{word.name}` could not be made into a property, because it's too big (`{w} by {h}`).")
	except errors.EmptyTile as e:
		raise errors.WebappUserError("Cannot use blank tiles in that context.")
		# return await ctx.error("Cannot use blank tiles in that context.")
	except errors.EmptyVariant as e:
		word = e.args[0]
		raise errors.WebappUserError(
			f"You provided an empty variant for `{word.name}`."
		)
		# return await ctx.error(
		# 	f"You provided an empty variant for `{word.name}`."
		# )
	except errors.VariantError as e:
		raise await handle_variant_errors(e) from e
	except errors.TextGenerationError as e:
		raise await handle_custom_text_errors(e) from e

	filename = datetime.utcnow().strftime(r"render_%Y-%m-%d_%H.%M.%S.gif")
	delta = time() - start
	msg = f"*Rendered in {delta:.2f} s*"
	if extra_buffer is not None and raw_name:
		extra_buffer.seek(0)
		return (buffer, extra_buffer)
		# await ctx.reply(content=f'{msg}\n*Raw files:*', files=[discord.File(extra_buffer, filename=f"{raw_name}.zip"),discord.File(buffer, filename=filename, spoiler=spoiler)])
	else:
		return (buffer, None)
		# await ctx.reply(content=msg, file=discord.File(buffer, filename=filename, spoiler=spoiler))

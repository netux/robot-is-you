# About

This is a webapp interface for the functionality normally found in [ROBOT IS YOU](https://github.com/RocketFace/robot-is-you), a Discord bot by @RocketFace based on the indie game [Baba Is You](https://store.steampowered.com/app/736260/Baba_Is_You/) (by Arvi Teikari) and written with the [discord.py](https://discordpy.readthedocs.io/en/latest/) library.

The webapp is written in Quart (reimplementation of Flask), and aims to provide all the functionality of the bot .

99% of the code here was written by the original ROBOT IS YOU devs. Please give them some love 🧡.

Looking for the README for ROBOT? Check [the original repository](https://github.com/RocketFace/robot-is-you) or [the README in the master branch](./blob/master#README).

# To Host This Yourself

Please follow the terms of the license!

It is recommended to use a virtual environment: `python3 -m venv .venv`, then `source .venv/path_to_active_script_for_your_system`.

Install the requirements: `python3 -m pip install -r requirements.txt`.

Run the webapp using `python3 WEBAPP.py`.

## Required files

Bot/webapp configuration is in `config.py`. Of all the values defined there, the webapp uses:

* `db_path`: `str` - The path to the sqlite3 database used by the bot.
* `webapp_host`: `str` - Hostname to listen on.
* `webapp_port`: `int` - Port to listen on.
* `webapp_max_result_life`: `datetime.timedelta` - Amount of time a result lives on the server before it is removed.
* `webapp_route_prefix`: `str | None` - Prefix for all routes. Useful when serving through a proxy e.g. https://my.domain/webapp-is-you/.

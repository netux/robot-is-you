from datetime import timedelta

try:
    import discord
except ModuleNotFoundError:
    # This might be imported from WEBAPP.py, which may mean discord.py is not installed.
    # Let's just mock what we need, as WEBAPP.py is unlikely to use config from ROBOT.py

    #region discord.py mockups
    class _DiscordColor:
        def __init__(self, hex: int): ...

    class discord:
        Color = _DiscordColor
    #endregion discord.py mockups

#region ROBOT
activity = "ROBOT IS HELP"
description = "*An entertainment bot for rendering levels and custom scenes based on the indie game Baba Is You.*"
prefixes = ["+", "Robot is ", "robot is ", "ROBOT IS "]
trigger_on_mention = True
embed_color = discord.Color(9077635)
log_file = None
db_path = "robot.db"
original_id = 480227663047294987
cogs = [
    "src.cogs.owner",
    "src.cogs.global",
    "src.cogs.meta",
    "src.cogs.errorhandler",
    "src.cogs.reader",
    "src.cogs.render",
    "src.cogs.variants",
    "src.cogs.utilities",
    "src.cogs.operations",
    "jishaku"
]
#endregion ROBOT

#region WEBAPP
webapp_host = "0.0.0.0"
webapp_port = 5000
webapp_max_result_life = timedelta(hours=1)
webapp_route_prefix = None
webapp_frontend_host = "localhost:5173"
#endregion WEBAPP

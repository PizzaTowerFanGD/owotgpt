"""
Standardized command system for OWoTGPT bot.

Provides a modular, extensible command dispatcher with consistent parsing,
error handling, and permission management.
"""

import re
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Any
from difflib import get_close_matches
from permissions import PermissionManager, UserTier, GroupTier

RATE_LIMIT_COMMANDS = 2
RATE_LIMIT_WINDOW = 10

# Global state for pending confirmations
_pending_stops = set()


@dataclass
class CommandContext:
    """Context passed to command handlers containing bot state and message info."""
    websocket: Any
    location: str
    real_user: str
    my_id: str
    admin_user: str
    bot_nick_default: str
    histories: Dict[str, List[str]]
    current_temperature: float
    bot_color: int
    context_limit: int
    context_token_limit: int
    permission_manager: PermissionManager
    is_registered: bool
    chat_template: str


@dataclass
class Command:
    """Represents a bot command with metadata and handler."""
    name: str
    handler: Callable
    aliases: List[str] = field(default_factory=list)
    required_tier: UserTier = UserTier.USER
    help_text: str = ""
    usage: str = ""
    description: str = ""

    @property
    def all_names(self) -> List[str]:
        """Returns all valid names for this command (name + aliases)."""
        return [self.name] + self.aliases

    def matches(self, text: str) -> bool:
        """Check if text matches this command (exact or prefix match)."""
        text_lower = text.lower().strip()
        for name in self.all_names:
            name_lower = name.lower()
            if text_lower == name_lower or text_lower.startswith(name_lower + " "):
                return True
        return False

    def extract_args(self, text: str) -> str:
        """Extract arguments portion from command text."""
        text_lower = text.lower().strip()
        for name in self.all_names:
            name_lower = name.lower()
            if text_lower == name_lower:
                return ""
            if text_lower.startswith(name_lower + " "):
                return text[len(name):].strip()
        return ""


class CommandDispatcher:
    """Routes commands to their handlers with validation and error handling."""

    def __init__(self, permission_manager: PermissionManager):
        self.commands: Dict[str, Command] = {}
        self.alias_map: Dict[str, str] = {}
        self.permission_manager = permission_manager
        self._rate_limits: Dict[str, List[float]] = {}

    def register(self, command: Command) -> None:
        """Register a command and its aliases."""
        self.commands[command.name] = command
        for alias in command.aliases:
            self.alias_map[alias.lower()] = command.name

    def parse_command(self, text: str) -> Optional[tuple[Command, str]]:
        """
        Parse input text to find matching command and extract arguments.
        Returns (command, args) tuple or None if no match.
        """
        text_lower = text.lower().strip()

        for cmd in self.commands.values():
            for name in cmd.all_names:
                name_lower = name.lower()
                if text_lower == name_lower:
                    return cmd, ""
                if text_lower.startswith(name_lower + " "):
                    args = text[len(name):].strip()
                    return cmd, args

        return None

    def _is_rate_limited(self, ctx: CommandContext) -> bool:
        """Check if a user is rate limited. Returns True if limited."""
        if ctx.is_registered and ctx.real_user:
            key = ctx.real_user
        else:
            key = "__anon__"

        now = time.monotonic()
        timestamps = self._rate_limits.get(key, [])
        timestamps = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW]

        if len(timestamps) >= RATE_LIMIT_COMMANDS:
            self._rate_limits[key] = timestamps
            return True

        timestamps.append(now)
        self._rate_limits[key] = timestamps
        return False

    def dispatch(self, text: str, ctx: CommandContext) -> Optional[str]:
        """
        Dispatch a command to its handler.
        Returns response message or None if command not found/handled.
        """
        result = self.parse_command(text)
        if not result:
            return None

        if self._is_rate_limited(ctx):
            return None

        command, args = result

        if self.permission_manager.is_banned(ctx.real_user, ctx.is_registered):
            return "🚫 You are banned from using this bot."

        if not self.permission_manager.can_use_command(ctx.real_user, command.required_tier, ctx.is_registered):
            return f"⛔ This command requires {command.required_tier.value} tier or higher."

        try:
            return command.handler(ctx, args)
        except Exception as e:
            return f"❌ Error executing command: {str(e)}"

    def _shortest_alias(self, cmd: Command) -> str:
        """Return the shortest name/alias for a command."""
        all_names = cmd.all_names
        return min(all_names, key=len)

    def get_help(self, ctx: CommandContext, command_name: str = "") -> str:
        """Generate help text for all commands or a specific command."""
        if command_name:
            cmd_name_lower = command_name.lower()
            cmd = self.commands.get(cmd_name_lower)
            if not cmd:
                cmd = self.commands.get(self.alias_map.get(cmd_name_lower, ""))

            if cmd:
                if not self.permission_manager.can_use_command(ctx.real_user, cmd.required_tier, ctx.is_registered):
                    return f"⛔ Command '{command_name}' requires {cmd.required_tier.value} tier or higher."

                lines = [f"📖 Help: {cmd.name}"]
                if cmd.description:
                    lines.append(f"Description: {cmd.description}")
                if cmd.usage:
                    lines.append(f"Usage: {cmd.usage}")
                if cmd.aliases:
                    lines.append(f"Aliases: {', '.join(cmd.aliases)}")
                if cmd.required_tier != UserTier.USER:
                    lines.append(f"Required tier: {cmd.required_tier.value}")
                if cmd.help_text:
                    lines.append(f"\n{cmd.help_text}")
                return "\n".join(lines)

            suggestions = get_close_matches(command_name.lower(),
                                            list(self.commands.keys()) + list(self.alias_map.keys()),
                                            n=2, cutoff=0.5)
            if suggestions:
                return f"❓ Unknown command '{command_name}'. Did you mean: {', '.join(suggestions)}?"
            return f"❓ Unknown command '{command_name}'. Type 'help' for available commands."

        names = []
        for cmd in self.commands.values():
            if self.permission_manager.can_use_command(ctx.real_user, cmd.required_tier, ctx.is_registered):
                names.append(self._shortest_alias(cmd))

        return " ".join(names)

    def suggest_command(self, text: str) -> Optional[str]:
        """Suggest a command if the input is close to a valid one."""
        text_lower = text.lower().strip()
        all_names = list(self.commands.keys()) + list(self.alias_map.keys())
        suggestions = get_close_matches(text_lower, all_names, n=1, cutoff=0.6)
        return suggestions[0] if suggestions else None


def parse_flags(text: str, valid_flags: list = None) -> tuple[str, dict]:
    """
    Parse command flags from text.
    Returns (cleaned_text, flags_dict).

    Args:
        text: Input text to parse
        valid_flags: List of valid flag names to extract. If None, extracts all --flags.
    """
    flags = {}
    cleaned_text = text

    if valid_flags is None:
        pattern = r'--(\w+)\s+((?:(?!--).)+)'
    else:
        pattern = rf'--({"|".join(re.escape(f) for f in valid_flags)})\s+((?:(?!--).)+)'

    matches = re.findall(pattern, text, re.IGNORECASE)

    for match in matches:
        if isinstance(match, tuple):
            flag_name, flag_value = match
        else:
            continue

        flag_name = flag_name.lower()
        val = flag_value.strip()

        if valid_flags is not None:
            valid_names = [f.lower() for f in valid_flags]
            if flag_name not in valid_names:
                continue

        flags[flag_name] = val
        cleaned_text = re.sub(rf'--{re.escape(match[0])}\s+{re.escape(flag_value)}',
                              '', cleaned_text, flags=re.IGNORECASE).strip()

    return cleaned_text, flags


def handle_help(ctx: CommandContext, args: str) -> str:
    """Handle help command - shows general or specific command help."""
    dispatcher = create_dispatcher(ctx.permission_manager)
    return dispatcher.get_help(ctx, args.strip())


def handle_info(ctx: CommandContext, args: str) -> str:
    """Handle info command - shows bot status."""
    loc = ctx.location
    ctx_count = len(ctx.histories.get(loc, []))
    return (f"🤖 Bot Info [{loc}]\n"
            f"Temperature: {ctx.current_temperature}\n"
            f"Template: {ctx.chat_template}\n"
            f"Color: #{ctx.bot_color:06X}\n"
            f"Context: {ctx_count}/{ctx.context_limit} messages")


def handle_clear(ctx: CommandContext, args: str) -> str:
    """Handle clear command - clears message history for current location."""
    loc = ctx.location
    if loc in ctx.histories:
        ctx.histories[loc] = []
    return f"🧹 Context for {loc} cleared."


def handle_temp(ctx: CommandContext, args: str) -> Optional[str]:
    """Handle temp command - sets global temperature."""
    cleaned_args, flags = parse_flags(args, valid_flags=["value"])
    temp_val = flags.get("value", cleaned_args.strip() if cleaned_args else None)

    if not temp_val:
        return f"🌡️ Current temperature: {ctx.current_temperature}\nUsage: temp <value> (0.1-2.0) or temp --value <value>"

    try:
        new_temp = float(temp_val.split()[0] if isinstance(temp_val, str) else temp_val)
        clamped_temp = max(0.1, min(2.0, new_temp))
        return f"SET_TEMP:{clamped_temp}"
    except ValueError:
        return "❌ Temperature must be a number between 0.1 and 2.0"


def handle_gen(ctx: CommandContext, args: str) -> str:
    """Handle gen command - triggers text generation."""
    _, flags = parse_flags(args, valid_flags=["temp", "start", "imitate"])

    gen_temp = ctx.current_temperature
    if "temp" in flags:
        try:
            temp_val = float(flags["temp"])
            gen_temp = max(0.1, min(2.0, temp_val))
        except ValueError:
            pass

    gen_nick = flags.get("imitate", ctx.bot_nick_default)
    gen_start = flags.get("start", "")
    return f"GEN_TRIGGER:{gen_temp}|{gen_nick}|{gen_start}"


def handle_imitate(ctx: CommandContext, args: str) -> str:
    """Handle imitate command - triggers generation with custom nickname."""
    cleaned_args, flags = parse_flags(args, valid_flags=["nick", "temp", "start"])

    gen_nick = flags.get("nick")
    if not gen_nick and cleaned_args:
        gen_nick = cleaned_args.split()[0]

    if not gen_nick:
        return "❌ Please specify a nickname to imitate.\nUsage: imitate --nick <nickname> [--temp <value>] [--start <text>] or imitate <nickname>"

    gen_temp = ctx.current_temperature
    if "temp" in flags:
        try:
            temp_val = float(flags["temp"])
            gen_temp = max(0.1, min(2.0, temp_val))
        except ValueError:
            pass

    gen_start = flags.get("start", "")
    return f"GEN_TRIGGER:{gen_temp}|{gen_nick}|{gen_start}"


def handle_tier(ctx: CommandContext, args: str) -> str:
    """Handle tier command - check or set user tiers (admin only)."""
    cleaned_args, flags = parse_flags(args, valid_flags=["user", "tier"])

    username = flags.get("user")
    tier_name = flags.get("tier")

    if not username and not tier_name and cleaned_args:
        parts = cleaned_args.split(maxsplit=1)
        if len(parts) == 1:
            username = parts[0]
        else:
            username, tier_name = parts[0], parts[1]
    elif username and not tier_name and cleaned_args:
        tier_name = cleaned_args.strip()
    elif tier_name and not username and cleaned_args:
        username = cleaned_args.strip()

    if not username:
        user_tier = ctx.permission_manager.get_tier(ctx.real_user)
        return f"👤 Your tier: {user_tier.value}"

    if not tier_name:
        tier = ctx.permission_manager.get_tier(username)
        return f"👤 {username}'s tier: {tier.value}"

    tier_name = tier_name.lower()
    valid_tiers = [t.value for t in UserTier]
    if tier_name not in valid_tiers:
        return f"❌ Invalid tier. Valid tiers: {', '.join(valid_tiers)}"

    try:
        new_tier = UserTier(tier_name)
        ctx.permission_manager.set_tier(username, new_tier)
        return f"SYNC_TIERS:✅ Set {username}'s tier to {new_tier.value}"
    except ValueError:
        return f"❌ Invalid tier: {tier_name}"


def handle_untier(ctx: CommandContext, args: str) -> str:
    """Handle untier command - remove custom tier from user (admin only)."""
    cleaned_args, flags = parse_flags(args, valid_flags=["user"])
    username = flags.get("user", cleaned_args.strip() if cleaned_args else None)

    if not username:
        return "❌ Usage: untier --user <username> or untier <username>"

    if ctx.permission_manager.remove_user(username):
        return f"SYNC_TIERS:✅ Removed custom tier from {username} (reverted to user)"
    return f"❓ {username} doesn't have a custom tier"


def handle_listtiers(ctx: CommandContext, args: str) -> str:
    """Handle listtiers command - list all users with custom tiers (admin only)."""
    users = ctx.permission_manager.get_all_users()
    if not users:
        return "📋 No users with custom tiers"

    lines = ["📋 Users with custom tiers:"]
    for username, tier in sorted(users.items()):
        lines.append(f"  • {username}: {tier}")
    return "\n".join(lines)


def handle_checktier(ctx: CommandContext, args: str) -> str:
    """Handle checktier command - check your current tier."""
    user_tier = ctx.permission_manager.get_tier(ctx.real_user, ctx.is_registered)
    lines = [f"👤 Your tier: {user_tier.value}"]

    if user_tier == UserTier.BANNED:
        lines.append("🚫 You are banned from using this bot")
    elif user_tier == UserTier.ADMIN:
        lines.append("🔒 You have admin privileges")
    elif user_tier == UserTier.MODERATOR:
        lines.append("🔹 You have moderator privileges")

    return "\n".join(lines)


def handle_kill(ctx: CommandContext, args: str) -> str:
    """Handle kill command - immediately stop the bot with confirmation."""
    user_id = ctx.real_user if ctx.is_registered else f"anon_{ctx.real_user}"
    
    if "--yes" in args.lower():
        if user_id in _pending_stops:
            _pending_stops.remove(user_id)
            return "KILL_BOT:🛑 Kill command received. Shutting down immediately."
        else:
            return "❌ You must run 'stop' first before 'stop --yes'."
    else:
        _pending_stops.add(user_id)
        return "⚠️ Are you sure you want to stop the bot? Run 'stop --yes' to confirm."


def handle_model(ctx: CommandContext, args: str) -> str:
    """Handle model command - switch to a different Hugging Face model."""
    cleaned_args, flags = parse_flags(args, valid_flags=["name"])
    model_name = flags.get("name", cleaned_args.strip() if cleaned_args else None)

    if not model_name:
        return "🤖 Usage: model <huggingface_model_name> or model --name <model_name>"

    return f"SET_MODEL:{model_name}"


def handle_grouptier(ctx: CommandContext, args: str) -> str:
    """Handle grouptier command - set tier for groups (%ANYONE%, %ANONS%, %REG%)."""
    cleaned_args, flags = parse_flags(args, valid_flags=["group", "tier"])

    group_name = flags.get("group", cleaned_args.split()[0] if cleaned_args else "")
    tier_name = flags.get("tier", cleaned_args.split()[1] if len(cleaned_args.split()) > 1 else "")

    if not group_name:
        lines = ["👥 Current Group Tiers:"]
        for group in [GroupTier.ANYONE, GroupTier.ANONS, GroupTier.REG]:
            tier = ctx.permission_manager.get_group_tier(group)
            status = tier.value if tier else "disabled"
            lines.append(f"  • %{group.value.upper()}%: {status}")
        return "\n".join(lines)

    group_name = group_name.upper()
    if not group_name.startswith("%") or not group_name.endswith("%"):
        group_name = f"%{group_name}%"

    group_map = {
        "%ANYONE%": GroupTier.ANYONE,
        "%ANONS%": GroupTier.ANONS,
        "%REG%": GroupTier.REG
    }

    group = group_map.get(group_name.upper())
    if not group:
        return f"❌ Invalid group. Valid groups: {', '.join(group_map.keys())}"

    if not tier_name:
        tier = ctx.permission_manager.get_group_tier(group)
        status = tier.value if tier else "disabled"
        return f"👥 Group %{group.value.upper()}% tier: {status}"

    tier_name = tier_name.lower()
    valid_tiers = [t.value for t in UserTier]
    if tier_name not in valid_tiers:
        return f"❌ Invalid tier. Valid tiers: {', '.join(valid_tiers)}"

    try:
        new_tier = UserTier(tier_name)
        ctx.permission_manager.set_group_tier(group, new_tier)
        return f"SYNC_TIERS:✅ Set %{group.value.upper()}% tier to {new_tier.value}"
    except ValueError:
        return f"❌ Invalid tier: {tier_name}"


def handle_color(ctx: CommandContext, args: str) -> str:
    """Handle color command - change bot color (admin only)."""
    cleaned_args, flags = parse_flags(args, valid_flags=["value"])
    color_hex = flags.get("value", cleaned_args.strip() if cleaned_args else "")

    if not color_hex:
        return f"❌ Usage: color --value <hex> or color <hex>\nExample: color #FF0000 or color 0xFF0000"

    # Validate hex format
    color_hex = color_hex.strip()
    if color_hex.startswith("#"):
        color_hex = color_hex[1:]
    elif color_hex.startswith("0x"):
        color_hex = color_hex[2:]

    if not all(c in "0123456789ABCDEFabcdef" for c in color_hex):
        return "❌ Invalid hex color. Use format #RRGGBB or 0xRRGGBB"

    return f"SET_COLOR:#{color_hex.upper()}"


def handle_channel(ctx: CommandContext, args: str) -> str:
    """Handle channel command - add or remove chat channels."""
    parts = args.split()
    if not parts:
        return "❌ Usage: channel add <url|/world> or channel remove <url|name>"

    action = parts[0].lower()
    if action == "add":
        if len(parts) < 2:
            return "❌ Usage: channel add <url|/world>"
        target = parts[1]
        return f"CHANNEL_ADD:{target}"
    elif action == "remove":
        if len(parts) < 2:
            return "❌ Usage: channel remove <url|name>"
        target = parts[1]
        return f"CHANNEL_REMOVE:{target}"
    else:
        return f"❌ Unknown action: {action}. Use 'add' or 'remove'."


def handle_template(ctx: CommandContext, args: str) -> str:
    """Handle template command - switch between chat log context formats."""
    cleaned_args, flags = parse_flags(args, valid_flags=["name"])
    template_name = flags.get("name", cleaned_args.strip().lower() if cleaned_args else None)

    if not template_name:
        return f"📝 Current template: {ctx.chat_template}\nAvailable templates: owot, role\nUsage: template <name>"

    if template_name not in ["owot", "role"]:
        return "❌ Invalid template. Available: owot, role"

    return f"SET_TEMPLATE:{template_name}"


def create_dispatcher(permission_manager: PermissionManager) -> CommandDispatcher:
    """Create and configure the command dispatcher with all commands."""
    dispatcher = CommandDispatcher(permission_manager)

    dispatcher.register(Command(
        name="owotgpt help",
        handler=handle_help,
        aliases=["help", "owotgpt h", "h"],
        required_tier=UserTier.USER,
        description="Show available commands or detailed help for a specific command",
        usage="help [command]",
        help_text="Without arguments, shows all available commands. With a command name, shows detailed usage."
    ))

    dispatcher.register(Command(
        name="owotgpt info",
        handler=handle_info,
        aliases=["info", "owotgpt i"],
        required_tier=UserTier.USER,
        description="Show bot status and configuration",
        usage="info"
    ))

    dispatcher.register(Command(
        name="owotgpt checktier",
        handler=handle_checktier,
        aliases=["checktier"],
        required_tier=UserTier.USER,
        description="Check your current permission tier",
        usage="checktier",
        help_text="Shows your current tier and what privileges you have."
    ))

    dispatcher.register(Command(
        name="owotgpt clear",
        handler=handle_clear,
        aliases=["clear", "owotgpt clearhistory", "clearhistory"],
        required_tier=UserTier.MODERATOR,
        description="Clear message history for current location",
        usage="clear"
    ))

    dispatcher.register(Command(
        name="owotgpt temp",
        handler=handle_temp,
        aliases=["temp", "owotgpt temperature", "temperature"],
        required_tier=UserTier.MODERATOR,
        description="Set global temperature for text generation",
        usage="temp <value> or temp --value <value>",
        help_text="Temperature controls randomness. Lower values (0.1-0.5) produce more focused text, higher values (1.0-2.0) produce more creative text."
    ))

    dispatcher.register(Command(
        name="owotgpt gen",
        handler=handle_gen,
        aliases=["gen", "owotgpt generate", "generate", "owotgpt g", "g"],
        required_tier=UserTier.USER,
        description="Generate text using GPT-2",
        usage="gen [--temp <value>] [--start <text>] [--imitate <nick>]",
        help_text="Generates text based on conversation context. Use --temp to override temperature, --start to provide seed text, --imitate to use a custom nickname."
    ))

    dispatcher.register(Command(
        name="owotgpt imitate",
        handler=handle_imitate,
        aliases=["imitate", "owotgpt im", "im"],
        required_tier=UserTier.USER,
        description="Generate text with a custom nickname",
        usage="imitate --nick <nickname> [--temp <value>] [--start <text>] or imitate <nickname>",
        help_text="Similar to gen, but uses a custom nickname for the response. Supports --nick, --temp, and --start flags."
    ))

    dispatcher.register(Command(
        name="owotgpt tier",
        handler=handle_tier,
        aliases=["tier", "settier", "owotgpt settier", "set tier"],
        required_tier=UserTier.ADMIN,
        description="Set or check user tiers",
        usage="tier [--user <username>] [--tier <admin|moderator|user|banned>] or tier [username] [tier]",
        help_text="Without arguments: shows your tier. With --user: shows that user's tier. With --user and --tier: sets the user's tier. Changes are saved permanently."
    ))

    dispatcher.register(Command(
        name="owotgpt untier",
        handler=handle_untier,
        aliases=["untier", "owotgpt removetier", "removetier"],
        required_tier=UserTier.ADMIN,
        description="Remove custom tier from a user",
        usage="untier --user <username> or untier <username>",
        help_text="Removes a user's custom tier, reverting them to 'user' tier."
    ))

    dispatcher.register(Command(
        name="owotgpt listtiers",
        handler=handle_listtiers,
        aliases=["listtiers", "owotgpt listusers", "listusers"],
        required_tier=UserTier.ADMIN,
        description="List all users with custom tiers",
        usage="listtiers",
        help_text="Shows all users who have been assigned non-default tiers."
    ))

    dispatcher.register(Command(
        name="owotgpt kill",
        handler=handle_kill,
        aliases=["kill", "owotgpt stop", "stop"],
        required_tier=UserTier.ADMIN,
        description="Immediately stop the bot (requires confirmation)",
        usage="stop then stop --yes",
        help_text="Stops all websocket handling and exits the process. Requires 'stop --yes' after the initial 'stop' command."
    ))

    dispatcher.register(Command(
        name="owotgpt model",
        handler=handle_model,
        aliases=["model", "setmodel", "owotgpt setmodel"],
        required_tier=UserTier.ADMIN,
        description="Switch the GPT-2 model",
        usage="model <huggingface_model_name>",
        help_text="Downloads and switches to a different Hugging Face model. This may take some time."
    ))

    dispatcher.register(Command(
        name="my son",
        handler=handle_gen,
        aliases=[],
        required_tier=UserTier.MODERATOR,
        description="Legacy trigger for text generation (moderator only)",
        usage="my son [--temp <value>] [--start <text>]",
        help_text="Moderator-only legacy command. Functions like 'gen' but only moderators+ can use it."
    ))

    dispatcher.register(Command(
        name="owotgpt grouptier",
        handler=handle_grouptier,
        aliases=["grouptier", "owotgpt setgrouptier", "setgrouptier"],
        required_tier=UserTier.ADMIN,
        description="Set or check group tiers for %ANYONE%, %ANONS%, %REG%",
        usage="grouptier [--group <group>] [--tier <tier>]",
        help_text="Without arguments: shows all group tiers. With --group: shows that group's tier. With --group and --tier: sets the group's tier. Groups affect users without explicit tier assignments."
    ))

    dispatcher.register(Command(
        name="owotgpt color",
        handler=handle_color,
        aliases=["color", "owotgpt setcolor", "setcolor"],
        required_tier=UserTier.ADMIN,
        description="Change the bot's chat color",
        usage="color --value <hex> or color <hex>",
        help_text="Changes the bot's message color. Use hex format like #FF0000 (red) or 0x00FF00 (green)."
    ))

    dispatcher.register(Command(
        name="owotgpt channel",
        handler=handle_channel,
        aliases=["channel", "owotgpt chan", "chan"],
        required_tier=UserTier.MODERATOR,
        description="Add or remove chat channels",
        usage="channel <add|remove> <url|name>",
        help_text="Adds a new websocket connection or removes an existing one. 'channel add /world' adds an OWoT world, 'channel remove global' disables the global chat location."
    ))

    dispatcher.register(Command(
        name="owotgpt template",
        handler=handle_template,
        aliases=["template", "format", "owotgpt format"],
        required_tier=UserTier.MODERATOR,
        description="Switch the chat context format",
        usage="template <owot|role>",
        help_text="Changes how messages are formatted in the model's context. 'owot' uses the standard '[*id] nick: message' format. 'role' uses 'role: assistant' or 'role: (nick)'."
    ))

    return dispatcher

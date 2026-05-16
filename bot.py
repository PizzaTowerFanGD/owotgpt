import asyncio
import json
import os
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from http.cookiejar import CookieJar

import torch
import websockets
from transformers import AutoModelForCausalLM, AutoTokenizer

from commands import CommandContext, create_dispatcher
from permissions import PermissionManager

WORLD_NAME = os.getenv("OWOT_WORLD_NAME", "")
BOT_DOMAIN = os.getenv("OWOT_DOMAIN", "ourworldoftext.com")
NETWORK_DOMAIN = os.getenv("OWOT_NETWORK_DOMAIN", BOT_DOMAIN)
NETWORK_WORLD_NAME = "...network"
MODEL_NAME = "Pomni/owotgpt1.3"
BOT_NICK_DEFAULT = os.getenv("OWOT_BOT_NICK", "OWoTGPT")
ADMIN_USER = "gimmickCellar"
CONTEXT_LIMIT = 15
CONTEXT_TOKEN_LIMIT = 900
MESSAGE_CHAR_LIMIT = 400
RECONNECT_DELAY_SECONDS = 5
_bot_color_env = os.getenv("OWOT_BOT_COLOR", "0x0077CC")
if _bot_color_env.startswith("#"):
    BOT_COLOR = int(_bot_color_env[1:], 16)
else:
    BOT_COLOR = int(_bot_color_env, 16)
TIERS_GIST_ID_ENV = "OWOTGPT_TIERS_GIST_ID"
GITHUB_TOKEN_ENV = "GITHUB_TOKEN"
OWOT_TOKEN_ENV = "OWOT_TOKEN"
OWOT_PASSWORD_ENV = "OWOT_PASSWORD"
BOT_LOGIN_NAME = "owotgpt."
UVIAS_LOGIN_URL = "https://uvias.com/api/auth/uvias"
OWOT_TOKEN_CHECK_URL = "https://ourworldoftext.com/accounts/member_autocomplete/"

current_temperature = float(os.getenv("OWOT_BOT_TEMP", "1.3"))
chat_template = os.getenv("OWOT_BOT_TEMPLATE", "owot").lower()
shutdown_event = None
active_tasks = {}
disabled_locations = set()


def log(msg):
    print(msg, flush=True)


permission_manager = PermissionManager()
command_dispatcher = create_dispatcher(permission_manager)


def create_command_context(ws, loc, real_user, my_id, histories, current_temp, bot_color, perm_manager, is_registered, template):
    return CommandContext(
        websocket=ws,
        location=loc,
        real_user=real_user,
        my_id=my_id,
        admin_user=ADMIN_USER,
        bot_nick_default=BOT_NICK_DEFAULT,
        histories=histories,
        current_temperature=current_temp,
        bot_color=bot_color,
        context_limit=CONTEXT_LIMIT,
        context_token_limit=CONTEXT_TOKEN_LIMIT,
        permission_manager=perm_manager,
        is_registered=is_registered,
        chat_template=template
    )


async def send_response(ws, message, loc, nickname=None, color=None, is_admin=False):
    if nickname is None:
        nickname = BOT_NICK_DEFAULT
    if color is None:
        color = BOT_COLOR
    if isinstance(color, int):
        color = f"#{color:06X}"
    if len(message) > MESSAGE_CHAR_LIMIT:
        message = message[:MESSAGE_CHAR_LIMIT - 3] + "..."
    if message.startswith("/block") and not is_admin:
        message = "\u200B" + message
    await ws.send(json.dumps({
        "kind": "chat",
        "nickname": nickname,
        "message": message,
        "location": loc,
        "color": color
    }))


def build_ws_url(domain: str, world_name: str) -> str:
    world_name = world_name.strip("/")
    path = f"/{world_name}/ws/" if world_name else "/ws/"
    return f"wss://{domain}{path}"


def check_token(token: str) -> bool:
    request = urllib.request.Request(
        OWOT_TOKEN_CHECK_URL,
        headers={"Cookie": f"token={token}"}
    )

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            status_code = getattr(response, "status", response.getcode())
            return status_code not in {403, 500}
    except Exception:
        return False


def login_with_password(password: str) -> str:
    login_data = urllib.parse.urlencode({
        "service": "uvias",
        "loginname": BOT_LOGIN_NAME,
        "pass": password,
        "persistent": "on"
    }).encode("utf-8")

    cookie_jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    request = urllib.request.Request(UVIAS_LOGIN_URL, data=login_data, method="POST")

    with opener.open(request, timeout=15):
        pass

    for cookie in cookie_jar:
        if cookie.name == "uviastoken":
            return cookie.value

    raise RuntimeError("No uviastoken cookie returned from login.")


def get_auth_token() -> str:
    token = os.getenv(OWOT_TOKEN_ENV, "").strip()
    if token:
        if check_token(token):
            return token
        log("OWOT_TOKEN is invalid; falling back to password login.")

    password = os.getenv(OWOT_PASSWORD_ENV, "").strip()
    if not password:
        raise RuntimeError(
            f"Missing authentication. Set {OWOT_TOKEN_ENV} or {OWOT_PASSWORD_ENV}."
        )

    token = login_with_password(password)
    if not check_token(token):
        raise RuntimeError("Password login succeeded but returned an invalid token.")
    return token


def build_headers() -> dict:
    token = get_auth_token()
    return {
        "Cookie": f"token={token}"
    }


def load_tiers_from_github() -> bool:
    gist_id = os.getenv(TIERS_GIST_ID_ENV)
    github_token = os.getenv(GITHUB_TOKEN_ENV)
    if not gist_id or not github_token:
        log("Tier sync disabled: missing GitHub gist ID or token.")
        return False

    import urllib.request
    import urllib.error

    request = urllib.request.Request(
        f"https://api.github.com/gists/{gist_id}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {github_token}",
            "X-GitHub-Api-Version": "2022-11-28"
        }
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        tier_file = payload.get("files", {}).get("user_permissions.json", {})
        content = tier_file.get("content")
        truncated = tier_file.get("truncated", False)
        if not content or truncated:
            log("No remote tier data found in gist.")
            return False
        permission_manager.replace_all(json.loads(content))
        permission_manager.ensure_admin(ADMIN_USER)
        log("Loaded user tiers from GitHub gist.")
        return True
    except urllib.error.HTTPError as exc:
        log(f"Failed to load tiers from GitHub gist: HTTP {exc.code}")
    except Exception as exc:
        log(f"Failed to load tiers from GitHub gist: {exc}")
    return False


def save_tiers_to_github() -> bool:
    gist_id = os.getenv(TIERS_GIST_ID_ENV)
    github_token = os.getenv(GITHUB_TOKEN_ENV)
    if not gist_id or not github_token:
        return False

    import urllib.request
    import urllib.error

    body = json.dumps({
        "files": {
            "user_permissions.json": {
                "content": json.dumps(permission_manager.export(), indent=2, sort_keys=True)
            }
        }
    }).encode("utf-8")

    request = urllib.request.Request(
        f"https://api.github.com/gists/{gist_id}",
        data=body,
        method="PATCH",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {github_token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json"
        }
    )

    try:
        with urllib.request.urlopen(request, timeout=30):
            pass
        log("Synced user tiers to GitHub gist.")
        return True
    except urllib.error.HTTPError as exc:
        log(f"Failed to sync tiers to GitHub gist: HTTP {exc.code}")
    except Exception as exc:
        log(f"Failed to sync tiers to GitHub gist: {exc}")
    return False


log("--- Starting Bot Initialization ---")

load_tiers_from_github()
permission_manager.ensure_admin(ADMIN_USER)

log(f"Loading model {MODEL_NAME}...")
try:
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForCausalLM.from_pretrained(MODEL_NAME, use_safetensors=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)
    log(f"Model loaded successfully on {device}!")
except Exception as e:
    log(f"ERROR: Failed to load model: {e}")
    sys.exit(1)

executor = ThreadPoolExecutor(max_workers=1)

histories = {
    "page": [],
    "global": [],
    "network": []
}


def format_message(msg_data, template="owot", my_id=None):
    mid = str(msg_data.get("id", "0"))
    nick = msg_data.get("nickname", "")
    real_user = msg_data.get("realUsername", "")
    text = msg_data.get("message", "")
    is_registered = msg_data.get("registered", False)

    if is_registered and real_user:
        display_name = nick if nick and nick.lower() != real_user.lower() else real_user
    else:
        display_name = nick if nick else "Anonymous"

    if template == "role":
        role = "assistant" if mid == str(my_id) else f"({display_name})"
        return f"role: {role}: {text}"

    if template == "instruct":
        role = "Assistant" if mid == str(my_id) else f"User ({display_name})"
        return f"{role}: {text}"

    if template == "chatml":
        role = "assistant" if mid == str(my_id) else f"user name={display_name}"
        return f"<|im_start|>{role}\n{text}<|im_end|>"

    # Default to 'owot' format
    if is_registered and real_user:
        return f"[*{mid}] {display_name}: {text}"
    return f"[*{mid}] {nick}: {text}" if nick else f"[{mid}]: {text}"


def trim_history_by_tokens(history, token_limit, template, my_id):
    """Trim history to stay within token limit."""
    if len(history) == 0:
        return []

    # Format history based on current template
    formatted_history = [format_message(msg, template, my_id) for msg in history]

    # Calculate total tokens
    full_text = "\n".join(formatted_history)
    tokens = tokenizer(full_text, return_tensors="pt")
    total_tokens = len(tokens["input_ids"][0])

    # If within limit, return as-is
    if total_tokens <= token_limit:
        return formatted_history

    # Binary search to find the maximum number of messages that fit
    left, right = 1, len(history)
    best_count = 0

    while left <= right:
        mid = (left + right) // 2
        test_formatted = formatted_history[-mid:]
        test_text = "\n".join(test_formatted)
        test_tokens = tokenizer(test_text, return_tensors="pt")
        token_count = len(test_tokens["input_ids"][0])

        if token_count <= token_limit:
            best_count = mid
            left = mid + 1
        else:
            right = mid - 1

    # Return the best number of messages from the end
    return formatted_history[-best_count:] if best_count > 0 else []


def do_generate(prompt_str, temp):
    inputs = tokenizer(prompt_str, return_tensors="pt").to(device)
    output = model.generate(
        **inputs,
        max_new_tokens=100,
        temperature=temp,
        do_sample=True,
        pad_token_id=tokenizer.eos_token_id
    )
    generated = tokenizer.decode(output[0], skip_special_tokens=True)
    result = generated[len(prompt_str):].strip()
    if '\n' in result:
        result = result.split('\n')[0]
    return result


def do_load_model(new_model_name):
    global model, tokenizer, MODEL_NAME
    try:
        log(f"Loading new model: {new_model_name}...")
        new_tokenizer = AutoTokenizer.from_pretrained(new_model_name)
        new_model = AutoModelForCausalLM.from_pretrained(new_model_name, use_safetensors=True)
        new_model.to(device)
        
        # Successfully loaded, now replace old model
        tokenizer = new_tokenizer
        model = new_model
        MODEL_NAME = new_model_name
        log(f"Successfully switched to model: {new_model_name}")
        return True, f"✅ Successfully switched to model: {new_model_name}"
    except Exception as e:
        log(f"Failed to load model {new_model_name}: {e}")
        return False, f"❌ Failed to load model {new_model_name}: {str(e)}"


async def shutdown_bot(reason: str):
    if shutdown_event is None or shutdown_event.is_set():
        return
    log(reason)
    shutdown_event.set()


async def handle_command_response(ws, response_msg, loc, my_id, is_admin=False):
    global current_temperature, chat_template

    if response_msg.startswith("GEN_TRIGGER:"):
        _, params = response_msg.split(":", 1)
        gen_temp_str, gen_nick, gen_start = params.split("|", 2)
        gen_temp = float(gen_temp_str)

        log(f"[{loc}] Generating...")
        trimmed_history = trim_history_by_tokens(histories[loc], CONTEXT_TOKEN_LIMIT, chat_template, my_id)
        
        # Build prompt using current template
        bot_msg_data = {"id": my_id, "nickname": gen_nick, "message": gen_start, "registered": True}
        prompt = "\n".join(trimmed_history) + "\n" + format_message(bot_msg_data, chat_template, my_id)

        loop = asyncio.get_running_loop()
        output = await loop.run_in_executor(executor, do_generate, prompt, gen_temp)
        gen_response = (gen_start + " " + output.strip()).strip()

        if gen_response:
            await send_response(ws, gen_response, loc, gen_nick, is_admin=is_admin)
            histories[loc].append({
                "id": my_id,
                "nickname": gen_nick,
                "message": gen_response,
                "registered": True
            })
        return

    if response_msg.startswith("SET_TEMP:"):
        _, temp_str = response_msg.split(":", 1)
        current_temperature = float(temp_str)
        await send_response(ws, f"🌡️ Global temperature set to {current_temperature}", loc, is_admin=is_admin)
        return

    if response_msg.startswith("SET_TEMPLATE:"):
        _, template_name = response_msg.split(":", 1)
        chat_template = template_name
        await send_response(ws, f"📝 Chat template changed to {template_name}", loc, is_admin=is_admin)
        return

    if response_msg.startswith("SET_COLOR:"):
        _, color_hex = response_msg.split(":", 1)
        global BOT_COLOR
        try:
            if color_hex.startswith("#"):
                BOT_COLOR = int(color_hex[1:], 16)
            elif color_hex.startswith("0x"):
                BOT_COLOR = int(color_hex, 16)
            else:
                BOT_COLOR = int(color_hex, 16)
            await send_response(ws, f"🎨 Bot color changed to {color_hex}", loc, is_admin=is_admin)
        except ValueError:
            await send_response(ws, f"❌ Invalid color format. Use hex like #FF0000 or 0xFF0000", loc, is_admin=is_admin)
        return

    if response_msg.startswith("SYNC_TIERS:"):
        _, user_message = response_msg.split(":", 1)
        synced = save_tiers_to_github()
        suffix = " (synced to GitHub)" if synced else ""
        await send_response(ws, f"{user_message}{suffix}", loc, is_admin=is_admin)
        return

    if response_msg.startswith("KILL_BOT:"):
        _, user_message = response_msg.split(":", 1)
        with suppress(Exception):
            await send_response(ws, user_message, loc, is_admin=is_admin)
        await shutdown_bot("Kill command triggered. Exiting bot.")
        return

    if response_msg.startswith("SET_MODEL:"):
        _, new_model_name = response_msg.split(":", 1)
        await send_response(ws, f"🔄 Switching model to {new_model_name}... (this may take a minute)", loc, is_admin=is_admin)
        
        loop = asyncio.get_running_loop()
        success, message = await loop.run_in_executor(executor, do_load_model, new_model_name)
        await send_response(ws, message, loc, is_admin=is_admin)
        return

    if response_msg.startswith("CHANNEL_ADD:"):
        _, target = response_msg.split(":", 1)
        if target.lower() == "global":
            if "global" in disabled_locations:
                disabled_locations.remove("global")
                await send_response(ws, "✅ Global chat re-enabled.", loc, is_admin=is_admin)
            else:
                await send_response(ws, "❓ Global chat is already enabled.", loc, is_admin=is_admin)
            return

        url = target
        name = target
        is_net = False
        if target.startswith("/"):
            url = build_ws_url(BOT_DOMAIN, target)
            name = target
        elif target.startswith("ws://") or target.startswith("wss://"):
            url = target
            name = target
        else:
            # Assume it's a world name
            url = build_ws_url(BOT_DOMAIN, target)
            name = "/" + target if not target.startswith("/") else target

        if name in active_tasks:
            await send_response(ws, f"❓ Channel {name} is already active.", loc, is_admin=is_admin)
            return

        active_tasks[name] = asyncio.create_task(handle_websocket(url, name, is_network=is_net))
        await send_response(ws, f"✅ Added channel: {name}", loc, is_admin=is_admin)
        return

    if response_msg.startswith("CHANNEL_REMOVE:"):
        _, target = response_msg.split(":", 1)
        if target.lower() == "global":
            disabled_locations.add("global")
            await send_response(ws, "✅ Global chat disabled.", loc, is_admin=is_admin)
            return

        # Try to find task by name
        name = target
        if name not in active_tasks and not name.startswith("/") and not name.startswith("ws"):
            name = "/" + target

        if name in active_tasks:
            task = active_tasks.pop(name)
            task.cancel()
            await send_response(ws, f"✅ Removed channel: {name}", loc, is_admin=is_admin)
            return

        await send_response(ws, f"❓ Channel {target} not found.", loc, is_admin=is_admin)
        return

    await send_response(ws, response_msg, loc, is_admin=is_admin)


async def handle_websocket(url, name, is_network=False):
    global histories

    while not shutdown_event.is_set():
        ws = None
        headers = None
        try:
            headers = build_headers()
            log(f"Connecting to {url} (name: {name})...")
            ws = await websockets.connect(url, additional_headers=headers or None)
            my_id = "0"

            if is_network:
                await ws.send(json.dumps({"kind": "chathistory"}))

            while not shutdown_event.is_set():
                raw_data = await ws.recv()
                data = json.loads(raw_data)

                if data.get("kind") == "channel":
                    my_id = data.get("id")
                    log(f"Connected to {url} - ID: {my_id}")
                    continue

                if data.get("kind") != "chat":
                    continue

                sender_id = data.get("id")
                if str(sender_id) == str(my_id):
                    continue

                loc = data.get("location", "page")
                if is_network:
                    loc = "network"
                elif loc == "page" and name:
                    # Map 'page' to the world name for unique history keys
                    loc = name.strip("/") or "page"
                
                if loc in disabled_locations:
                    continue

                if is_network and loc == "global":
                    continue

                msg_text = data.get("message", "")
                real_user = data.get("realUsername", "")
                is_registered = data.get("registered", False)

                if loc not in histories:
                    histories[loc] = []

                ctx = create_command_context(ws, loc, real_user, my_id, histories, current_temperature, BOT_COLOR, permission_manager, is_registered, chat_template)
                response_msg = command_dispatcher.dispatch(msg_text, ctx)

                if response_msg:
                    sender_is_admin = permission_manager.is_admin(real_user, is_registered)
                    await handle_command_response(ws, response_msg, loc, my_id, is_admin=sender_is_admin)
                    continue

                histories[loc].append({
                    "id": data.get("id"),
                    "nickname": data.get("nickname"),
                    "realUsername": data.get("realUsername"),
                    "message": data.get("message"),
                    "registered": data.get("registered")
                })
                if len(histories[loc]) > CONTEXT_LIMIT:
                    histories[loc].pop(0)

        except websockets.ConnectionClosed as exc:
            if shutdown_event.is_set():
                break
            log(f"Lost connection to {url}: {exc}. Reconnecting in {RECONNECT_DELAY_SECONDS}s...")
        except Exception as exc:
            if shutdown_event.is_set():
                break
            error_context = "authentication" if headers is None else "connection"
            log(f"Error during {error_context} setup for {url}: {exc}. Reconnecting in {RECONNECT_DELAY_SECONDS}s...")
        finally:
            if ws is not None:
                with suppress(Exception):
                    await ws.close()

        if shutdown_event.is_set():
            break
        await asyncio.sleep(RECONNECT_DELAY_SECONDS)


async def main():
    global shutdown_event

    shutdown_event = asyncio.Event()
    world_url = build_ws_url(BOT_DOMAIN, WORLD_NAME)
    network_url = build_ws_url(NETWORK_DOMAIN, NETWORK_WORLD_NAME)

    # Initial tasks
    world_name = WORLD_NAME if WORLD_NAME.startswith("/") else "/" + WORLD_NAME
    active_tasks[world_name] = asyncio.create_task(handle_websocket(world_url, world_name, is_network=False))
    active_tasks["network"] = asyncio.create_task(handle_websocket(network_url, "network", is_network=True))

    try:
        await shutdown_event.wait()
    finally:
        for task in active_tasks.values():
            task.cancel()
        await asyncio.gather(*active_tasks.values(), return_exceptions=True)
        executor.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("Keyboard interrupt received. Exiting bot.")
        sys.exit(0)

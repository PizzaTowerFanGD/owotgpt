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
BOT_NICK_DEFAULT = "OWoTGPT"
ADMIN_USER = "gimmickCellar"
CONTEXT_LIMIT = 15
MESSAGE_CHAR_LIMIT = 400
RECONNECT_DELAY_SECONDS = 5
TIERS_GIST_ID_ENV = "OWOTGPT_TIERS_GIST_ID"
GITHUB_TOKEN_ENV = "GITHUB_TOKEN"
OWOT_TOKEN_ENV = "OWOT_TOKEN"
OWOT_PASSWORD_ENV = "OWOT_PASSWORD"
BOT_LOGIN_NAME = "owotgpt."
UVIAS_LOGIN_URL = "https://uvias.com/api/auth/uvias"
OWOT_TOKEN_CHECK_URL = "https://ourworldoftext.com/accounts/member_autocomplete/"

current_temperature = 1.3
shutdown_event = None


def log(msg):
    print(msg, flush=True)


permission_manager = PermissionManager()
command_dispatcher = create_dispatcher(permission_manager)


def create_command_context(ws, loc, real_user, my_id, histories, current_temp, perm_manager):
    return CommandContext(
        websocket=ws,
        location=loc,
        real_user=real_user,
        my_id=my_id,
        admin_user=ADMIN_USER,
        bot_nick_default=BOT_NICK_DEFAULT,
        histories=histories,
        current_temperature=current_temp,
        context_limit=CONTEXT_LIMIT,
        permission_manager=perm_manager
    )


async def send_response(ws, message, loc, nickname=BOT_NICK_DEFAULT):
    if len(message) > MESSAGE_CHAR_LIMIT:
        message = message[:MESSAGE_CHAR_LIMIT - 3] + "..."
    await ws.send(json.dumps({
        "kind": "chat",
        "nickname": nickname,
        "message": message,
        "location": loc,
        "color": 0
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


def format_message(msg_data):
    mid = msg_data.get("id", "0")
    nick = msg_data.get("nickname", "")
    real_user = msg_data.get("realUsername", "")
    text = msg_data.get("message", "")
    is_registered = msg_data.get("registered", False)
    if is_registered:
        display_name = nick if nick and nick.lower() != real_user.lower() else real_user
        return f"[*{mid}] {display_name}: {text}"
    return f"[*{mid}] {nick}: {text}" if nick else f"[{mid}]: {text}"


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


async def shutdown_bot(reason: str):
    if shutdown_event is None or shutdown_event.is_set():
        return
    log(reason)
    shutdown_event.set()


async def handle_command_response(ws, response_msg, loc, my_id):
    global current_temperature

    if response_msg.startswith("GEN_TRIGGER:"):
        _, params = response_msg.split(":", 1)
        gen_temp_str, gen_nick, gen_start = params.split("|", 2)
        gen_temp = float(gen_temp_str)

        log(f"[{loc}] Generating...")
        prompt = "\n".join(histories[loc]) + f"\n[*{my_id}] {gen_nick}: {gen_start}"

        loop = asyncio.get_running_loop()
        output = await loop.run_in_executor(executor, do_generate, prompt, gen_temp)
        gen_response = (gen_start + " " + output.strip()).strip()

        if gen_response:
            await send_response(ws, gen_response, loc, gen_nick)
            histories[loc].append(f"[*{my_id}] {gen_nick}: {gen_response}")
        return

    if response_msg.startswith("SET_TEMP:"):
        _, temp_str = response_msg.split(":", 1)
        current_temperature = float(temp_str)
        await send_response(ws, f"🌡️ Global temperature set to {current_temperature}", loc)
        return

    if response_msg.startswith("SYNC_TIERS:"):
        _, user_message = response_msg.split(":", 1)
        synced = save_tiers_to_github()
        suffix = " (synced to GitHub)" if synced else ""
        await send_response(ws, f"{user_message}{suffix}", loc)
        return

    if response_msg.startswith("KILL_BOT:"):
        _, user_message = response_msg.split(":", 1)
        with suppress(Exception):
            await send_response(ws, user_message, loc)
        await shutdown_bot("Kill command triggered. Exiting bot.")
        return

    await send_response(ws, response_msg, loc)


async def handle_websocket(url, is_network=False):
    global histories

    while not shutdown_event.is_set():
        ws = None
        headers = None
        try:
            headers = build_headers()
            log(f"Connecting to {url}...")
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

                if is_network and data.get("location") == "global":
                    continue

                loc = "network" if is_network else data.get("location", "page")
                msg_text = data.get("message", "")
                real_user = data.get("realUsername", "")

                ctx = create_command_context(ws, loc, real_user, my_id, histories, current_temperature, permission_manager)
                response_msg = command_dispatcher.dispatch(msg_text, ctx)

                if response_msg:
                    await handle_command_response(ws, response_msg, loc, my_id)
                    continue

                formatted = format_message(data)
                histories[loc].append(formatted)
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

    tasks = [
        asyncio.create_task(handle_websocket(world_url, is_network=False)),
        asyncio.create_task(handle_websocket(network_url, is_network=True))
    ]

    try:
        await shutdown_event.wait()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        executor.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("Keyboard interrupt received. Exiting bot.")
        sys.exit(0)

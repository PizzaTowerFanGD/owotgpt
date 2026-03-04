import asyncio
import json
import websockets
import gpt_2_simple as gpt2
import tensorflow as tf # Required to fix the Graph error
import os
import sys
from concurrent.futures import ThreadPoolExecutor

from commands import CommandDispatcher, CommandContext, create_dispatcher, parse_flags

# --- CONFIGURATION ---
WORLD_URL = "wss://ourworldoftext.com/ws/"
NETWORK_URL = "wss://ourworldoftext.com/...network/ws/"
RUN_NAME = 'owotgpt'
BOT_NICK_DEFAULT = "OWoTGPT"
ADMIN_USER = "gimmickCellar"
CONTEXT_LIMIT = 15

# Global state
current_temperature = 1.3

# Legacy triggers (kept for reference, now handled by command dispatcher)
# T_GEN = "owotgpt gen", T_SON = "my son", T_CLEAR = "owotgpt clear"
# T_IMITATE = "owotgpt imitate", T_HELP = "owotgpt help"
# T_TEMP = "owotgpt temp", T_INFO = "owotgpt info"

def log(msg):
    print(msg, flush=True)

# Create global command dispatcher
command_dispatcher = create_dispatcher()


def create_command_context(ws, loc, real_user, my_id, histories, current_temp):
    """Create a CommandContext with current state."""
    return CommandContext(
        websocket=ws,
        location=loc,
        real_user=real_user,
        my_id=my_id,
        admin_user=ADMIN_USER,
        bot_nick_default=BOT_NICK_DEFAULT,
        histories=histories,
        current_temperature=current_temp,
        context_limit=CONTEXT_LIMIT
    )


async def send_response(ws, message, loc, nickname=BOT_NICK_DEFAULT):
    """Send a chat response via websocket."""
    await ws.send(json.dumps({
        "kind": "chat",
        "nickname": nickname,
        "message": message,
        "location": loc,
        "color": 0
    }))

log("--- Starting Bot Initialization ---")
if not os.path.exists(os.path.join('checkpoint', RUN_NAME)):
    log(f"ERROR: checkpoint/{RUN_NAME} not found.")
    sys.exit(1)

log("Loading GPT-2 model...")
# Fix: Keep reference to the session and the computation graph
sess = gpt2.start_tf_sess()
graph = tf.compat.v1.get_default_graph() 
gpt2.load_gpt2(sess, run_name=RUN_NAME)
log("Model loaded successfully!")

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
    else:
        return f"[*{mid}] {nick}: {text}" if nick else f"[{mid}]: {text}"

# Generation wrapper with Graph safety
def do_generate(prompt_str, temp):
    with graph.as_default():
        with sess.as_default():
            return gpt2.generate(
                sess, run_name=RUN_NAME, length=100, temperature=temp,
                prefix=prompt_str, return_as_list=True, include_prefix=False, truncate='\n'
            )[0]

async def handle_websocket(url, is_network=False):
    global histories, current_temperature
    log(f"Connecting to {url}...")
    
    async with websockets.connect(url) as ws:
        my_id = "0"
        if is_network:
            await ws.send(json.dumps({"kind": "chathistory"}))
        
        while True:
            try:
                raw_data = await ws.recv()
                data = json.loads(raw_data)
                
                if data.get("kind") == "channel":
                    my_id = data.get("id")
                    log(f"Connected to {url} - ID: {my_id}")

                if data.get("kind") == "chat":
                    # Self-trigger prevention
                    sender_id = data.get("id")
                    if str(sender_id) == str(my_id):
                        continue
                    
                    # IGNORE GLOBAL CHAT ON NETWORK SOCKET
                    # (Prevent bot from seeing the same global message twice)
                    if is_network and data.get("location") == "global":
                        continue

                    if is_network:
                        loc = "network"
                    else:
                        loc = data.get("location", "page")
                    
                    msg_text = data.get("message", "")
                    real_user = data.get("realUsername", "")

                    # Create command context
                    ctx = create_command_context(ws, loc, real_user, my_id, histories, current_temperature)

                    # Try to dispatch command
                    response_msg = command_dispatcher.dispatch(msg_text, ctx)

                    if response_msg:
                        # Handle special command responses
                        if response_msg.startswith("GEN_TRIGGER:"):
                            # Parse generation parameters from response marker
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

                        elif response_msg.startswith("SET_TEMP:"):
                            # Update global temperature
                            _, temp_str = response_msg.split(":", 1)
                            current_temperature = float(temp_str)
                            await send_response(ws, f"🌡️ Global temperature set to {current_temperature}", loc)

                        else:
                            # Regular response
                            await send_response(ws, response_msg, loc)
                        continue

                    # Add non-command messages to history
                    formatted = format_message(data)
                    histories[loc].append(formatted)
                    if len(histories[loc]) > CONTEXT_LIMIT:
                        histories[loc].pop(0)

                    # Note: Legacy triggers like "my son" are now handled through the
                    # command dispatcher. The old inline generation logic has been
                    # replaced by the standardized command system in commands.py

            except websockets.ConnectionClosed:
                log(f"Lost connection to {url}. Reconnecting...")
                break
            except Exception as e:
                log(f"Error on {url}: {e}")

async def main():
    while True:
        # Run both tasks. Gather will restart if one closes.
        await asyncio.gather(
            handle_websocket(WORLD_URL, is_network=False),
            handle_websocket(NETWORK_URL, is_network=True)
        )
        await asyncio.sleep(5)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)

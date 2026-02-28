import asyncio
import json
import websockets
import gpt_2_simple as gpt2
import os
import sys
import time
import re
from concurrent.futures import ThreadPoolExecutor

# --- CONFIGURATION ---
WORLD_URL = "wss://ourworldoftext.com/ws/"
NETWORK_URL = "wss://ourworldoftext.com/...network/ws/" # Custom network WS
RUN_NAME = 'owotgpt'
BOT_NICK_DEFAULT = "OWoTGPT"
ADMIN_USER = "gimmickCellar"
CONTEXT_LIMIT = 15

# Global state
current_temperature = 1.3

# Triggers
T_GEN = "owotgpt gen"
T_SON = "my son"
T_CLEAR = "owotgpt clear"
T_IMITATE = "owotgpt imitate"
T_HELP = "owotgpt help"
T_TEMP = "owotgpt temp"
T_INFO = "owotgpt info"

def log(msg):
    print(msg, flush=True)

def parse_flags(text):
    flags = {"temp": None, "start": "", "imitate": None}
    matches = re.findall(r'--(temp|start|imitate)\s+((?:(?!--).)+)', text, re.IGNORECASE)
    cleaned_text = text
    for flag_name, flag_value in matches:
        flag_name = flag_name.lower()
        val = flag_value.strip()
        if flag_name == "temp":
            try: flags["temp"] = float(val)
            except: pass
        elif flag_name == "start":
            flags["start"] = val
        elif flag_name == "imitate":
            flags["imitate"] = val
        cleaned_text = re.sub(rf'--{flag_name}\s+{re.escape(flag_value)}', '', cleaned_text, flags=re.IGNORECASE).strip()
    return cleaned_text, flags

log("--- Starting Bot Initialization ---")
if not os.path.exists(os.path.join('checkpoint', RUN_NAME)):
    log(f"ERROR: checkpoint/{RUN_NAME} not found.")
    sys.exit(1)

log("Loading GPT-2 model...")
sess = gpt2.start_tf_sess()
gpt2.load_gpt2(sess, run_name=RUN_NAME)
log("Model loaded successfully!")

executor = ThreadPoolExecutor(max_workers=1)

histories = {
    "page": [],
    "global": [],
    "network": [] # Buffer for the custom network
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

def do_generate(prompt_str, temp):
    return gpt2.generate(
        sess, run_name=RUN_NAME, length=100, temperature=temp,
        prefix=prompt_str, return_as_list=True, include_prefix=False, truncate='\n'
    )[0]

async def handle_websocket(url, is_network=False):
    global histories, current_temperature
    log(f"Connecting to {url}...")
    
    async with websockets.connect(url) as ws:
        my_id = "0"
        # Shared chat protocol: get history on join
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
                    sender_id = data.get("id")
                    if str(sender_id) == str(my_id):
                        continue

                    # Determine history category
                    if is_network:
                        loc = "network"
                    else:
                        loc = data.get("location", "page")
                    
                    msg_text = data.get("message", "")
                    msg_text_l = msg_text.lower()
                    real_user = data.get("realUsername", "")

                    # 1. Commands
                    if msg_text_l == T_HELP:
                        help_msg = ("Commands: owotgpt gen, owotgpt imitate [nick], owotgpt info, owotgpt help.\n"
                                   "Flags: --temp [0.1-1.5], --start [text], --imitate [nick].")
                        await ws.send(json.dumps({"kind": "chat", "nickname": BOT_NICK_DEFAULT, "message": help_msg, "location": loc, "color": 0}))
                        continue

                    if msg_text_l == T_CLEAR and real_user == ADMIN_USER:
                        histories[loc] = []
                        await ws.send(json.dumps({"kind": "chat", "nickname": BOT_NICK_DEFAULT, "message": f"Context for {loc} cleared.", "location": loc, "color": 0}))
                        continue

                    if msg_text_l.startswith(T_TEMP) and real_user == ADMIN_USER:
                        try:
                            new_temp_str = msg_text_l.replace(T_TEMP, "").strip()
                            current_temperature = max(0.1, min(2.0, float(new_temp_str)))
                            await ws.send(json.dumps({"kind": "chat", "nickname": BOT_NICK_DEFAULT, "message": f"Global temperature set to {current_temperature}", "location": loc, "color": 0}))
                        except: pass
                        continue

                    if msg_text_l == T_INFO:
                        info_msg = (f"🤖 OWoTGPT Info\nTemp: {current_temperature} | Context: {len(histories[loc])}/{CONTEXT_LIMIT}\nNetwork support: Active")
                        await ws.send(json.dumps({"kind": "chat", "nickname": BOT_NICK_DEFAULT, "message": info_msg, "location": loc, "color": 0}))
                        continue

                    # Add to history
                    formatted = format_message(data)
                    histories[loc].append(formatted)
                    if len(histories[loc]) > CONTEXT_LIMIT: histories[loc].pop(0)

                    # 2. Generation Logic
                    cleaned_msg, flags = parse_flags(msg_text)
                    cleaned_msg_l = cleaned_msg.lower()
                    should_gen = False

                    gen_temp = flags["temp"] if flags["temp"] is not None else current_temperature
                    gen_nick = flags["imitate"] if flags["imitate"] else BOT_NICK_DEFAULT
                    gen_start = flags["start"]

                    if cleaned_msg_l.startswith(T_IMITATE):
                        legacy_name = cleaned_msg[len(T_IMITATE):].strip()
                        if not flags["imitate"] and legacy_name: gen_nick = legacy_name
                        should_gen = True
                    elif T_GEN in cleaned_msg_l or (T_SON in cleaned_msg_l and real_user == ADMIN_USER):
                        should_gen = True

                    if should_gen:
                        log(f"[{loc}] Generating (Temp: {gen_temp}, Nick: {gen_nick})")
                        prompt = "\n".join(histories[loc]) + f"\n[*{my_id}] {gen_nick}: {gen_start}"
                        
                        loop = asyncio.get_running_loop()
                        output = await loop.run_in_executor(executor, do_generate, prompt, gen_temp)
                        response = (gen_start + " " + output.strip()).strip()
                        
                        if response:
                            await ws.send(json.dumps({
                                "kind": "chat",
                                "nickname": gen_nick,
                                "message": response,
                                "location": loc,
                                "color": 0
                            }))
                            histories[loc].append(f"[*{my_id}] {gen_nick}: {response}")

            except websockets.ConnectionClosed:
                log(f"Connection to {url} lost. Retrying...")
                break
            except Exception as e:
                log(f"Websocket Error ({url}): {e}")

async def main():
    while True:
        # Run both the standard world and the custom network concurrently
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

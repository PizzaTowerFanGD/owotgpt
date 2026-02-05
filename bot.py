import asyncio
import json
import websockets
import gpt_2_simple as gpt2
import os
import sys
import time
import re

# --- CONFIGURATION ---
WORLD_URL = "wss://ourworldoftext.com/ws/"
RUN_NAME = 'owotgpt'
BOT_NICK_DEFAULT = "OWoTGPT"
ADMIN_USER = "gimmickCellar"
CONTEXT_LIMIT = 15

# New global state
current_temperature = 0.8

# Triggers
T_GEN = "owotgpt gen"
T_SON = "my son"
T_CLEAR = "owotgpt clear"
T_IMITATE = "owotgpt imitate"
T_HELP = "owotgpt help"
T_TEMP = "owotgpt temp"

def log(msg):
    print(msg, flush=True)

def parse_flags(text):
    """
    Parses --temp [num], --start [str], --imitate [str] from a string.
    Returns (cleaned_text, flags_dict)
    """
    flags = {
        "temp": None,
        "start": "",
        "imitate": None
    }
    
    # Regex to find --flag value (stops at next -- or end of string)
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
            
        # Remove the flag from the text so it doesn't interfere with legacy name detection
        cleaned_text = re.sub(rf'--{flag_name}\s+{re.escape(flag_value)}', '', cleaned_text, flags=re.IGNORECASE).strip()

    return cleaned_text, flags

log("--- Starting Bot Initialization ---")

if not os.path.exists(os.path.join('checkpoint', RUN_NAME)):
    log(f"ERROR: checkpoint/{RUN_NAME} not found.")
    sys.exit(1)

log("Loading GPT-2 model (CPU mode)...")
sess = gpt2.start_tf_sess()
gpt2.load_gpt2(sess, run_name=RUN_NAME)
log("Model loaded successfully!")

histories = {
    "page": [],
    "global": []
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

async def run_owot_bot():
    global histories, current_temperature
    log(f"Connecting to {WORLD_URL}...")
    
    async with websockets.connect(WORLD_URL) as ws:
        log("Connected! Listening for messages...")
        my_id = "0"
        
        while True:
            try:
                raw_data = await ws.recv()
                data = json.loads(raw_data)
                
                if data.get("kind") == "channel":
                    my_id = data.get("id")
                    log(f"Bot Session ID: {my_id}")

                if data.get("kind") == "chat":
                    loc = data.get("location", "page")
                    msg_text = data.get("message", "")
                    msg_text_l = msg_text.lower()
                    real_user = data.get("realUsername", "")
                    
                    # 1. Command: Help
                    if msg_text_l == T_HELP:
                        help_msg = ("Commands: owotgpt gen, owotgpt imitate [nick], owotgpt help.\n"
                                   "Flags: --temp [0.1-1.5], --start [text], --imitate [nick].")
                        await ws.send(json.dumps({"kind": "chat", "nickname": BOT_NICK_DEFAULT, "message": help_msg, "location": loc, "color": 0}))
                        continue

                    # 2. Command: Clear (Admin only)
                    if msg_text_l == T_CLEAR and real_user == ADMIN_USER:
                        histories[loc] = []
                        await ws.send(json.dumps({"kind": "chat", "nickname": BOT_NICK_DEFAULT, "message": f"Context for {loc} cleared.", "location": loc, "color": 0}))
                        continue

                    # 3. Command: Global Temperature (Admin only)
                    if msg_text_l.startswith(T_TEMP) and real_user == ADMIN_USER:
                        try:
                            new_temp_str = msg_text_l.replace(T_TEMP, "").strip()
                            current_temperature = max(0.1, min(2.0, float(new_temp_str)))
                            await ws.send(json.dumps({"kind": "chat", "nickname": BOT_NICK_DEFAULT, "message": f"Global temperature set to {current_temperature}", "location": loc, "color": 0}))
                        except:
                            pass
                        continue

                    # Add incoming message to history
                    formatted = format_message(data)
                    log(f"[{loc}] {formatted}")
                    histories[loc].append(formatted)
                    if len(histories[loc]) > CONTEXT_LIMIT:
                        histories[loc].pop(0)

                    # --- TRIGGER & FLAG LOGIC ---
                    should_gen = False
                    
                    # Parse flags from the original message
                    cleaned_msg, flags = parse_flags(msg_text)
                    cleaned_msg_l = cleaned_msg.lower()

                    # Determine specific generation settings
                    gen_temp = flags["temp"] if flags["temp"] is not None else current_temperature
                    gen_nick = BOT_NICK_DEFAULT
                    gen_start = flags["start"]

                    if cleaned_msg_l.startswith(T_IMITATE):
                        # Legacy support: extract name from "owotgpt imitate [Name]"
                        legacy_name = cleaned_msg[len(T_IMITATE):].strip()
                        gen_nick = flags["imitate"] if flags["imitate"] else (legacy_name if legacy_name else BOT_NICK_DEFAULT)
                        should_gen = True
                    elif T_GEN in cleaned_msg_l:
                        gen_nick = flags["imitate"] if flags["imitate"] else BOT_NICK_DEFAULT
                        should_gen = True
                    elif T_SON in cleaned_msg_l and real_user == ADMIN_USER:
                        gen_nick = flags["imitate"] if flags["imitate"] else BOT_NICK_DEFAULT
                        should_gen = True

                    if should_gen:
                        log(f"Generating (Temp: {gen_temp}, Nick: {gen_nick}, Start: {gen_start})")
                        
                        # Build prompt: Context + Bot Entry + Optional start text
                        prompt = "\n".join(histories[loc]) + f"\n[*{my_id}] {gen_nick}: {gen_start}"
                        
                        output = gpt2.generate(
                            sess,
                            run_name=RUN_NAME,
                            length=100,
                            temperature=gen_temp,
                            prefix=prompt,
                            return_as_list=True,
                            include_prefix=False,
                            truncate='\n'
                        )[0]

                        # Prepend the --start text to the AI response
                        response = (gen_start + " " + output.strip()).strip()
                        
                        if response:
                            log(f"AI Output: {response}")
                            await ws.send(json.dumps({
                                "kind": "chat",
                                "nickname": gen_nick,
                                "message": response,
                                "location": loc,
                                "color": 0
                            }))
                            histories[loc].append(f"[*{my_id}] {gen_nick}: {response}")

            except websockets.ConnectionClosed:
                log("Connection closed. Retrying...")
                break
            except Exception as e:
                log(f"Runtime Error: {e}")

if __name__ == "__main__":
    while True:
        try:
            asyncio.run(run_owot_bot())
        except KeyboardInterrupt:
            sys.exit(0)
        except Exception as e:
            log(f"Restarting in 10s... Error: {e}")
            time.sleep(10)

import asyncio
import json
import websockets
import gpt_2_simple as gpt2
import os
import sys
import time

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
T_TEMP = "owotgpt temp" # New trigger

def log(msg):
    print(msg, flush=True)

log("--- Starting Bot Initialization ---")

if not os.path.exists(os.path.join('checkpoint', RUN_NAME)):
    log(f"ERROR: checkpoint/{RUN_NAME} not found.")
    sys.exit(1)

log("Loading GPT-2 model (CPU mode)...")
sess = gpt2.start_tf_sess()
gpt2.load_gpt2(sess, run_name=RUN_NAME)
log("Model loaded successfully!")

# Separate contexts for Global and Page
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
                        help_msg = "Commands: owotgpt gen, owotgpt imitate [text], owotgpt help. Admin: my son, owotgpt clear, owotgpt temp [0.1-1.5]."
                        await ws.send(json.dumps({"kind": "chat", "nickname": BOT_NICK_DEFAULT, "message": help_msg, "location": loc, "color": 0}))
                        continue

                    # 2. Command: Clear (Admin only)
                    if msg_text_l == T_CLEAR and real_user == ADMIN_USER:
                        histories[loc] = []
                        log(f"Context for {loc} cleared by {ADMIN_USER}")
                        await ws.send(json.dumps({"kind": "chat", "nickname": BOT_NICK_DEFAULT, "message": f"Context for {loc} cleared.", "location": loc, "color": 0}))
                        continue

                    # 3. Command: Temperature (Admin only)
                    if msg_text_l.startswith(T_TEMP) and real_user == ADMIN_USER:
                        try:
                            # Usage: "owotgpt temp 0.7"
                            new_temp_str = msg_text_l.replace(T_TEMP, "").strip()
                            new_temp = float(new_temp_str)
                            # Clamping for safety
                            current_temperature = max(0.1, min(2.0, new_temp))
                            msg = f"Temperature set to {current_temperature}"
                            log(msg)
                            await ws.send(json.dumps({"kind": "chat", "nickname": BOT_NICK_DEFAULT, "message": msg, "location": loc, "color": 0}))
                        except ValueError:
                            await ws.send(json.dumps({"kind": "chat", "nickname": BOT_NICK_DEFAULT, "message": "Invalid number. Usage: owotgpt temp 0.8", "location": loc, "color": 0}))
                        continue

                    # Add incoming message to history
                    formatted = format_message(data)
                    log(f"[{loc}] {formatted}")
                    histories[loc].append(formatted)
                    if len(histories[loc]) > CONTEXT_LIMIT:
                        histories[loc].pop(0)

                    # --- TRIGGER LOGIC ---
                    target_prefix = BOT_NICK_DEFAULT
                    should_gen = False

                    if msg_text_l.startswith(T_IMITATE):
                        imitate_name = msg_text[len(T_IMITATE):].strip()
                        if imitate_name:
                            target_prefix = imitate_name
                            should_gen = True
                    elif T_GEN in msg_text_l:
                        should_gen = True
                    elif T_SON in msg_text_l and real_user == ADMIN_USER:
                        should_gen = True

                    if should_gen:
                        log(f"Generating (Temp: {current_temperature}) for {loc} as '{target_prefix}'...")
                        
                        prompt = "\n".join(histories[loc]) + f"\n[*{my_id}] {target_prefix}: "
                        
                        output = gpt2.generate(
                            sess,
                            run_name=RUN_NAME,
                            length=100,
                            temperature=current_temperature, # Using global variable
                            prefix=prompt,
                            return_as_list=True,
                            include_prefix=False,
                            truncate='\n'
                        )[0]

                        response = output.strip()
                        if response:
                            log(f"AI Output: {response}")
                            await ws.send(json.dumps({
                                "kind": "chat",
                                "nickname": target_prefix,
                                "message": response,
                                "location": loc,
                                "color": 0
                            }))
                            histories[loc].append(f"[*{my_id}] {target_prefix}: {response}")

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

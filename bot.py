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
TRIGGER_GEN = "owotgpt gen"
TRIGGER_SON = "my son"
TRIGGER_CLEAR = "owotgpt clear"
BOT_NICK = "OWoTGPT"
ADMIN_USER = "gimmickCellar"
CONTEXT_LENGTH = 15

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

chat_history = []

def format_message(msg_data):
    mid = msg_data.get("id", "0")
    nick = msg_data.get("nickname", "")
    real_user = msg_data.get("realUsername", "")
    text = msg_data.get("message", "")
    is_registered = msg_data.get("registered", False)

    if is_registered:
        # If the user hasn't changed their nickname, nick will be empty or same as realUsername
        if not nick or nick.lower() == real_user.lower():
            return f"[*{mid}] {real_user}: {text}"
        else:
            return f"[*{mid}] {nick}: {text}"
    else:
        if nick:
            return f"[*{mid}] {nick}: {text}"
        else:
            return f"[{mid}]: {text}"

async def run_owot_bot():
    global chat_history
    log(f"Connecting to {WORLD_URL}...")
    
    async with websockets.connect(WORLD_URL) as ws:
        log("Connected! Listening for page-only messages...")
        my_id = "0"
        
        while True:
            try:
                raw_data = await ws.recv()
                data = json.loads(raw_data)
                
                if data.get("kind") == "channel":
                    my_id = data.get("id")
                    log(f"Bot Session ID: {my_id}")

                if data.get("kind") == "chat":
                    # --- FILTER: Only get Main Page chat (non-global) ---
                    if data.get("location") != "page":
                        continue

                    msg_text = data.get("message", "")
                    real_user = data.get("realUsername", "")
                    
                    # 1. Handle "owotgpt clear" (Admin only)
                    if msg_text.lower() == TRIGGER_CLEAR and real_user == ADMIN_USER:
                        chat_history = []
                        log(f"Context cleared by {ADMIN_USER}")
                        continue

                    # Process and store the message in history
                    formatted = format_message(data)
                    log(f"Chat Log: {formatted}")
                    chat_history.append(formatted)
                    if len(chat_history) > CONTEXT_LENGTH:
                        chat_history.pop(0)

                    # --- TRIGGER LOGIC ---
                    should_gen = False
                    
                    # A. Standard trigger
                    if TRIGGER_GEN in msg_text.lower():
                        should_gen = True
                    
                    # B. "my son" trigger (Admin only)
                    if TRIGGER_SON in msg_text.lower() and real_user == ADMIN_USER:
                        should_gen = True

                    if should_gen:
                        log("Trigger detected. Generating response...")
                        
                        # Build prompt
                        prompt = "\n".join(chat_history) + f"\n[*{my_id}] {BOT_NICK}: "
                        
                        # Generate
                        output = gpt2.generate(
                            sess,
                            run_name=RUN_NAME,
                            length=100,
                            temperature=0.8,
                            prefix=prompt,
                            return_as_list=True,
                            include_prefix=False,
                            truncate='\n'
                        )[0]

                        response = output.strip()
                        if response:
                            log(f"AI Response: {response}")
                            await ws.send(json.dumps({
                                "kind": "chat",
                                "nickname": BOT_NICK,
                                "message": response,
                                "location": "page",
                                "color": 0
                            }))
                            # Add our own line to the history
                            chat_history.append(f"[*{my_id}] {BOT_NICK}: {response}")

            except websockets.ConnectionClosed:
                log("Server closed connection. Reconnecting...")
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

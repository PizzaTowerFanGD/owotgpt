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
TRIGGER = "owotgpt gen"
BOT_NICK = "OWoTGPT"
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
    """
    Replicating OWoT chatType logic:
    - Registered user (no nick change): [*id] realUsername: message
    - Registered user (nick change): [*id] nickname: message
    - Unregistered with nick: [*id] nickname: message
    - Pure anonymous: [id]: message
    """
    mid = msg_data.get("id", "0")
    nick = msg_data.get("nickname", "")
    real_user = msg_data.get("realUsername", "")
    text = msg_data.get("message", "")
    is_registered = msg_data.get("registered", False)

    # 1. Registered User Logic
    if is_registered:
        # If the user hasn't changed their nickname, nick will be empty or same as realUsername
        if not nick or nick.lower() == real_user.lower():
            return f"[*{mid}] {real_user}: {text}"
        else:
            return f"[*{mid}] {nick}: {text}"
    
    # 2. Unregistered User Logic
    else:
        if nick:
            # Unregistered but has a nickname set
            return f"[*{mid}] {nick}: {text}"
        else:
            # Pure anonymous
            return f"[{mid}]: {text}"

async def run_owot_bot():
    global chat_history
    log(f"Connecting to {WORLD_URL}...")
    
    async with websockets.connect(WORLD_URL) as ws:
        log("Connected! Listening...")
        my_id = "0"
        
        while True:
            try:
                raw_data = await ws.recv()
                data = json.loads(raw_data)
                
                if data.get("kind") == "channel":
                    my_id = data.get("id")
                    log(f"Bot Session ID: {my_id}")

                if data.get("kind") == "chat":
                    # Format message using the new logic
                    formatted = format_message(data)
                    log(f"Chat Log: {formatted}")
                    
                    chat_history.append(formatted)
                    if len(chat_history) > CONTEXT_LENGTH:
                        chat_history.pop(0)

                    msg_text = data.get("message", "")
                    if TRIGGER in msg_text.lower():
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
            log(f"Connection lost: {e}. Restarting in 10s...")
            time.sleep(10)

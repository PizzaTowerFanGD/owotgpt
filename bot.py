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

# Force print to show up immediately in GitHub Actions
def log(msg):
    print(msg, flush=True)

log("--- Starting Bot Initialization ---")

if not os.path.exists(os.path.join('checkpoint', RUN_NAME)):
    log(f"ERROR: checkpoint/{RUN_NAME} not found.")
    sys.exit(1)

log("Loading GPT-2 model into memory (this takes ~1 minute on CPU)...")
sess = gpt2.start_tf_sess()
gpt2.load_gpt2(sess, run_name=RUN_NAME)
log("Model loaded successfully!")

chat_history = []

def format_message(msg_data):
    mid = msg_data.get("id", "0")
    nick = msg_data.get("nickname", "")
    real_user = msg_data.get("realUsername", "")
    text = msg_data.get("message", "")
    mtype = msg_data.get("type", "")
    if mtype == "user":
        return f"[*{mid}] {real_user}: {text}"
    elif mtype in ["user_nick", "anon_nick"]:
        return f"[*{mid}] {nick}: {text}"
    else:
        return f"[{mid}]: {text}"

async def run_owot_bot():
    global chat_history
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
                    log(f"My Client ID: {my_id}")

                if data.get("kind") == "chat":
                    formatted = format_message(data)
                    log(f"Chat: {formatted}") # This will show all chat in logs
                    
                    chat_history.append(formatted)
                    if len(chat_history) > CONTEXT_LENGTH:
                        chat_history.pop(0)

                    if TRIGGER in data.get("message", "").lower():
                        log("Trigger detected! Generating...")
                        
                        prompt = "\n".join(chat_history) + f"\n[*{my_id}] {BOT_NICK}: "
                        
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
                            log(f"Generated response: {response}")
                            await ws.send(json.dumps({
                                "kind": "chat",
                                "nickname": BOT_NICK,
                                "message": response,
                                "location": "page",
                                "color": 0
                            }))
                            chat_history.append(f"[*{my_id}] {BOT_NICK}: {response}")

            except websockets.ConnectionClosed:
                log("Lost connection to server.")
                break
            except Exception as e:
                log(f"Error: {e}")

if __name__ == "__main__":
    while True:
        try:
            asyncio.run(run_owot_bot())
        except KeyboardInterrupt:
            sys.exit(0)
        except Exception as e:
            log(f"Restarting due to error: {e}")
            time.sleep(10)

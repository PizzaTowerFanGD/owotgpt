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

# --- INITIALIZE GPT-2 ---
if not os.path.exists(os.path.join('checkpoint', RUN_NAME)):
    print(f"ERROR: Checkpoint folder 'checkpoint/{RUN_NAME}' not found.")
    sys.exit(1)

sess = gpt2.start_tf_sess()
gpt2.load_gpt2(sess, run_name=RUN_NAME)

chat_history = []

def format_message(msg_data):
    """
    Formatting Logic:
    1. type == 'user': [*id] realUsername: message
    2. type == 'user_nick' or 'anon_nick': [*id] nickname: message
    3. No nickname/anon: [id]: message
    """
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
    
    async with websockets.connect(WORLD_URL) as ws:
        print("Connected to Our World of Text.")
        my_id = "0"
        
        while True:
            try:
                raw_data = await ws.recv()
                data = json.loads(raw_data)
                
                # kind: channel tells us our connection ID
                if data.get("kind") == "channel":
                    my_id = data.get("id")
                    print(f"Bot Session ID: {my_id}")

                if data.get("kind") == "chat":
                    formatted = format_message(data)
                    chat_history.append(formatted)
                    
                    if len(chat_history) > CONTEXT_LENGTH:
                        chat_history.pop(0)

                    msg_text = data.get("message", "").lower()
                    if TRIGGER in msg_text:
                        # Construct prompt ending with our bot line
                        prompt = "\n".join(chat_history)
                        prompt += f"\n[*{my_id}] {BOT_NICK}: "
                        
                        print("Generating...")
                        output = gpt2.generate(
                            sess,
                            run_name=RUN_NAME,
                            length=100,
                            temperature=0.8,
                            prefix=prompt,
                            return_as_list=True,
                            include_prefix=False,
                            truncate='\n' # Stop generating at newline
                        )[0]

                        response = output.strip()
                        
                        if response:
                            print(f"Sending: {response}")
                            await ws.send(json.dumps({
                                "kind": "chat",
                                "nickname": BOT_NICK,
                                "message": response,
                                "location": "page",
                                "color": 0
                            }))
                            chat_history.append(f"[*{my_id}] {BOT_NICK}: {response}")

            except websockets.ConnectionClosed:
                print("Socket closed. Retrying...")
                break
            except Exception as e:
                print(f"Error: {e}")

if __name__ == "__main__":
    while True:
        try:
            asyncio.run(run_owot_bot())
        except KeyboardInterrupt:
            sys.exit(0)
        except Exception:
            time.sleep(10)

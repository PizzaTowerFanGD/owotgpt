import asyncio
import json
import websockets
import gpt_2_simple as gpt2
import os
import sys

# --- CONFIGURATION ---
WORLD_URL = "wss://ourworldoftext.com/ws/"
RUN_NAME = 'owotgpt'
TRIGGER = "owotgpt gen"
BOT_NICK = "OWoTGPT"
CONTEXT_LENGTH = 15

# --- INITIALIZE GPT-2 ---
# Ensure directory exists before loading
if not os.path.exists(os.path.join('checkpoint', RUN_NAME)):
    print(f"ERROR: Checkpoint folder 'checkpoint/{RUN_NAME}' not found.")
    sys.exit(1)

sess = gpt2.start_tf_sess()
gpt2.load_gpt2(sess, run_name=RUN_NAME)

chat_history = []

def format_message(msg_data):
    """
    Requested Format Logic:
    - type == 'user': [*id] realUsername: message
    - type == 'user_nick' or 'anon_nick': [*id] nickname: message
    - type == 'anon': [id]: message
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
        # For non-nick users
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
                        # Format prompt: history + our bot's prefix
                        prompt = "\n".join(chat_history)
                        prompt += f"\n[*{my_id}] {BOT_NICK}: "
                        
                        print(f"Generating for prompt:\n{prompt}")
                        
                        # Generate response
                        output = gpt2.generate(
                            sess,
                            run_name=RUN_NAME,
                            length=100,
                            temperature=0.8,
                            prefix=prompt,
                            return_as_list=True,
                            include_prefix=False,
                            truncate='\n' # Stop at first newline
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
                            # Feed our own output back into history
                            chat_history.append(f"[*{my_id}] {BOT_NICK}: {response}")

            except websockets.ConnectionClosed:
                print("Connection closed. Reconnecting...")
                break
            except Exception as e:
                print(f"Error: {e}")

if __name__ == "__main__":
    # Loop for reconnection
    while True:
        try:
            asyncio.run(run_owot_bot())
        except KeyboardInterrupt:
            sys.exit(0)
        except Exception:
            time.sleep(10)

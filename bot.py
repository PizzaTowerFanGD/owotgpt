import asyncio
import json
import websockets
import gpt_2_simple as gpt2
import os
import time

# --- CONFIGURATION ---
WORLD_URL = "wss://ourworldoftext.com/ws/"
RUN_NAME = 'owotgpt'
TRIGGER = "owotgpt gen"
BOT_NICK = "OWoTGPT"
CONTEXT_LENGTH = 15

# --- INITIALIZE GPT-2 ---
checkpoint_dir = 'checkpoint'
if not os.path.exists(os.path.join(checkpoint_dir, RUN_NAME)):
    print(f"Error: checkpoint/{RUN_NAME} not found!")
    # Create dummy files for Action testing if needed, but normally user provides these
    exit(1)

sess = gpt2.start_tf_sess()
gpt2.load_gpt2(sess, run_name=RUN_NAME)

chat_history = []

def format_message(msg_data):
    """
    Requested Logic:
    1. type == "user": Use realUsername
    2. type == "user_nick": Use nickname
    3. No nick/anon: [id]: message
    """
    mid = msg_data.get("id", "0")
    nick = msg_data.get("nickname", "")
    real_user = msg_data.get("realUsername", "")
    text = msg_data.get("message", "")
    mtype = msg_data.get("type", "")

    if mtype == "user":
        return f"[*{mid}] {real_user}: {text}"
    elif mtype == "user_nick":
        return f"[*{mid}] {nick}: {text}"
    else:
        # Default / Anonymous
        return f"[{mid}]: {text}"

async def run_owot_bot():
    global chat_history
    
    async with websockets.connect(WORLD_URL) as ws:
        print("Connected to OWoT.")
        my_id = "0"
        
        while True:
            try:
                raw_data = await ws.recv()
                data = json.loads(raw_data)
                
                if data.get("kind") == "channel":
                    my_id = data.get("id")
                    print(f"Assigned ID: {my_id}")

                if data.get("kind") == "chat":
                    formatted = format_message(data)
                    chat_history.append(formatted)
                    if len(chat_history) > CONTEXT_LENGTH:
                        chat_history.pop(0)

                    msg_text = data.get("message", "").lower()
                    if TRIGGER in msg_text:
                        # Construct prompt: history + Current Bot Line
                        prompt = "\n".join(chat_history)
                        prompt += f"\n[*{my_id}] {BOT_NICK}: "
                        
                        print("Generating response...")
                        output = gpt2.generate(
                            sess,
                            run_name=RUN_NAME,
                            length=60,
                            temperature=0.8,
                            prefix=prompt,
                            return_as_list=True,
                            include_prefix=False,
                            truncate='\n' # Cut off at newline
                        )[0]

                        response = output.strip()
                        if response:
                            await ws.send(json.dumps({
                                "kind": "chat",
                                "nickname": BOT_NICK,
                                "message": response,
                                "location": "page",
                                "color": 0
                            }))
                            # Add our own message to history to keep context flow
                            chat_history.append(f"[*{my_id}] {BOT_NICK}: {response}")

            except websockets.ConnectionClosed:
                break
            except Exception as e:
                print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(run_owot_bot())

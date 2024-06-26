#!/usr/bin/env python

import requests
import telethon
import json
import asyncio
import aioconsole
from telethon import TelegramClient, events, sync

backend = 'vllm'

# Launch vllm with python -m vllm.entrypoints.openai.api_server --model microsoft/Phi-3-mini-128k-instruct --dtype auto --trust-remote-code --gpu-memory-utilization 0.85 --max-model-len 25000
def vllm_complete(txt, max_tokens):
    data = {
        'model': 'microsoft/Phi-3-mini-128k-instruct',
        'prompt': txt,
        'max_tokens': max_tokens,
        'temperature': 0.20,
        'stop': ['\n', '</s>'],
    }

    headers = {'Content-Type': 'application/json'}
    response = requests.post('http://localhost:8000/v1/completions', data=json.dumps(data), headers=headers)
    print("vllm response", response.text)
    return json.loads(response.text)['choices'][0]['text']


# You may need to create an account on https://api.together.ai/, and copy your API key in tokens/together
def togetherxyz_complete(txt, max_tokens, api_key):
    data = {
        'model': 'meta-llama/Llama-3-8b-chat-hf',
        'max_tokens': max_tokens,
        'stream_tokens': False,
        "stop": ["</s>", "[/INST]"],
        'temperature': 0.20,
    }

    headers = {'Content-Type': 'application/json', "Authorization": f'Bearer {api_key}'}
    data['prompt'] = txt
    response = requests.post('https://api.together.xyz/inference', data=json.dumps(data), headers=headers)
    if response.status_code != 200:
        print("Failed infering", response.text)
        return "Error"
    return json.loads(response.text)['output']['choices'][0]['text']

# Create a function that continues the request and make "prompt" bigger to retain context
def continue_prompt(discussion, max_tokens=512):
    if backend == 'togetherXYZ':
        # would need to have a lambda that would be cleaner
        with open('tokens/together', 'r') as f:
            api_key = f.readline().strip()
        content = togetherxyz_complete(discussion, max_tokens, api_key)
    elif backend == 'vllm':
        content = vllm_complete(discussion, max_tokens)
    else:
        raise ValueError(f'{backend} is not in the list of supported backends')

    print("Returning ", content)
    return content

api_id = SET ME
api_hash = SET ME TOO
devices = {}

peer_cache = {}
async def get_peer(client, peer_id):
    if not peer_id.user_id in peer_cache:
        peer = await client.get_entity(peer_id)
        peer_cache[peer_id.user_id] = peer
    else:
        peer = peer_cache[peer_id.user_id]
    return peer

async def handle_new_msg(client, ctxt):
    prompt = """
You are a useful Assistant with function calling. You do not respond. You only provide a JSON output. Nothing else.
You'll get an extract of a discussion between Android custom ROM users.
Your goal is to write down which user owns which smartphone model.
You exclusively output a JSON object with the extracted infos, with the prefix `Assistant:`.
You always output one-liner JSONL and nothing else.
The JSON you output must be on exactly one line.
The format of the JSON is that it is an object, with the key is the userid as a string.
And the value at userid is the model name of the smartphone.
Do note that the user you're helping will have "me" as userid

Examples of answers:
{"439014904":"champion"}
{"4932488":"Galaxy S9", "9394852":"Nothing Phone 2"}
DO NOT add new lines.

Example:

<s>432847: My smartphone is the best
392484: Can I use AOSP ROM to improve performance?
94881: bess rom for gaming on a51?
1002345: My Mi14 is overheating, what can I do?
me: Can you take logs?
4958672: Lol look at that cute video!<|end|>
Assistant: {"94881":"a51","1002345":"Mi14"}

Example:

<s>99123: Hi everyone
4919438: Hi @phh!
me: Hello, how are you?
19484978515: This is the best ROM, thank you!
34848751: Great great, thanks for the love<|end|>
<|assistant|>{}<|end|>

Example:

<s>94032991: I love my Fxtec Pro1
329049144: Yeah but it sucks
234904981: Not saying no
12321: Agreed
3294581: Okay, we all agree<|end|>
<|assistant|>{"94032991":"Fxtec Pro1"}<|end|>

Here comes the discussion:

"""
    ret = continue_prompt(prompt + "<s>" + ctxt + "<|end|>\n<|assistant|>")
    #ret = "{" + ret
    print("pre-json", ret)
    try:
        ret = json.loads(ret)
        for k in ret:
            v = ret[k]
            if isinstance(v, list):
                v = v[0]
            if not k in users:
                users[k] = {}
            if not 'devices' in users[k]:
                users[k]['devices'] = set()
            print(f"Adding {ret[k]} for {k}") 
            users[k]['devices'].add(ret[k])
    except:
        print("Failed parsing json")

last_conversation = ""
async def repl(client):
    global last_conversation
    prompt = """
You are a helpful Assistant to User with function calling. You never directly respond. You only provide a JSON as output. Nothing else.
User is an Android custom ROM developer.
You'll get an extract of the current discussion the user is engaging in.
You'll also receive a request from the User. Do what the user is requesting you.

Here are the functions you can call:
- `say`. You can ask/say something to the user. Example: {"function":"say","message":"What a nice day"}
- `attach_note_to_user`. You can attach a note to a user id. Example: {"function":"attach_note_to_user","note":"This user received a test image to work-around BPF on Linux 4.14","user_id":"439014904"}
- `infos_from_user`. Retrieve all infos from a user id. Example: {"function":"infos_from_user","user_id":"4932488"}

Example:
432847: My smartphone is the best
392484: Can I use AOSP ROM to improve performance?
1002345: My Mi14 is overheating, what can I do?
94881: bess rom for gaming on a51?
<s><|user|>Please note that this mi14 is cursed<|end|>
<|assistant|>{"function":"attach_note_to_user","note":"Cursed mi14","user_id":"1002345"}<|end|>

Anoter example:
99123: Hi everyone
4919438: Hi @phh!
me: Hello, how are you?
19484978515: This is the best ROM, thank you!
me: Great great, thanks for the love
<s><|user|>Remind me that this user thanked me without being annoying<|end|>
<|assistant|>{"function":"attach_note_to_user","note":"Thanked me without being annoying","user_id":"19484978515"}<|end|>

Here comes the discussion:
"""
    while True:
        line = await aioconsole.ainput("> ")
        line = line.strip().rstrip()
        if not line:
            continue
        ret = continue_prompt(prompt + last_conversation + "<s><|user|>" + line + "<|end|>\n<|assistant|>")
        try:
            nextCall = json.loads(ret)
            if not 'function' in nextCall:
                print("Invalid JSON, no function field", nextCall)
                continue
            if nextCall['function'] == 'attach_note_to_user':
                if not 'user_id' in nextCall:
                    print("attach_note_to_user missing required parameter `user_id`")
                    continue
                if not 'note' in nextCall:
                    print("attach_note_to_user missing required parameter `note`")
                    continue
                uid = nextCall['user_id']
                if not uid in users:
                    users[uid] = {}
                if not 'notes' in users[uid]:
                    users[uid]['notes'] = []
                users[uid]['notes'] += [nextCall['note']]

                person = await get_peer(client, telethon.tl.types.PeerUser(int(uid)))
                print(f"{person.username} {person.first_name} {person.last_name}: {users[uid]}")
        except Exception as e:
            print("Failed executing JSON", e)
            print("Stacktrace:", exc_info())

async def main():
    async with telethon.TelegramClient('session_name', api_id, api_hash) as client:
        me = await client.get_me()
        asyncio.create_task(repl(client))
        print("I am", me)
        # 1344234045 = phhtreble
        # 1345606564 = treblewars
        # 4249313609 = stupid group
        @client.on(events.NewMessage(chats=[1344234045, 1345606564, 4249313609]))
        async def new_msg(event):
            global last_conversation
            ctxt = ""
            peers = []
            async for m in client.iter_messages(event.message.peer_id, limit = 10):
                if m.message:
                    peers += [m.from_id.user_id]
                    talker = await get_peer(client, m.from_id)
                    f = talker.id
                    if f == me.id:
                        f = "me"
                    msg_add = [f"{f}: {x}\n" for x in m.message.split("\n")]
                    ctxt = '\n'.join(msg_add) + ctxt
            last_conversation = ctxt
            await handle_new_msg(client, ctxt)

            peers = set(peers)
            for peer in peers:
                talker = await get_peer(client, telethon.tl.types.PeerUser(peer))
                if str(peer) in users:
                    v = users[str(peer)]
                    print(f"{talker.username} {talker.first_name} {talker.last_name}: {v}")

        await client.run_until_disconnected()

asyncio.run(main())

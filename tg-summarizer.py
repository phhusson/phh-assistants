#!/usr/bin/env python

import requests
import telethon
import json
import asyncio
import aioconsole
from aioconsole import aprint
import os
import re
import readability
import aiohttp
from config import features, api_id, api_hash, bot

backend = 'llamacpp'

async def llamacpp_complete(txt):
    data = {
        'stream': False,
        'n_predict': 128,
        "stop": ["</s>", "[/INST]"],
        'temperature': 0.35,
        'top_k': 40,
        'top_p': 0.95,
        'min_p': 0.05,
        'typical_p': 1,
        'cache_prompt': True,
    }

    headers = {'Content-Type': 'application/json'}
    data['prompt'] = txt
    async with aiohttp.ClientSession() as session:
        async with session.post(os.environ['LLAMACPP_SERVER'], data=json.dumps(data), headers=headers) as response:
            response_text = await response.text()
            return json.loads(response_text)['content']


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

    headers = {'Content-Type': 'application/json', "Authorization": f'Bearer {os.environ['TOGETHERXYZ_APIKEY']}'}
    data['prompt'] = txt
    response = requests.post('https://api.together.xyz/inference', data=json.dumps(data), headers=headers)
    if response.status_code != 200:
        print("Failed infering", response.text)
        return "Error"
    return json.loads(response.text)['output']['choices'][0]['text']

def extract_answer(txt):
    # Regular expression pattern to match text between '**'
    pattern = r'\*\*(.*?)\*\*'

    # Find all matches
    matches = re.findall(pattern, txt)

    return matches[0]

# Create a function that continues the request and make "prompt" bigger to retain context
async def continue_prompt(discussion, max_tokens=512):
    if backend == 'togetherXYZ':
        content = togetherxyz_complete(discussion, max_tokens)
    elif backend == 'vllm':
        content = vllm_complete(discussion, max_tokens)
    elif backend == 'llamacpp':
        content = await llamacpp_complete(discussion)
    else:
        raise ValueError(f'{backend} is not in the list of supported backends')

    print("Returning ", content)
    return content

devices = {}

peer_cache = {}
async def get_peer(client, peer_id):
    if not peer_id.user_id in peer_cache:
        peer = await client.get_entity(peer_id)
        peer_cache[peer_id.user_id] = peer
    else:
        peer = peer_cache[peer_id.user_id]
    return peer

def get_features(event):
    channel_id = ""
    if hasattr(event.message.peer_id, 'channel_id'):
        channel_id = str(event.message.peer_id.channel_id)
    elif hasattr(event.message.peer_id, 'chat_id'):
        channel_id = str(event.message.peer_id.chat_id)
    return features.get(channel_id, {})

def extract_url(msg):
    regexp = r'(https?://[^\s]+)'
    urls = re.findall(regexp, msg)
    print("regexp found matches ", urls)
    if not urls:
        return None
    url = urls[0]
    # If there are multiple URLs, skip for now
    if len(urls) > 1:
        # Special case, there are two URLs, and one of them is news.ycombinator.com
        if len(urls) == 2 and 'news.ycombinator.com' in urls:
            # In this case, we can ignore the news.ycombinator.com URL
            urls = [x for x in urls if x != 'news.ycombinator.com']
            url = urls[0]
        else:
            return None
    return url

async def txt_question(client, event, url, msg):
    feat = get_features(event)
    print("Feature set for this channel", feat)
    cont = False
    if feat.get('txt_question', False) == True:
        cont = True
    if not cont:
        return

    print("Question on", url)

    # List of regexp that will go to yt-dlp
    videos = [
        # Youtube
        r'(youtube\.com|youtu\.be)',
        r'vimeo\.com',
        # "Reels" from Instagram
        r'instagram\.com/p/.*',
        # Tiktok
        r'tiktok\.com',
    ]
    is_video = False
    for video in videos:
        if re.search(video, url):
            is_video = True
            break
    if is_video:
        # TODO
        return

    # Remove the @xxx in the msg
    msg = re.sub(r'@[^ ]+', '', msg)
    print("Question is", msg)

    await aprint("Retrieving article from", url)
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            article = await response.text()
    await aprint("Got it")
    doc = readability.Document(article)
    await aprint("Got its readability variant, title is", doc.title())

    prompt = f"""
Here is an article. You'll get a question about it.
Provide a short answer.

{doc.title()}
```html
{doc.summary()}
```
User: {msg}
Assistant: """
    ret = await continue_prompt(prompt)
    # ret = extract_answer(ret)
    await event.reply(f"{ret}")
    print("handl-new-msg", ret)

async def txt_summary(client, event, msg):
    feat = get_features(event)
    print("Feature set for this channel", feat)
    cont = False
    if feat.get('txt_summary', False) == True:
        cont = True
    if feat.get('mentioned_txt_summary', False) == True and event.message.mentioned:
        cont = True
    if not cont:
        return

    url = extract_url(msg)
    if not url:
        return

    # This is a list of regexps for blocklist
    blocklist = [
        # t.me
        r't\.me',
        # All twitter like, x.com, fxtwitter.com, fixupx
        r'(x\.com|fxtwitter\.com|fixupx)',
    ]
    for block in blocklist:
        if re.search(block, url):
            return
    # List of regexp that will go to yt-dlp
    videos = [
        # Youtube
        r'(youtube\.com|youtu\.be)',
        r'vimeo\.com',
        # "Reels" from Instagram
        r'instagram\.com/p/.*',
        # Tiktok
        r'tiktok\.com',
    ]
    is_video = False
    for video in videos:
        if re.search(video, url):
            is_video = True
            break
    if is_video:
        # TODO
        return

    await aprint("Retrieving article from", url)
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            article = await response.text()
    await aprint("Got it")
    doc = readability.Document(article)
    await aprint("Got its readability variant, title is", doc.title())

    prompt = f"""
Please make a one-sentence summary of the following article.
Do not output anything other than the title.
Write your final choice of title in bold with **, like this **this is my title**.

{doc.title()}
```html
{doc.summary()}
```
Assistant: """
    ret = await continue_prompt(prompt)
    ret = extract_answer(ret)
    await event.reply(f"**{ret}**")
    print("handl-new-msg", ret)

# REPL is currently useless, ignore it
async def repl(client):
    global last_conversation
    prompt = """

"""
    while True:
        line = await aioconsole.ainput("> ")
        line = line.strip().rstrip()
        if not line:
            continue
        ret = continue_prompt(prompt + last_conversation + "<s><|user|>" + line + "<|end|>\n<|assistant|>")
        try:
            nextCall = json.loads(ret)
        except Exception as e:
            print("Failed executing JSON", e)
            print("Stacktrace:", exc_info())


# Take a message, and follow the reply-chain, until we find an URL
async def find_url_parent(client, message):
    parent = message
    while parent.reply_to_msg_id:
        parent = await client.get_messages(message.peer_id, ids=parent.reply_to_msg_id)
        url = extract_url(parent.message)
        if url:
            return url
    return None


async def main():
    # User bot
    #async with telethon.TelegramClient('session_name', api_id, api_hash) as client:
    # bot bot
    client = await telethon.TelegramClient('bot', api_id, api_hash).start(bot_token=bot)
    async with client:
        me = await client.get_me()
        asyncio.create_task(repl(client))
        print("I am", me)
        @client.on(telethon.events.NewMessage)
        async def new_msg(event):
            global last_conversation
            await aprint("Received event", event)
            # ctxt = ""
            # peers = []
            # async for m in client.iter_messages(event.message.peer_id, limit = 10):
            #     if m.message:
            #         peers += [m.from_id.user_id]
            #         talker = await get_peer(client, m.from_id)
            #         f = talker.id
            #         if f == me.id:
            #             f = "me"
            #         msg_add = [f"{f}: {x}\n" for x in m.message.split("\n")]
            #         ctxt = '\n'.join(msg_add) + ctxt
            # last_conversation = ctxt


            # If bot is mentioned, and it is a reply, then redirect to txt_question
            if event.message.mentioned:
                await aprint("MENTIONED!")
                if event.message.reply_to_msg_id:
                    # Retrieve the original message
                    original_url = await find_url_parent(client, event.message)
                    await txt_question(client, event, original_url, event.message.message)
            await txt_summary(client, event, event.message.message)

            # peers = set(peers)
            # for peer in peers:
            #     talker = await get_peer(client, telethon.tl.types.PeerUser(peer))
            #     if str(peer) in users:
            #         v = users[str(peer)]
            #         print(f"{talker.username} {talker.first_name} {talker.last_name}: {v}")

        await client.run_until_disconnected()

asyncio.run(main())

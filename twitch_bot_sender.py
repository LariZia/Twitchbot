import eventlet
eventlet.monkey_patch()

import asyncio
import configparser
import requests
import random
import threading
import csv
from datetime import datetime
from twitchio.ext import commands
from flask import Flask, request, redirect
import os
import logging
from flask_twitch_webapp2 import load_config, refresh_access_token
from fetch_streamers import get_latest_timestamp


from flask_socketio import SocketIO

redis_url = os.environ.get("REDIS_URL", os.environ.get("REDIS_URL")) #"redis://127.0.0.1:6379")
socketio = SocketIO(message_queue=redis_url, async_mode="eventlet")
# socketio = SocketIO(message_queue="redis://127.0.0.1:6379", async_mode="eventlet")


# # Flask app for handling authorization process
# app = Flask(__name__)

# # Get the folder where the EXE is located
# base_dir = os.path.dirname(os.path.abspath(__file__))

# # Construct the path to the config file in the same folder
# config_path = os.path.join(base_dir, "CONFIG2.ini")

# # Read the configuration
# config = configparser.ConfigParser()
# config.read(config_path)

socketio.emit("bot_log_update", {"log": "Bot started"})



config = load_config()

# Extract Twitch credentials from config
ACCESS_TOKEN = config.get('Twitch', 'access_token').strip()
REFRESH_TOKEN = config.get('Twitch', 'refresh_token').strip()
CLIENT_ID = config.get('Twitch', 'client_id').strip()
CLIENT_SECRET = config.get('Twitch', 'client_secret').strip()
REDIRECT_URL = config.get('Twitch', 'redirect_uri').strip()
initial_messages = config.get('Bot', 'initial_messages', fallback="Hello|Welcome").split('|')
second_messages = config.get('Bot', 'second_messages').split('|')
timer = config.get('Bot', 'message_interval', fallback=120) # 2 min fallback 

prompt_pairs = list(zip(initial_messages, second_messages))

# Twitch Helix API endpoint for checking stream status
TWITCH_HELIX_STREAMS_URL = config.get('Helix', 'api_url').strip()

def refresh(rt):
    url  = "https://id.twitch.tv/oauth2/token"
    data = {
      "grant_type":"refresh_token",
      "refresh_token":rt,
      "client_id":CLIENT_ID,
      "client_secret":CLIENT_SECRET
    }
    r = requests.post(url,data=data,timeout=10)
    if not r.ok:
        print("Token refresh failed:",r.status_code,r.text)
        return None,None
    j = r.json()
    return j["access_token"], j["refresh_token"]

if REFRESH_TOKEN:
    new_at, new_rt = refresh(REFRESH_TOKEN)
    if not new_at:
        raise RuntimeError("Failed to refresh Twitch token – re-auth required.")
    config["Twitch"]["access_token"]  = new_at
    config["Twitch"]["refresh_token"] = new_rt
    with open("CONFIG2.ini","w") as f:
        config.write(f)
    ACCESS_TOKEN = new_at
else:
    raise RuntimeError("No refresh_token in CONFIG2.ini – do one /act → /callback cycle first")


timestamp = get_latest_timestamp()
LOG_FILE = f"static/streamers_{timestamp}.csv"

socketio.emit("bot_log_update", {"log": f"Reading file {LOG_FILE}"})


# Configure logging
logging.basicConfig(
    filename=f"logs/streamers_debug_{timestamp}.log",
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# Add an info log to confirm it's working
logging.info("Bot started.")

# Function to read channel names from the CSV file
def read_channel_names_from_csv(csv_file_path):
    channel_names = []
    try:
        with open(csv_file_path, mode='r', encoding="utf-8", newline="") as file:
            reader = csv.reader(file)
            next(reader)  # Skip the header row
            for row in reader:
                if row:  # Ensure the row is not empty
                    channel_names.append(row[1])  #  channel names are in the second column
    except FileNotFoundError:
        log_message = f"CSV file not found at {csv_file_path}"
        print(log_message)
        socketio.emit("bot_log_update", {"log": log_message})


    except Exception as e:
        log_message = f"Error reading CSV file: {e}"
        print(log_message)
        socketio.emit("bot_log_update", {"log": log_message})

    return channel_names

# Read channel names from the CSV file
CHANNELS = read_channel_names_from_csv(LOG_FILE)

# Track conversation state and timeouts
conversation_state = {channel: {
                        "waiting": False, 
                        "timeout_task": None,
                        "message_count": 0,
                        "start_time": None, 
                        "first_reply": None, 
                        "second_reply": None, 
                        "socials": [], 
                        "abandoned": False, 
                        "initial_message_sent": False, 
                        "followup_message_sent": False,
                        "message_pair": None 
                    } 
                    for channel in CHANNELS}

# @app.route("/")
# def home():
#     """Redirect the user to Twitch's authorization page."""
#     twitch_auth_url = (
#         f"https://id.twitch.tv/oauth2/authorize"
#         f"?response_type=code"
#         f"&client_id={CLIENT_ID}"
#         f"&redirect_uri={REDIRECT_URL}"
#         f"&scope=chat:read+chat:edit+channel:moderate"
#         f"&state=random_state_value"
#     )

#     return redirect(twitch_auth_url)

# @app.route("/callback")
# def callback():
#     """Handle the callback from Twitch and retrieve the authorization code."""
#     code = request.args.get("code")

#     if not code:
#         return "Error: No code received from Twitch.", 400

#     print(f"Received code: {code}")

#     # Exchange the code for a new access token
#     new_access_token = get_user_access_token(CLIENT_ID, CLIENT_SECRET, code)
#     if new_access_token:
#         return "Authorization successful! You can now close this tab."
#     else:
#         return "Failed to retrieve access token.", 500

# def get_user_access_token(client_id, client_secret, code):
#     """Exchange the authorization code for an access token."""
#     url = "https://id.twitch.tv/oauth2/token"
#     data = {
#         "client_id": client_id,
#         "client_secret": client_secret,
#         "grant_type": "authorization_code",
#         "redirect_uri": REDIRECT_URL,
#         "code": code,
#     }

#     response = requests.post(url, data=data)
#     if response.status_code == 200:
#         response_data = response.json()
#         new_access_token = response_data["access_token"]
#         refresh_token = response_data["refresh_token"]

#         print("New Access Token:", new_access_token)
#         print("Refresh Token:", refresh_token)

#         # Save tokens to config
#         config["Twitch"]["access_token"] = new_access_token
#         config["Twitch"]["refresh_token"] = refresh_token
#         with open(config_path, "w") as configfile:
#             config.write(configfile)

#         return new_access_token
#     else:
#         print(f"Failed to get access token: {response.status_code} - {response.text}")
#         return None

# def refresh_access_token():
#     """Refresh the Twitch access token using the refresh token."""
#     global ACCESS_TOKEN, REFRESH_TOKEN  # Ensure updated values persist

#     if not REFRESH_TOKEN:
#         print("No refresh token available.")
#         return None

#     url = "https://id.twitch.tv/oauth2/token"
#     data = {
#         "grant_type": "refresh_token",
#         "refresh_token": REFRESH_TOKEN,
#         "client_id": CLIENT_ID,
#         "client_secret": CLIENT_SECRET,
#     }

#     try:
#         print("Sending request to refresh token...")
#         response = requests.post(url, data=data)

#         if response.status_code == 200:
#             response_data = response.json()
#             ACCESS_TOKEN = response_data["access_token"]
#             REFRESH_TOKEN = response_data["refresh_token"]

#             print("Access Token Refreshed:", ACCESS_TOKEN)
#             print("Refresh Token Updated:", REFRESH_TOKEN)

#             # Save the new access and refresh tokens
#             config["Twitch"]["access_token"] = ACCESS_TOKEN
#             config["Twitch"]["refresh_token"] = REFRESH_TOKEN
#             with open(config_path, "w") as configfile:
#                 config.write(configfile)

#             return ACCESS_TOKEN
#         else:
#             print(f"Failed to refresh token: {response.status_code} - {response.text}")
#             return None

#     except requests.exceptions.RequestException as e:
#         print(f"Exception occurred during token refresh: {e}")
#         return None

class TwitchBot(commands.Bot):
    def __init__(self):
        """Initialize the Twitch bot with valid credentials."""
        global ACCESS_TOKEN
        if not ACCESS_TOKEN:
            print("Error: No valid access token. Attempting refresh...")
            log_message = f"Error: No valid access token. Attempting refresh..."
            logging.error(log_message)
            socketio.emit("bot_log_update", {"log": log_message})

            ACCESS_TOKEN = refresh_access_token()
            if not ACCESS_TOKEN:
                print("Error: Could not refresh token. Exiting...")
                log_message = f"Error: Could not refresh token. Exiting..."
                logging.info(log_message)
                socketio.emit("bot_log_update", {"log": log_message})
                return

        super().__init__(token=f'oauth:{ACCESS_TOKEN}', prefix='!', initial_channels=CHANNELS)
        self.prompt_pairs = prompt_pairs
        self.timer = timer

    async def event_ready(self):
        """Called when the bot successfully logs in."""
        print(f"Logged in as | {self.nick}")
        socketio.emit("bot_log_update", {"log": f"Logged in as | {self.nick}"})

        self.loop.create_task(self.send_initial_messages())
        self.loop.create_task(self.monitor_stream_status())  # Start monitoring stream status

    async def send_initial_messages(self):
        """Send initial messages to Twitch chat if the stream is live."""
        for channel_name in CHANNELS:
            # Check if the stream is live before sending a message
            is_live = await self._check_stream_status(channel_name)
            if not is_live:
                # Emit log update
                log_message = f"Stream is offline for {channel_name}. Skipping message."
                print(log_message)
                logging.info(log_message)
                socketio.emit("bot_log_update", {"log": log_message})
                continue  # Skip sending a message if the stream is offline

            # Send an initial message if the stream is live
            if not conversation_state[channel_name]["initial_message_sent"]:
                # pick & store exactly one pair for this channel
                pair = random.choice(self.prompt_pairs)
                conversation_state[channel_name]["message_pair"] = pair

                # send the initial msg 
                initial_msg, _ = pair

                channel = self.get_channel(channel_name)
                if channel:
                    await channel.send(initial_msg)
                    print(f"Initial message sent to {channel_name}: {initial_msg}")
                    # Emit log update
                    log_message = f"Initial message sent to {channel_name}: {initial_msg}"
                    logging.info(log_message)
                    socketio.emit("bot_log_update", {"log": log_message})

                    # Start waiting for a response and set a 10-minute timeout
                    conversation_state[channel_name]["waiting"] = True
                    conversation_state[channel_name]["start_time"] = datetime.now()
                    conversation_state[channel_name]["timeout_task"] = asyncio.create_task(
                        self._response_timeout(channel_name)
                    )
                    conversation_state[channel_name]["initial_message_sent"] = True
                else:
                    print(f"Unable to find channel: {channel_name}")
                    # Emit log update
                    log_message = f"Unable to find channel: {channel_name}"
                    logging.info(log_message)
                    socketio.emit("bot_log_update", {"log": log_message})

            await asyncio.sleep(1)  # Small delay to avoid rate limiting

    async def _response_timeout(self, channel_name):
        """Handle the 10-minute timeout for waiting for a response."""
        await asyncio.sleep(float(self.timer))  # 2 minutes = 120 seconds
        if conversation_state[channel_name]["waiting"]:
            print(f"Timeout reached for {channel_name}. No response received.")
            log_message = f"Timeout reached for {channel_name}. No response received."
            logging.info(log_message)
            socketio.emit("bot_log_update", {"log": log_message})

            if not conversation_state[channel_name]["followup_message_sent"]:
                # Send a follow-up message if no response was received
                pair = conversation_state[channel_name]["message_pair"]
                _, followup_msg = pair

                channel = self.get_channel(channel_name)
                if channel:
                    await channel.send(followup_msg)
                    print(f"Follow-up message sent to {channel_name}: {followup_msg}")
                    log_message = f"Follow-up message sent to {channel_name}: {followup_msg}"
                    logging.info(log_message)
                    socketio.emit("bot_log_update", {"log": log_message})

                    # Start waiting for a response and set another 10-minute timeout
                    conversation_state[channel_name]["waiting"] = True
                    conversation_state[channel_name]["start_time"] = datetime.now()
                    conversation_state[channel_name]["timeout_task"] = asyncio.create_task(
                        self._response_timeout(channel_name)
                    )
                    conversation_state[channel_name]["followup_message_sent"] = True
                else:
                    print(f"Unable to find channel: {channel_name}")
                    log_message = f"Unable to find channel: {channel_name}"
                    logging.info(log_message)
                    socketio.emit("bot_log_update", {"log": log_message})
            else:
                # If no response after follow-up message, log as abandoned
                conversation_state[channel_name]["waiting"] = False
                conversation_state[channel_name]["abandoned"] = True
                self._log_conversation(channel_name)

    async def monitor_stream_status(self):
        """Periodically check if the stream is live."""
        while True:
            for channel_name in CHANNELS:
                if conversation_state[channel_name]["waiting"]:
                    is_live = await self._check_stream_status(channel_name)
                    if not is_live:
                        print(f"Stream ended for {channel_name}. Stopping response wait.")
                        log_message = f"Stream ended for {channel_name}. Stopping response wait."
                        logging.info(log_message)
                        socketio.emit("bot_log_update", {"log": log_message})

                        conversation_state[channel_name]["waiting"] = False
                        conversation_state[channel_name]["abandoned"] = True
                        if conversation_state[channel_name]["timeout_task"]:
                            conversation_state[channel_name]["timeout_task"].cancel()  # Cancel the timeout task
                        self._log_conversation(channel_name)

            await asyncio.sleep(60)  # Check stream status every 60 seconds

    async def _check_stream_status(self, channel_name):
        """Check if the stream is live using the Twitch Helix API."""
        headers = {
            "Client-ID": CLIENT_ID,
            "Authorization": f"Bearer {ACCESS_TOKEN}",
        }
        params = {"user_login": channel_name}

        try:  
            response = requests.get(TWITCH_HELIX_STREAMS_URL, headers=headers, params=params)
            if response.status_code == 200:
                data = response.json()
                return len(data["data"]) > 0  # Stream is live if data is not empty
            else:
                print(f"Failed to check stream status for {channel_name}: {response.status_code} - {response.text}")
                log_message = f"Failed to check stream status for {channel_name}: {response.status_code} - {response.text}"
                logging.info(log_message)
                socketio.emit("bot_log_update", {"log": log_message})
                return False
        except Exception as e:
            print(f"Exception occurred while checking stream status: {e}")
            log_message = f"Exception occurred while checking stream status: {e}"
            logging.error(log_message)
            socketio.emit("bot_log_update", {"log": log_message})
            return False

    async def event_message(self, message):
        """Handle incoming chat messages."""
        global conversation_state

        # Ignore messages without an author (e.g., system messages)
        if not message.author:
            return

         # Ignore messages from the bot itself 
        if message.author.name.lower() == self.nick.lower():
            return
        
        channel_name = message.channel.name
        if conversation_state.get(channel_name, {}).get("waiting", False):  # Check if we're waiting for a response
            if message.author.name.lower() == channel_name.lower():
                content = message.content.lower()

                if "discord" in content or "instagram" in content:
                    print(f"{message.author.name} provided a social link: {message.content}")
                    log_message = f"{message.author.name} provided a social link: {message.content}"
                    logging.info(log_message)
                    socketio.emit("bot_log_update", {"log": log_message})

                    conversation_state[channel_name]["socials"].append(message.content)
                    if conversation_state[channel_name]["message_count"] == 0:
                        conversation_state[channel_name]["first_reply"] = message.content
                    else:
                        conversation_state[channel_name]["second_reply"] = message.content
                    conversation_state[channel_name]["message_count"] += 1
                    if conversation_state[channel_name]["message_count"] >= 2:
                        conversation_state[channel_name]["waiting"] = False  # Reset state
                        if conversation_state[channel_name]["timeout_task"]:
                            conversation_state[channel_name]["timeout_task"].cancel()  # Cancel the timeout task
                        self._log_conversation(channel_name)
                else:
                    if not conversation_state[channel_name]["fallback_prompt_sent"]:
                        pair = conversation_state[channel_name]["message_pair"] #or random.choice(self.prompt_pairs)
                        _, followup_msg = pair
                        await message.channel.send(f"@{message.author.name}, {followup_msg}") 
                        log_message = f"{message.author.name} did not provide a social link. Sending follow-up... with {followup_msg}"
                        print(log_message)
                        logging.info(log_message)

                        socketio.emit("bot_log_update", {"log": log_message})
                        # mark that we've prompted once, so we won't spam
                        conversation_state[channel_name]["fallback_prompt_sent"] = True

                   
    def _log_conversation(self, channel_name):
        """Log the conversation details to the CSV file."""
        with open(LOG_FILE, mode='a', newline='') as file:
            writer = csv.writer(file)
            writer.writerow([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                channel_name,
                conversation_state[channel_name]["message_count"],
                conversation_state[channel_name]["first_reply"],
                conversation_state[channel_name]["second_reply"],
                conversation_state[channel_name]["socials"],
                conversation_state[channel_name]["abandoned"]
            ])

# def run_flask():
#     """Run Flask app in a separate thread to prevent blocking the main script."""
#     app.run(port=3000, debug=True, use_reloader=False)

# if __name__ == "__main__":
#     # Run Flask in a separate thread
#     flask_thread = threading.Thread(target=run_flask, daemon=True)
#     flask_thread.start()

#     # Wait a few seconds to ensure Flask is running
#     asyncio.run(asyncio.sleep(3))  # Await the sleep coroutine

#     # Create and set a new event loop for the main thread
#     loop = asyncio.new_event_loop()
#     asyncio.set_event_loop(loop)

#     # Start the bot
#     if ACCESS_TOKEN:
#         print("Starting Twitch bot...")
#         bot = TwitchBot()
#         loop.run_until_complete(bot.run())
#     else: 
#         print("Error: Unable to get a valid access token.")

if __name__ == "__main__":
    import asyncio

    # 1) Create a fresh event loop…
    loop = asyncio.new_event_loop()
    # 2) …and make it the “current” one on this thread
    asyncio.set_event_loop(loop)

    # Now TwitchIO’s Bot() and bot.run() will see a valid loop
    # sanity check
    if not ACCESS_TOKEN:
        log_message = "Error: No valid access token. Exiting…"
        print(log_message)
        socketio.emit("bot_log_update", {"log": log_message})

    else:
        log_message = "Starting Twitch bot…"
        print(log_message)
        socketio.emit("bot_log_update", {"log": log_message})
        bot = TwitchBot()
        # This will block and run your event loop
        bot.run()

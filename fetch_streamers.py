import eventlet
eventlet.monkey_patch()

import requests
import time
import csv
import os
import logging
import configparser
import argparse
from datetime import datetime
from flask_socketio import SocketIO


# Connect to the Flask-SocketIO server
# socketio = SocketIO(message_queue="redis://127.0.0.1:6379", async_mode="eventlet")  # Ensure Redis is running for message queue
redis_url = os.environ.get("REDIS_URL", os.environ.get("REDIS_URL"))
socketio = SocketIO(message_queue=redis_url, async_mode="eventlet")  # Ensure Redis is running for message queue


# Setup argument parser
parser = argparse.ArgumentParser(description="Fetch Twitch streamers and log their details.")
parser.add_argument("--language", type=str, default="en", help="Language filter for streamers")
parser.add_argument("--tags_filter", type=str, default="", help="Comma-separated list of tags to filter by")
parser.add_argument("--game",       type=str, default="", help="Game name to filter by (takes priority over tags)")
parser.add_argument("--max_viewers", type=int, default=50, help="Maximum number of viewers a streamer can have")
parser.add_argument("--limit", type=int, default=100, help="Number of streamers to fetch per request")
parser.add_argument("--max_pages", type=int, default=100, help="Maximum number of pagination requests")
parser.add_argument("--retry_delay", type=int, default=2, help="Delay in seconds before retrying failed requests")
parser.add_argument("--log_count", type=int, default=10, help="Number of streamers to log")

args = parser.parse_args()

# Convert tags_filter from comma-separated string to list
tags_list = args.tags_filter.split(",") if args.tags_filter else []
game_filter = args.game.strip().lower() if args.game else ''

# CSV file to log the details
def get_latest_timestamp():
    try:
        with open("logs/timestamp.txt", "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""
    
timestamp = get_latest_timestamp()
# timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

# # Save the timestamp to a file
# os.makedirs("logs", exist_ok=True)
# with open("logs/timestamp.txt", "w") as f:
#     f.write(timestamp)

# Configure logging
logging.basicConfig(
    filename=f"logs/streamers_debug_{timestamp}.log",
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# Add an info log to confirm it's working
logging.info("Logging system initialized.")


# Get the folder where the EXE is located
base_dir = os.path.dirname(os.path.abspath(__file__))

# Construct the path to the config file in the same folder
config_path = os.path.join(base_dir, "CONFIG2.ini")

# Read the configuration
config = configparser.ConfigParser()
config.read(config_path)

# Extract Twitch credentials from config
try:
    ACCESS_TOKEN = config.get('Helix', 'access_token').strip()
    # if not ACCESS_TOKEN:
    #     print("not")

    CLIENT_ID = config.get('Helix', 'client_id').strip()
    TWITCH_API_URL = config.get('Helix', 'api_url').strip()
except Exception as e:
    logging.error(f"Error reading config file: {e}")
    raise


os.makedirs("static", exist_ok=True)
CSV_FILE  = f"static/streamers_{timestamp}.csv"


# # Ensure CSV file has a header
# if not os.path.exists(CSV_FILE):
#     with open(CSV_FILE, mode="w", newline="") as file:
#         writer = csv.writer(file)
#         writer.writerow(["Timestamp", "Streamer", "Viewers", "Game", "Tags"])

def fetch_streamers(language='en', tags=None,game_filter=None, max_viewers=50, limit=10000, max_pages=100, retry_delay=2, log_count=10):
    """
    Fetches Twitch streamers with pagination, filtering by viewer count, language, and tags,
    and logs them into a CSV file.
    """
    headers = {
        "Client-ID": CLIENT_ID,
        "Authorization": f"Bearer {ACCESS_TOKEN}"
    }
    params = {"first": limit}
    streamers = []
    page_cursor = None
    current_page = 0
    below_threshold_count = 0

    while current_page < max_pages:
        current_page += 1
        log_message = f"Fetching page {current_page}..."
        logging.info(log_message)
        socketio.emit("log_update", {"log": log_message})  # Send log to frontend

        if page_cursor:
            params["after"] = page_cursor

        try:
            response = requests.get(TWITCH_API_URL, headers=headers, params=params)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            error_message = f"Error fetching streamers: {e}"
            logging.error(error_message)
            socketio.emit("log_update", {"log": error_message})
            return []

        data = response.json()
        streams = data.get("data", [])
        page_cursor = data.get("pagination", {}).get("cursor")

        filtered_streams = []
        for stream in streams:
            if (stream["viewer_count"] < max_viewers and stream.get("language") == language):
                # socketio.emit("log_update", {"log": f"in if streame, game filter = {game_filter}, tags = {tags}"})
            #     continue
            # if language and stream.get("language") != language:
            #     continue
            # game filter takes priority over tags
                if game_filter:

                    game_name = stream.get("game_name").strip()
                    # socketio.emit("log_update", {"log":f"in if streame games {game_name}, {game_filter}"})

                    if game_name.lower() in game_filter.lower():
                        # socketio.emit("log_update", {"log": "game filter if: "+game_name.lower() in game_filter.lower()})
                        filtered_streams.append(stream)
                        # logging.debug(f"Skipping {stream['user_name']} - game '{game_name}' doesn't match filter '{game_filter}'")
                        
                elif tags:
                    stream_tags = stream.get("tags") or []
                    # socketio.emit("log_update", {"log": f"in if streame tags {stream_tags}, tags input {tags}"})

                    if any(tag.lower() in [t.lower() for t in stream_tags] for tag in tags):
                        # socketio.emit("log_update", {"log":f"conidition --> {any(tag.lower() in [t.lower() for t in stream_tags] for tag in tags)}"})
                        filtered_streams.append(stream)
                    else: 
                        continue
                else:
                    continue  
            else:
                continue
            # if tags:
            #     stream_tags = stream.get("tags", []) or []
            #     if not all(tag.lower() in [t.lower() for t in stream_tags] for tag in tags):
            #         continue
            
            # filtered_streams.append(stream)
            if below_threshold_count < log_count:
                try:
                    CSV_HEADERS = [
                    "Timestamp", "Streamer", "Viewers", "Language", "Game", "Tags",
                    "First Message Sent", "First Message Timestamp", "First Reply", "First Reply Timestamp",
                    "Second Message Sent", "Second Reply", "Second Reply Timestamp",
                    "Socials", "Abandoned", "Timeout"
                    ]
                    # Default empty values for unused columns
                    DEFAULT_VALUES = {header: "" for header in CSV_HEADERS}

                    streamer_name = stream["user_name"]
                    viewer_count = stream["viewer_count"]
                    game_name = stream["game_name"]
                    stream_tags = stream.get("tags", []) or []
                    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
                   
                    
                    # Prepare the row data
                    row_data = {
                        **DEFAULT_VALUES,  # Start with empty values for all columns
                        "Timestamp": timestamp,
                        "Streamer": streamer_name,
                        "Viewers": viewer_count,
                        "Language": language,
                        "Game": game_name,
                        "Tags": str(stream_tags)  # Convert list to string to avoid splitting
                        }
                    
                    # with open(CSV_FILE, mode="a", newline="") as file:
                    #     writer = csv.writer(file)
                    #     writer.writerow([timestamp, streamer_name, viewer_count, language, game_name, stream_tags])
                    
                    # Write to CSV
                    with open(CSV_FILE, mode="a", newline="", encoding="utf-8") as file:
                        writer = csv.DictWriter(file, fieldnames=CSV_HEADERS)
                        if file.tell() == 0:  # Write headers only if file is empty
                            writer.writeheader()
                        writer.writerow(row_data)
                    below_threshold_count += 1
                    
                    # Emit log update
                    log_message = f"Logged: {streamer_name} - {viewer_count} viewers - {game_name} - tags {stream_tags}"
                    logging.info(log_message)
                    socketio.emit("log_update", {"log": log_message})

                except Exception as e:
                    log_message = f"Error logging streamer {stream['user_name']}: {e}"
                    logging.error(log_message)
                    socketio.emit("error_logging", {'log': log_message})

                
                if below_threshold_count >= log_count:
                    log_message = f"Logged details of {log_count} streamers below {max_viewers} viewers."
                    logging.info(log_message)
                    socketio.emit("fetch_complete", {"log": "Fetching complete!"})
                    break

    

        if filtered_streams:
            streamers.extend(filtered_streams)

        if below_threshold_count >= log_count:
            socketio.emit("fetch_complete", {"log": "Fetching complete!"})
            return streamers

        if not page_cursor:
            logging.info("No more pages available.")
            break

        logging.info(f"No streamers found on page {current_page}. Retrying in {retry_delay} seconds...")
        time.sleep(retry_delay)

    logging.error("Reached max pages but found no streamers.")
    return streamers

# Example Usage
# language_filter = "en"
# tags_filter = ["minecraft"]
# streamers = fetch_streamers(language_filter, tags_filter, max_viewers=5000, log_count=5)

# if not streamers:
#     logging.info("No streamers found matching criteria.")
# else:
#     for streamer in streamers:
#         logging.info(f"{streamer['user_name']} - {streamer['viewer_count']} viewers - {streamer['game_name']}")
#         logging.info(f"Tags: {streamer.get('tags', [])}")


# Run fetch with CLI arguments
streamers = fetch_streamers(
    language=args.language,
    tags=tags_list,
    game_filter=game_filter,
    max_viewers=args.max_viewers,
    limit=args.limit,
    max_pages=args.max_pages,
    retry_delay=args.retry_delay,
    log_count=args.log_count
)

if not streamers:
    logging.info("No streamers found matching criteria.")
else:
    for streamer in streamers:
        logging.info(f"{streamer['user_name']} - {streamer['viewer_count']} viewers - {streamer['game_name']}")
        logging.info(f"Tags: {streamer.get('tags', [])}")


import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request, redirect, url_for, jsonify, send_file
from flask_socketio import SocketIO
import configparser
import os
import threading
import subprocess
import sqlite3
import time
import csv
from datetime import datetime
import requests

app = Flask(__name__)
# socketio = SocketIO(app)  # Initialize Flask-SocketIO
# socketio = SocketIO(app, message_queue="redis://127.0.0.1:6379", async_mode="eventlet")  # <-- Use Redis
redis_url = os.environ.get("REDIS_URL", os.environ.get("REDIS_URL")) #"redis://127.0.0.1:6379")
socketio = SocketIO(app, message_queue=redis_url, async_mode="eventlet")

CONFIG_FILE = "CONFIG2.ini"
DB_FOLDER = "static/"  # Folder to store CSV files
DB_FILE = "streamers.db"


if not os.path.exists(DB_FOLDER):
    os.makedirs(DB_FOLDER)


# Initialize database
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS streamers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            viewers INTEGER,
            language TEXT,
            timestamp TEXT,
            first_message_sent TEXT,
            first_message_timestamp TEXT,
            first_reply TEXT,
            first_reply_timestamp TEXT,
            second_message_sent TEXT,
            second_reply TEXT,
            second_reply_timestamp TEXT,
            socials TEXT,
            abandoned TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# Load configuration
def load_config():
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    return config

def save_config(new_config):
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    for section, values in new_config.items():
        for key, value in values.items():
            if not config.has_section(section):
                config.add_section(section)
            config.set(section, key, value)
    with open(CONFIG_FILE, "w") as configfile:
        config.write(configfile)

# Fetch streamers from the database
def get_streamers(from_date, to_date):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT name, viewers, language, timestamp, first_message_sent, first_message_timestamp, 
               first_reply, first_reply_timestamp, second_message_sent, second_reply, 
               second_reply_timestamp, socials, abandoned 
        FROM streamers WHERE timestamp BETWEEN ? AND ?""", (from_date, to_date))
    results = cursor.fetchall()
    conn.close()
    return results


config = load_config()
# Extract Twitch credentials from config
ACCESS_TOKEN = config.get('Twitch', 'access_token').strip()
REFRESH_TOKEN = config.get('Twitch', 'refresh_token').strip()
CLIENT_ID = config.get('Twitch', 'client_id').strip()
CLIENT_SECRET = config.get('Twitch', 'client_secret').strip()
REDIRECT_URL = config.get('Twitch', 'redirect_uri').strip()
MESSAGES = config.get('Bot', 'messages', fallback="Hello|Welcome").split('|')




@app.route('/')
def home():
    return render_template("index.html")

@app.route('/act')
def act():
    twitch_auth_url = (
    f"https://id.twitch.tv/oauth2/authorize"
    f"?response_type=code"
    f"&client_id={CLIENT_ID}"
    f"&redirect_uri={REDIRECT_URL}"
    f"&scope=chat:read+chat:edit+channel:moderate"
    f"&state=random_state_value"
    )

    return redirect(twitch_auth_url, code=302)

@app.route("/callback")
def callback():
    """Handle the callback from Twitch and retrieve the authorization code."""
    code = request.args.get("code")

    if not code:
        return "Error: No code received from Twitch.", 400

    # print(f"Received code: {code}")

    # # Exchange the code for a new access token
    # new_access_token = get_user_access_token(CLIENT_ID, CLIENT_SECRET, code)
    # if new_access_token:
    #     return "Authorization successful! You can now close this tab."
    # elif not new_access_token:
    #     return "Failed to retrieve access token.", 500
    # else:
    #     return  redirect(url_for("bot_logs"))


    # exchange code for tokens and save into CONFIG2.ini
    new_access_token = get_user_access_token(CLIENT_ID, CLIENT_SECRET, code)
    if not new_access_token:
        return "Failed to retrieve access token.", 500

    # reload your module-level config and globals so bot_logs() sees the new refresh_token
    global config, ACCESS_TOKEN, REFRESH_TOKEN
    config         = load_config()
    ACCESS_TOKEN   = config.get("Twitch", "access_token").strip()
    REFRESH_TOKEN  = config.get("Twitch", "refresh_token").strip()

    # finally, send the user right back to /bot_logs
    return redirect(url_for("bot_logs"))

def get_user_access_token(client_id, client_secret, code):
    """Exchange the authorization code for an access token."""
    url = "https://id.twitch.tv/oauth2/token"
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URL,
        "code": code,
    }

    response = requests.post(url, data=data)
    if response.status_code == 200:
        response_data = response.json()
        new_access_token = response_data["access_token"]
        refresh_token = response_data["refresh_token"]

        print("New Access Token:", new_access_token)
        print("Refresh Token:", refresh_token)

        # Save tokens to config
        config["Twitch"]["access_token"] = new_access_token
        config["Twitch"]["refresh_token"] = refresh_token
        with open(CONFIG_FILE, "w") as configfile:
            config.write(configfile)

        return new_access_token
    else:
        print(f"Failed to get access token: {response.status_code} - {response.text}")
        return None

def refresh_access_token():
    """Refresh the Twitch access token using the refresh token."""
    global ACCESS_TOKEN, REFRESH_TOKEN  # Ensure updated values persist

    if not REFRESH_TOKEN:
        print("No refresh token available.")
        return None

    url = "https://id.twitch.tv/oauth2/token"
    data = {
        "grant_type": "refresh_token",
        "refresh_token": REFRESH_TOKEN,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }

    try:
        print("Sending request to refresh token...")
        response = requests.post(url, data=data)

        if response.status_code == 200:
            response_data = response.json()
            ACCESS_TOKEN = response_data["access_token"]
            REFRESH_TOKEN = response_data["refresh_token"]

            print("Access Token Refreshed:", ACCESS_TOKEN)
            print("Refresh Token Updated:", REFRESH_TOKEN)

            # Save the new access and refresh tokens
            config["Twitch"]["access_token"] = ACCESS_TOKEN
            config["Twitch"]["refresh_token"] = REFRESH_TOKEN
            with open(CONFIG_FILE, "w") as configfile:
                config.write(configfile)

            return ACCESS_TOKEN
        else:
            print(f"Failed to refresh token: {response.status_code} - {response.text}")
            return None

    except requests.exceptions.RequestException as e:
        print(f"Exception occurred during token refresh: {e}")
        return None

@app.route('/configure', methods=["GET", "POST"])
def configure():
    config = load_config()
    if request.method == "POST":
        new_config = {
            "Twitch": {
                "client_id": request.form["client_id"],
                "client_secret": request.form["client_secret"],
                "access_token": request.form["access_token"],
                "refresh_token": request.form["refresh_token"],
            }
        }
        save_config(new_config)
        return redirect(url_for("fetch_streamers"))
    return render_template("configure.html", config=config)


def run_fetch_script(language, max_viewers, log_count, tags_list,game_filter, output_file):
    """Runs the fetching script and sends logs to the frontend in real-time."""
    command = ["python", "fetch_streamers.py", "--language", language, 
               "--max_viewers", str(max_viewers), "--log_count", str(log_count),
               "--tags_filter", ",".join(tags_list), "--game",game_filter]

    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as process:
        for line in process.stdout:
            socketio.emit("log_update", {"log": line.strip()})
    
    socketio.emit("fetch_complete", {"message": "Fetching complete!", "output_file": output_file})


@app.route('/fetch_streamers', methods=["GET", "POST"])
def fetch_streamers():
    if request.method == "POST":
        language = request.form["language"]
        max_viewers = int(request.form["max_viewers"])
        log_count = int(request.form["log_count"])
        game_filter = request.form["game_filter"]
        tags_filter = request.form["tags_filter"]
        
        # Convert the comma-separated tags string into a list
        tags_list = [tag.strip() for tag in tags_filter.split(',')] if tags_filter else []
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Save the timestamp to a file
        os.makedirs("logs", exist_ok=True)
        with open("logs/timestamp.txt", "w") as f:
            f.write(timestamp)
        output_file = os.path.join(DB_FOLDER, f"streamers_{timestamp}.csv")
        # Ensure CSV file has a header
        if not os.path.exists(output_file):
            with open(output_file, mode="w", newline="") as file:
                writer = csv.writer(file)
                writer.writerow(["Timestamp", "Streamer", "Viewers", "Language", "Game", "Tags", "First Message Sent",
                                  "First Message Timestamp", "First Reply", "First Reply Timestamp", "Second Message Sent",
                                  "Second Reply", "Second Reply Timestamp", "Socials", "Abandoned", "Timeout"])
        # thread = threading.Thread(target=run_fetch_script, args=(language, tags_list, game_filter, max_viewers, log_count,  output_file))

        thread = threading.Thread(target=run_fetch_script, args=(language, max_viewers, log_count,tags_list, game_filter, output_file))
        thread.start()
        return jsonify({"message": "Fetching started...", "output_file": output_file})
    
    existing_files = sorted(os.listdir(DB_FOLDER), reverse=True)
    return render_template("fetch_streamers.html", files=existing_files)

# def run_fetch_script(language, max_viewers, limit, tags_list, output_file):
#     command = ["python", "fetch_streamers.py", "--language", language, "--max_viewers", max_viewers, "--limit", limit, "--tags_filter", tags_list]
#     with open(output_file, "w") as f:
#         subprocess.run(command, stdout=f)


@app.route('/download_db', methods=["GET"])
def download_db():
    from_date = request.args.get("from_date", "2000-01-01 00:00:00")
    to_date = request.args.get("to_date", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    streamers = get_streamers(from_date, to_date)
    csv_filename = f"streamers_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    csv_path = os.path.join("temp", csv_filename)
    os.makedirs("temp", exist_ok=True)
    
    with open(csv_path, "w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["Timestamp", "Streamer", "Viewers", "Language", "Game", "Tags", "First Message Sent", "First Message Timestamp", 
                         "First Reply", "First Reply Timestamp", "Second Message Sent", "Second Reply", 
                         "Second Reply Timestamp", "Socials", "Abandoned"])
        writer.writerows(streamers)
    
    return send_file(csv_path, as_attachment=True)

@app.route('/download/<filename>')
def download_csv(filename):
    file_path = os.path.join(DB_FOLDER, filename)
    return send_file(file_path, as_attachment=True)


# @app.route('/update_prompts', methods=["GET", "POST"])
# def update_prompts():
#     if request.method == "POST":
#         new_messages = request.form.get("new_messages", "")
        
#         # Update messages in the config
#         config["Bot"]["messages"] = new_messages
#         with open(CONFIG_FILE, "w") as configfile:
#             config.write(configfile)
        
#         return jsonify({"message": "Bot prompts updated successfully!"})

#     saved_messages = config.get("Bot", "messages", fallback="Hello|Welcome")
#     return render_template("update_prompts.html", saved_messages=saved_messages)

bot_process = None  # Global variable to track the bot's process
@app.route('/update_prompts', methods=["GET", "POST"])
def update_prompts():
    config = load_config()

    if request.method == "POST":
        data = request.get_json()

        # These come in from your JS as arrays and a number
        message_interval   = data.get("message_interval", 1200)
        initial_messages   = data.get("initial_messages", [])
        second_messages    = data.get("second_messages", [])

        # Save back as pipe-separated strings and integer
        config["Bot"]["message_interval"]  = str(message_interval)
        config["Bot"]["initial_messages"]  = "|".join(initial_messages)
        config["Bot"]["second_messages"]   = "|".join(second_messages)

        with open(CONFIG_FILE, "w") as configfile:
            config.write(configfile)

        return jsonify({"message": "Bot prompts and interval updated successfully!"})

    # GET: load existing values from CONFIG2.ini
    saved_interval = int(config.get("Bot", "message_interval", fallback="1200"))
    # Split the two prompt lists back into Python lists
    initials = config.get("Bot", "initial_messages", fallback="Hello|Welcome").split("|")
    seconds  = config.get("Bot", "second_messages",  fallback="").split("|")

    # Pair them up for the template
    prompt_pairs = list(zip(initials, seconds))
    if not prompt_pairs:
        prompt_pairs = [("", "")]

    return render_template(
        "update_prompts.html",
        message_interval=      saved_interval,  # in seconds
        prompt_pairs=          prompt_pairs
    )

# @app.route('/run_bot', methods=["POST"])
# def run_bot():
#     global bot_process
#     if bot_process is not None and bot_process.poll() is None:
#         return jsonify({"message": "Bot is already running."})
#     # Start a new process running twitch_bot_sender.py
#     bot_process = subprocess.Popen(["python", "twitch_bot_sender.py"])
#     return jsonify({"message": "Twitch bot started!"})


@app.route('/run_bot', methods=["POST"])
def run_bot():
    global bot_process

    # 1) refresh token from disk
    new_token = refresh_access_token()
    if not new_token:
        return jsonify({
            "error":"Could not refresh token; please re-authenticate once."
        }), 500

    # 2) only now start the bot
    if bot_process is not None and bot_process.poll() is None:
        return jsonify({"message": "Bot is already running."})
    bot_process = subprocess.Popen(["python", "twitch_bot_sender.py"])
    return jsonify({"message": "Twitch bot started!"})


@app.route('/stop_bot', methods=["POST"])
def stop_bot():
    global bot_process
    if bot_process is not None and bot_process.poll() is None:
        bot_process.terminate()
        bot_process = None
        return jsonify({"message": "Twitch bot has been stopped."})
    else:
        return jsonify({"message": "Bot is not running."})

@app.route('/bot_logs')
def bot_logs():
    existing_files = sorted(os.listdir(DB_FOLDER), reverse=True)
    # determine if the user has already completed OAuth (we're storing the refresh token in session)
    parser = configparser.ConfigParser()
    parser.read("CONFIG2.ini")  # “”
    refresh = parser.get("Twitch", "refresh_token", fallback="").strip()
    has_token = bool(refresh)

    return render_template(
        "bot_logs.html",
        files=existing_files,
        has_token=has_token
    )

# @app.route('/start_bot', methods=["POST"])
# def start_bot():
#     thread = threading.Thread(target=run_bot_script)
#     thread.start()
#     return jsonify({"message": "Bot started..."})

# def run_bot_script():
#     subprocess.run(["python", "twitch_bot_sender.py"])


# if __name__ == '__main__':
#     app.run(host="0.0.0.0", port=5000, debug=True)

# @app.route('/run_bot', methods=["POST"])
# def run_bot():
#     """Runs the Twitch bot after fetching is complete."""
#     subprocess.Popen(["python", "twitch_bot_sender.py"])
#     return jsonify({"message": "Twitch bot started!"})

if __name__ == '__main__':
    import os
    port = int(os.environ.get("PORT", 5000))  # Use Railway's PORT or default to 5000
    socketio.run(app, debug=True, host="0.0.0.0", port=port)

    # socketio.run(app, debug=True, use_reloader=False, host="0.0.0.0", port=5000)

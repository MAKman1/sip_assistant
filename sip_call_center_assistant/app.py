import asyncio
import threading
import os
import signal
import base64
import json
import audioop # For audio format conversion
import traceback # Added for better error logging in WebSocket
import sys # Added for version check
import queue # For thread-safe queue between Flask and Asyncio threads
import time # For sleep

# Conditional import for ExceptionGroup backport
if sys.version_info < (3, 11, 0):
    try:
        # Import the backport library
        import exceptiongroup
        # Define ExceptionGroup in the global scope for the except block
        ExceptionGroup = exceptiongroup.ExceptionGroup
    except ImportError:
        print("Warning: 'exceptiongroup' backport not installed. TaskGroup exception handling will fail on Python < 3.11.")
        # Define a dummy ExceptionGroup if the backport isn't installed to avoid NameError
        # This allows the script to load but will fail at runtime if an ExceptionGroup is raised.
        class ExceptionGroup(Exception): pass
else:
    # For Python 3.11+, ExceptionGroup is built-in
    pass # No need to do anything, ExceptionGroup is already available


from flask import Flask, request, jsonify
from flask_sock import Sock # Keep for Twilio Media Stream
from flask_socketio import SocketIO # Added for Frontend communication
from flask_cors import CORS # Added for Frontend communication
from dotenv import load_dotenv
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream # For TwiML generation
from twilio.rest import Client as TwilioRestClient # Added for outbound calls
# flask-sock uses different underlying libraries, need robust exception handling
# from websockets.exceptions import ConnectionClosedOK # May not be the right one
# Instead, catch generic Exception or specific ones from the underlying ws library if known (e.g., geventwebsocket.exceptions.WebSocketError)

from gemini_assistant import AudioLoop # Keep this import
# Attempt to import potential underlying websocket exceptions for flask-sock
try:
    from geventwebsocket.exceptions import WebSocketError
except ImportError:
    WebSocketError = ConnectionError # Fallback to a general connection error

try:
    # Simple-websocket might be used too
    from simple_websocket import ConnectionClosed
except ImportError:
    ConnectionClosed = ConnectionError # Fallback

# Define a tuple of likely connection closed exceptions
CONNECTION_CLOSED_EXCEPTIONS = (WebSocketError, ConnectionClosed, ConnectionError)

# Load environment variables from .env file
load_dotenv()

# Check for environment variables AFTER loading .env
required_env_vars = ['GOOGLE_API_KEY', 'TWILIO_ACCOUNT_SID', 'TWILIO_AUTH_TOKEN', 'TWILIO_PHONE_NUMBER', 'PUBLIC_APP_HOST']
missing_vars = [var for var in required_env_vars if not os.getenv(var)]
if missing_vars:
    raise ValueError(f"Missing environment variables: {', '.join(missing_vars)}. Make sure they are in your .env file or environment.")

# --- Twilio REST Client ---
twilio_client = TwilioRestClient(os.getenv('TWILIO_ACCOUNT_SID'), os.getenv('TWILIO_AUTH_TOKEN'))
twilio_phone_number = os.getenv('TWILIO_PHONE_NUMBER') # Your Twilio phone number to call from

# --- Constants for Audio Conversion ---
TWILIO_SAMPLE_RATE = 8000
GEMINI_INPUT_SAMPLE_RATE = 16000
GEMINI_OUTPUT_SAMPLE_RATE = 24000
AUDIO_WIDTH = 2 # Bytes per sample for PCM16

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "http://localhost:5173"}}) # Allow CORS for frontend dev server
sock = Sock(app) # Initialize flask-sock for Twilio Media Stream
socketio = SocketIO(app, async_mode='threading', cors_allowed_origins="http://localhost:5173") # Initialize SocketIO for Frontend

# Global state (consider managing per-connection state differently for WebSockets)
# assistant_instance: AudioLoop | None = None
# assistant_thread: threading.Thread | None = None
# assistant_lock = threading.Lock() # Lock might still be useful if accessing shared resources

# --- Audio Conversion Helpers ---

def decode_twilio_audio(payload: str) -> bytes:
    """Decodes base64 encoded µ-law audio from Twilio Media Stream."""
    return base64.b64decode(payload)

def pcm_to_ulaw(pcm_data: bytes) -> bytes:
    """Converts linear PCM16 audio data to µ-law."""
    return audioop.lin2ulaw(pcm_data, AUDIO_WIDTH)

def ulaw_to_pcm(ulaw_data: bytes) -> bytes:
    """Converts µ-law audio data to linear PCM16."""
    return audioop.ulaw2lin(ulaw_data, AUDIO_WIDTH)

def change_rate(pcm_data: bytes, current_rate: int, new_rate: int) -> bytes:
    """Changes the sample rate of PCM16 audio data."""
    # audioop.ratecv returns (new_data, state). We only need new_data.
    new_data, _ = audioop.ratecv(pcm_data, AUDIO_WIDTH, 1, current_rate, new_rate, None)
    return new_data

# --- Twilio Webhook Endpoint ---

@app.route('/incoming_call', methods=['POST'])
def handle_incoming_call():
    """Handles incoming calls from Twilio and responds with TwiML to start the stream."""
    print("Incoming call received")
    call_sid = request.form.get('CallSid')
    from_number = request.form.get('From')
    print(f"Call SID: {call_sid}, From: {from_number}")

    # --- Emit event to frontend ---
    # TODO: Add more call details if needed
    socketio.emit('new_call', {'call_sid': call_sid, 'from_number': from_number})
    # ---

    response = VoiceResponse()
    connect = Connect()

    # Construct WebSocket URL from environment variable
    public_host = os.getenv('PUBLIC_APP_HOST')
    if not public_host:
        # This should ideally be caught by the check above, but double-check
        print("FATAL ERROR: PUBLIC_APP_HOST environment variable not set.")
        # Return an error TwiML or handle appropriately
        response = VoiceResponse()
        response.say("System configuration error. Please contact support.")
        response.hangup()
        return str(response), 500, {'Content-Type': 'text/xml'}

    websocket_url = f"wss://{public_host}/sip_stream"
    print(f"Connecting to WebSocket: {websocket_url}")

    connect.stream(url=websocket_url)
    response.append(connect)

    # Optional: Add a <Say> verb for debugging or initial greeting before stream connects
    response.say("Connecting you to the assistant.")

    return str(response), 200, {'Content-Type': 'text/xml'}

# --- Fallback Handler ---
@app.route('/incoming_call_fallback', methods=['POST'])
def handle_incoming_call_fallback():
    """Handles failures from the primary incoming call webhook."""
    error_code = request.form.get('ErrorCode')
    error_url = request.form.get('ErrorUrl')
    print(f"!!! Primary incoming call handler failed! ErrorCode: {error_code}, ErrorUrl: {error_url}")
    # Log request details for debugging
    print(f"Fallback Request Form Data: {request.form}")

    response = VoiceResponse()
    response.say("Apologies, we encountered a system error connecting your call. Please try again later.")
    response.hangup()
    return str(response), 200, {'Content-Type': 'text/xml'}

# --- Call Status Handler ---
@app.route('/call_status', methods=['POST'])
def handle_call_status():
    """Handles call status updates from Twilio."""
    call_sid = request.form.get('CallSid')
    call_status = request.form.get('CallStatus')
    timestamp = request.form.get('Timestamp')
    print(f"Call Status Update - SID: {call_sid}, Status: {call_status}, Timestamp: {timestamp}")

    # Emit status update to frontend
    socketio.emit('call_status_update', {'call_sid': call_sid, 'status': call_status})

    # Respond to Twilio with 200 OK
    return '', 200


# --- WebSocket Endpoint for Twilio Media Stream ---

@sock.route('/sip_stream')
def sip_stream(ws):
    """Handles the bidirectional audio stream with Twilio via WebSocket."""
    print("WebSocket connection established")
    assistant_instance = None
    audio_tasks = []
    # Create queues for this specific connection
    audio_in_queue = asyncio.Queue()      # Audio FROM Gemini TO Async Sender Task
    out_queue = asyncio.Queue(maxsize=20)   # Audio FROM Sync Receiver TO Gemini
    transcript_queue = asyncio.Queue()    # Transcript text FROM Gemini TO Async Transcript Sender
    twilio_send_queue = queue.Queue()     # Messages FROM Async Sender Task TO Sync Sender (Flask Thread)
    connection_active = threading.Event() # Use threading.Event for cross-thread signaling
    connection_active.set()               # Initially assume connection is active

    # --- Async Tasks (Managed by Background Thread's Event Loop) ---

    async def run_async_tasks(loop_instance: AudioLoop, twilio_send_q: queue.Queue, conn_active_flag: threading.Event):
        """Runs the Gemini audio sender and main assistant loop tasks."""
        send_task = None
        run_task = None
        tasks = set()
        try:
            async with asyncio.TaskGroup() as tg:
                print("Starting assistant async TaskGroup...")
                # Task to get audio from Gemini and put message onto the thread-safe queue
                send_task = tg.create_task(send_gemini_audio_to_sync_queue(loop_instance, twilio_send_q, conn_active_flag), name="gemini_to_sync_queue")
                tasks.add(send_task)
                # Task to run the core Gemini processing loop
                run_task = tg.create_task(loop_instance.run(), name="gemini_run_loop")
                tasks.add(run_task)
                # Transcript sender task will be started by the main thread via run_coroutine_threadsafe
                print("Assistant async background tasks created.")
            print("Assistant async TaskGroup finished.")

        except ExceptionGroup as eg: # Use ExceptionGroup for backport compatibility
            print(f"--- ERROR in run_async_tasks TaskGroup ---")
            for i, exc in enumerate(eg.exceptions):
                print(f"  Async TaskGroup Exception {i+1}/{len(eg.exceptions)}: {type(exc).__name__}: {exc}")
                if not isinstance(exc, (asyncio.CancelledError)): # Don't trace expected cancellations
                     traceback.print_exception(type(exc), exc, exc.__traceback__)
            print(f"--- END ERROR in run_async_tasks TaskGroup ---")
            conn_active_flag.clear() # Signal main thread to stop
        except asyncio.CancelledError:
             print("run_async_tasks was cancelled.")
             conn_active_flag.clear()
        except Exception as e:
             print(f"Unexpected error in run_async_tasks: {type(e).__name__}: {e}")
             traceback.print_exc()
             conn_active_flag.clear()
        finally:
            print("Cleaning up async tasks...")
            conn_active_flag.clear() # Ensure flag is cleared
            print("Async task cleanup finished.")


    async def send_gemini_audio_to_sync_queue(loop_instance: AudioLoop, twilio_send_q: queue.Queue, conn_active_flag: threading.Event):
        """Gets audio from Gemini's output queue, converts it, and puts JSON message onto thread-safe queue."""
        while conn_active_flag.is_set(): # Check flag managed by main thread
            try:
                # Check flag before waiting
                if not conn_active_flag.is_set():
                    print("Async Sender: Connection inactive flag set, exiting.")
                    break

                # Get audio data from Gemini's output queue
                try:
                    gemini_pcm24_chunk = await asyncio.wait_for(loop_instance.audio_in_queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    continue # No audio, loop and check flag again

                # Check flag again after waiting
                if not conn_active_flag.is_set():
                    print("Async Sender: Connection inactive flag set after wait, exiting.")
                    break

                # 1. Convert 24kHz PCM16 -> 8kHz PCM16
                gemini_pcm8_chunk = change_rate(gemini_pcm24_chunk, GEMINI_OUTPUT_SAMPLE_RATE, TWILIO_SAMPLE_RATE)

                # 2. Convert 8kHz PCM16 -> 8kHz µ-law
                twilio_ulaw_chunk = pcm_to_ulaw(gemini_pcm8_chunk)

                # 3. Encode µ-law to base64 and format for Twilio Media Stream
                twilio_payload = base64.b64encode(twilio_ulaw_chunk).decode('utf-8')
                media_message = {
                    "event": "media",
                    "streamSid": "placeholder_stream_sid", # TODO: Get real streamSid if possible
                    "media": {
                        "payload": twilio_payload
                    }
                }

                # 4. Put the JSON string onto the thread-safe queue for the main thread to send
                try:
                    twilio_send_q.put(json.dumps(media_message))
                    loop_instance.audio_in_queue.task_done()
                except queue.Full:
                    print("Warning: Twilio send queue is full. Discarding audio chunk.")
                    loop_instance.audio_in_queue.task_done() # Mark as done even if discarded

            except asyncio.CancelledError:
                print("Async Sender: Task cancelled.")
                break # Exit loop
            except Exception as e:
                print(f"Async Sender: Unexpected error: {type(e).__name__}: {e}")
                traceback.print_exc()
                conn_active_flag.clear() # Signal main thread to stop on error
                break # Exit loop
        print("Async Gemini audio sender task finished.")


    # --- Transcript Sender Task (Runs in Asyncio Loop) ---
    async def send_transcript_to_frontend(sid, transcript_q: asyncio.Queue, active_flag: threading.Event):
        """Reads from transcript queue and emits SocketIO events."""
        print(f"Starting transcript sender for stream SID: {sid}")
        while active_flag.is_set():
            try:
                text_chunk = await asyncio.wait_for(transcript_q.get(), timeout=0.5)
                if text_chunk is None: # Sentinel value to stop
                    break
                # Use socketio.emit for thread safety
                socketio.emit('call_transcript_update', {'stream_sid': sid, 'transcript_chunk': text_chunk})
                transcript_q.task_done()
            except asyncio.TimeoutError:
                continue # Just check active_flag again
            except asyncio.CancelledError:
                print(f"Transcript sender {sid}: Task cancelled.")
                break
            except Exception as e:
                print(f"Transcript sender {sid}: Error: {e}")
                traceback.print_exc()
                break # Exit on error
        print(f"Transcript sender stopped for stream SID: {sid}")


    # --- Background Thread Runner ---
    def loop_runner(loop: asyncio.AbstractEventLoop, instance: AudioLoop, twilio_q: queue.Queue, conn_flag: threading.Event):
        """Target function for the background thread managing the asyncio loop."""
        asyncio.set_event_loop(loop)
        try:
            print(f"Starting assistant event loop {loop} in thread {threading.current_thread().name}")
            # Run the async task manager
            loop.run_until_complete(run_async_tasks(instance, twilio_q, conn_flag))
        except Exception as e:
            print(f"Error running async tasks in thread {threading.current_thread().name}: {e}")
            traceback.print_exc()
        finally:
            print(f"Closing assistant event loop {loop} in thread {threading.current_thread().name}")
            conn_flag.clear() # Ensure flag is cleared before closing loop
            try:
                if not loop.is_running() and not loop.is_closed():
                    loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception as shutdown_err:
                print(f"Error during final shutdown_asyncgens in thread: {shutdown_err}")
            if not loop.is_closed():
                loop.close()
            print(f"Assistant event loop {loop} closed.")


    # --- Main WebSocket Connection Handling (Flask Thread) ---
    assistant_loop = None
    runner_thread = None
    transcript_sender_future = None # To manage the transcript sender task future

    try:
        # 1. Instantiate the Assistant
        print("Instantiating AudioLoop for WebSocket connection.")
        assistant_instance = AudioLoop(audio_in_queue=audio_in_queue, out_queue=out_queue, transcript_queue=transcript_queue)

        # 2. Create and start the background asyncio thread
        assistant_loop = asyncio.new_event_loop()
        runner_thread = threading.Thread(
            target=loop_runner,
            args=(assistant_loop, assistant_instance, twilio_send_queue, connection_active),
            daemon=True,
            name="AsyncioRunner"
        )
        runner_thread.start()
        print("Background asyncio thread started.")

        # Give the loop a moment to start
        time.sleep(0.1)

        # 3. Main loop in Flask thread for WebSocket I/O
        stream_sid = None # Variable to store the actual stream SID
        while connection_active.is_set():
            try:
                # --- Send outgoing messages ---
                try:
                    # Non-blocking check for messages from the async sender task
                    outgoing_message_json = twilio_send_queue.get_nowait()
                    print(f"Sync Sender: Retrieved message from queue. Attempting send...") # Log retrieval

                    # --- Inject correct streamSid ---
                    if stream_sid:
                        try:
                            # Parse, update, and re-serialize the message
                            message_dict = json.loads(outgoing_message_json)
                            if message_dict.get("event") == "media":
                                message_dict["streamSid"] = stream_sid
                                outgoing_message_to_send = json.dumps(message_dict)
                                ws.send(outgoing_message_to_send) # Send synchronously
                                print(f"Sync Sender: ws.send executed for message with SID {stream_sid}.") # Log send attempt
                            else:
                                # Should not happen if only media messages are queued, but handle defensively
                                print(f"Sync Sender: Warning - Non-media message in queue: {outgoing_message_json}")
                                ws.send(outgoing_message_json) # Send original if not media
                        except json.JSONDecodeError:
                             print(f"Sync Sender: Error decoding JSON from queue: {outgoing_message_json}")
                             # Decide whether to send original or discard
                             # ws.send(outgoing_message_json) # Option: Send original anyway
                        except Exception as send_err:
                             print(f"Sync Sender: Error during ws.send: {send_err}")
                             connection_active.clear() # Stop on send error
                             break
                    else:
                        # stream_sid not received yet, should not be sending media
                        print(f"Sync Sender: Warning - Attempting to send message before stream_sid received: {outgoing_message_json}")
                        # Optionally discard or queue for later? Discarding for now.

                    twilio_send_queue.task_done()
                except queue.Empty:
                    pass # No message to send right now
                except CONNECTION_CLOSED_EXCEPTIONS as e:
                     print(f"Sync Sender: Connection closed during send: {type(e).__name__}")
                     connection_active.clear() # Signal closure
                     break
                except Exception as e:
                    print(f"Sync Sender: Error sending message: {e}")
                    traceback.print_exc()
                    connection_active.clear() # Signal closure on error
                    break

                # --- Receive incoming messages ---
                # Use a timeout to avoid blocking forever, allowing send queue check
                message_json = ws.receive(timeout=0.05) # Short timeout

                if message_json is None:
                    # Check connection status again if receive timed out or returned None ambiguously
                    if not connection_active.is_set(): # Check if flag was cleared by background thread
                         print("Sync Receiver: Connection flag cleared, exiting.")
                         break
                    continue # Timeout, loop again to check send queue and receive again

                # --- Process incoming messages ---
                message = json.loads(message_json)
                event = message.get("event")

                if event == "connected":
                    print("Sync Receiver: Twilio Media Stream Connected")
                elif event == "start":
                    stream_sid = message.get("streamSid")
                    print(f"Sync Receiver: Twilio Media Stream Started (SID: {stream_sid})")
                    
                    # --- START: Add initial text message sending ---
                    initial_text_message = "Hey there" # Define the message
                    if assistant_instance and assistant_loop and assistant_loop.is_running():
                        print(f"Sync Receiver: Scheduling initial text message: '{initial_text_message}'")
                        # Use run_coroutine_threadsafe to put the message onto the async queue
                        asyncio.run_coroutine_threadsafe(
                            assistant_instance.text_in_queue.put(initial_text_message),
                            assistant_loop
                        )
                    else:
                         print("Sync Receiver: Warning - Cannot send initial text, assistant/loop not ready.")
                    # --- END: Add initial text message sending ---

                    
                    # Start the transcript sender task in the background asyncio loop
                    if stream_sid and not transcript_sender_future and assistant_loop.is_running():
                        print(f"Sync Receiver: Scheduling transcript sender for SID: {stream_sid}")
                        transcript_sender_future = asyncio.run_coroutine_threadsafe(
                            send_transcript_to_frontend(stream_sid, transcript_queue, connection_active),
                            assistant_loop
                        )
                    # TODO: Update streamSid in the media message template? Difficult across threads.
                elif event == "media":
                    payload = message.get("media", {}).get("payload")
                    if payload and assistant_loop.is_running():
                        ulaw_data = decode_twilio_audio(payload)
                        pcm8_data = ulaw_to_pcm(ulaw_data)
                        pcm16_data = change_rate(pcm8_data, TWILIO_SAMPLE_RATE, GEMINI_INPUT_SAMPLE_RATE)
                        # Put data onto the async queue for Gemini
                        asyncio.run_coroutine_threadsafe(
                            assistant_instance.out_queue.put({"data": pcm16_data, "mime_type": "audio/pcm"}),
                            assistant_loop
                        )
                elif event == "stop":
                    print("Sync Receiver: Twilio Media Stream Stopped")
                    connection_active.clear() # Signal closure
                    break
                elif event == "error":
                    print(f"Sync Receiver: Twilio Media Stream Error: {message.get('message')}")
                    connection_active.clear() # Signal closure
                    break
                else:
                    print(f"Sync Receiver: Received unknown WebSocket event: {event}")

            except TimeoutError: # Catch specific timeout exception from ws.receive
                 continue # Loop again after timeout
            except CONNECTION_CLOSED_EXCEPTIONS as e:
                 print(f"Sync Receiver: Connection closed during receive: {type(e).__name__}")
                 connection_active.clear() # Signal closure
                 break
            except Exception as e:
                 print(f"Sync Receiver: Error processing WebSocket message: {e}")
                 traceback.print_exc()
                 connection_active.clear() # Signal closure on error
                 break

        print("Sync WebSocket I/O loop finished.")

    except Exception as e:
        print(f"Error during WebSocket handling setup: {e}")
        traceback.print_exc()
        connection_active.clear() # Ensure flag is cleared on setup error
    finally:
        print("WebSocket connection handler finishing. Cleaning up...")
        connection_active.clear() # Explicitly clear flag

        # Signal the assistant instance to stop (schedules stop in asyncio loop)
        if assistant_instance and assistant_loop and assistant_loop.is_running():
            print("Scheduling assistant stop...")
            stop_future = asyncio.run_coroutine_threadsafe(assistant_instance.stop(), assistant_loop)
            try:
                stop_future.result(timeout=1.0) # Wait briefly for stop signal
            except Exception as stop_err:
                print(f"Error waiting for assistant stop future: {stop_err}")

        # Signal transcript sender to stop (if running) by putting None
        if transcript_queue:
             # Use run_coroutine_threadsafe as queue is async, loop might still be running briefly
             if assistant_loop and assistant_loop.is_running():
                  asyncio.run_coroutine_threadsafe(transcript_queue.put(None), assistant_loop)
             else: # If loop already stopped, just clear queue? Less clean.
                  pass

        # Wait for the background thread to finish its cleanup (including loop closing)
        if runner_thread and runner_thread.is_alive():
             print("Waiting for background asyncio thread to join...")
             runner_thread.join(timeout=5)
             if runner_thread.is_alive():
                 print("Warning: Background asyncio thread did not join.")
             else:
                 print("Background asyncio thread joined successfully.")

        print("WebSocket cleanup complete.")


# --- Old Endpoints (Commented Out) ---
# def run_assistant_loop(loop_instance: AudioLoop):
#     """Creates and runs the asyncio event loop for the assistant in its own thread."""
#     loop = asyncio.new_event_loop()
#     asyncio.set_event_loop(loop)
#     try:
#         print(f"Starting assistant event loop {loop} in thread {threading.current_thread().name}")
#         loop.run_until_complete(loop_instance.run())
#     except Exception as e:
#         print(f"Error running assistant loop in thread {threading.current_thread().name}: {e}")
#     finally:
#         print(f"Closing assistant event loop {loop} in thread {threading.current_thread().name}")
#         loop.run_until_complete(loop.shutdown_asyncgens())
#         loop.close()

# @app.route('/start', methods=['POST'])
# def start_assistant():
#     """
#     Starts the Gemini assistant (Audio Only) in a background thread. (DEPRECATED for SIP)
#     """
#     # ... (original code) ...
#     return jsonify({"error": "This endpoint is deprecated for SIP integration."}), 410

# @app.route('/send_message', methods=['POST'])
# def send_message():
#     """
#     Sends a text message to the running assistant. (DEPRECATED for SIP)
#     Accepts 'message' in JSON body.
#     """
#     # ... (original code) ...
#     return jsonify({"error": "This endpoint is deprecated for SIP integration."}), 410


# @app.route('/stop', methods=['POST'])
# def stop_assistant():
#     """Stops the running assistant. (DEPRECATED for SIP)"""
#     # ... (original code) ...
#     return jsonify({"error": "This endpoint is deprecated for SIP integration."}), 410


# Optional: Add a simple root route
@app.route('/')
def index():
    # Updated message
    return "Gemini SIP Call Assistant Backend is running."

# --- SocketIO Event Handlers for Frontend ---

@socketio.on('connect')
def handle_connect():
    print('Frontend client connected:', request.sid)

@socketio.on('disconnect')
def handle_disconnect():
    print('Frontend client disconnected:', request.sid)

# --- Handler for Frontend to Initiate Call ---
@socketio.on('initiate_call')
def handle_initiate_call(data):
    """Handles request from frontend to initiate an outbound call."""
    to_number = data.get('to_number')
    if not to_number:
        print("Error: No 'to_number' provided in initiate_call event.")
        socketio.emit('call_initiation_error', {'error': 'No phone number provided'}, room=request.sid)
        return

    print(f"--- [handle_initiate_call] Received request from SID {request.sid} to call {to_number}")

    try:
        print(f"--- [handle_initiate_call] Using Twilio Number: {twilio_phone_number}")
        # Construct the TwiML webhook URL dynamically using the same domain
        # Assumes Flask app is running on HTTP, not HTTPS locally
        # Construct webhook URLs using the environment variable
        public_host = os.getenv('PUBLIC_APP_HOST')
        if not public_host:
             print("--- [handle_initiate_call] FATAL ERROR: PUBLIC_APP_HOST not set for outbound call.")
             socketio.emit('call_initiation_error', {'error': 'Server configuration error (Missing PUBLIC_APP_HOST)'}, room=request.sid)
             return

        # IMPORTANT: Twilio needs an HTTP/HTTPS URL for the call webhook, not WSS
        twiml_webhook_url = f"https://{public_host}/incoming_call" # Use HTTPS
        status_callback_url = f"https://{public_host}/call_status"

        print(f"--- [handle_initiate_call] Using TwiML URL: {twiml_webhook_url}")
        print(f"--- [handle_initiate_call] Using Status Callback URL: {status_callback_url}")
        print(f"--- [handle_initiate_call] Attempting Twilio API call...")

        call = twilio_client.calls.create(
                        to=to_number,
                        from_=twilio_phone_number,
                        url=twiml_webhook_url, # URL for TwiML instructions when call connects
                        status_callback=status_callback_url, # Optional: Reuse status callback
                        status_callback_event=['initiated', 'ringing', 'answered', 'completed'],
                        status_callback_method='POST'
                    )
        print(f"--- [handle_initiate_call] Twilio API call successful. Call SID: {call.sid}")
        # Notify the specific frontend client that initiated the call
        print(f"--- [handle_initiate_call] Emitting 'call_initiated' to SID {request.sid}")
        socketio.emit('call_initiated', {'call_sid': call.sid, 'to_number': to_number}, room=request.sid)
        print(f"--- [handle_initiate_call] 'call_initiated' emitted.")

    except Exception as e:
        print(f"--- [handle_initiate_call] Error during Twilio call initiation: {e}")
        traceback.print_exc()
        # Notify the specific frontend client about the error
        print(f"--- [handle_initiate_call] Emitting 'call_initiation_error' to SID {request.sid}")
        socketio.emit('call_initiation_error', {'error': str(e)}, room=request.sid)
        print(f"--- [handle_initiate_call] 'call_initiation_error' emitted.")


# Add more handlers here later for frontend interactions if needed

# --- Main App Runner ---

if __name__ == '__main__':
    print("Starting Flask-SocketIO app for SIP Assistant...")
    # Use socketio.run to handle both Flask routes and SocketIO events
    # allow_unsafe_werkzeug=True is needed for newer versions when using threading/async modes with Werkzeug dev server
    socketio.run(app, host='0.0.0.0', port=8081, debug=False, allow_unsafe_werkzeug=True)

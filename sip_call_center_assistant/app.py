import asyncio
import threading
import os
import signal
import base64
import json
import audioop # For audio format conversion
import traceback # Added for better error logging in WebSocket
import sys # Added for version check

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
from websockets.exceptions import ConnectionClosedOK # Correct exception for websockets library used by flask-sock

from gemini_assistant import AudioLoop # Keep this import

# Load environment variables from .env file
load_dotenv()

# Check for environment variables AFTER loading .env
required_env_vars = ['GOOGLE_API_KEY', 'TWILIO_ACCOUNT_SID', 'TWILIO_AUTH_TOKEN', 'TWILIO_PHONE_NUMBER']
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

    # IMPORTANT: Replace with your actual public WebSocket URL (e.g., from ngrok)
    # TODO: Make this dynamically configurable instead of hardcoding
    ngrok_domain = "3d1e-2a01-4b00-c014-9a00-d0ea-801a-aa66-f7f.ngrok-free.app" # Extract domain
    websocket_url = f"wss://{ngrok_domain}/sip_stream"
    print(f"Connecting to WebSocket: {websocket_url}")

    connect.stream(url=websocket_url)
    response.append(connect)

    # Optional: Add a <Say> verb for debugging or initial greeting before stream connects
    # response.say("Connecting you to the assistant.")

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
    audio_in_queue = asyncio.Queue() # Audio FROM Gemini TO Twilio
    out_queue = asyncio.Queue(maxsize=20) # Audio FROM Twilio TO Gemini
    transcript_queue = asyncio.Queue() # Transcript text FROM Gemini TO Frontend

    async def run_assistant_tasks(loop_instance: AudioLoop):
        """Runs the necessary assistant tasks in the background."""
        try:
            async with asyncio.TaskGroup() as tg:
                # Task to send audio from Gemini to Twilio
                audio_tasks.append(tg.create_task(send_gemini_audio_to_twilio(loop_instance, ws), name="gemini_to_twilio"))
                # Task to run the core Gemini processing loop (Corrected: only one instance)
                audio_tasks.append(tg.create_task(loop_instance.run(), name="gemini_run_loop"))
                print("Assistant background tasks started.")
                # No need to wait here, run() will block until stopped
        except ExceptionGroup as eg: # Use ExceptionGroup for backport compatibility
            print(f"--- ERROR in run_assistant_tasks TaskGroup ---")
            # Print full traceback for detailed debugging
            for i, exc in enumerate(eg.exceptions):
                 print(f"  TaskGroup Exception {i+1}/{len(eg.exceptions)}: {type(exc).__name__}: {exc}")
                 traceback.print_exception(type(exc), exc, exc.__traceback__)
            print(f"--- END ERROR in run_assistant_tasks TaskGroup ---")
            # Re-raise the exception group if necessary, or handle cleanup
            # raise # Optional: re-raise if Flask/SocketIO should handle it further

    async def send_gemini_audio_to_twilio(loop_instance: AudioLoop, websocket):
        """Gets audio from Gemini's output queue, converts it, and sends to Twilio."""
        while True:
            try:
                # Get audio data from Gemini's output queue (likely 24kHz PCM16)
                gemini_pcm24_chunk = await asyncio.wait_for(loop_instance.audio_in_queue.get(), timeout=0.1)

                # 1. Convert 24kHz PCM16 -> 8kHz PCM16
                gemini_pcm8_chunk = change_rate(gemini_pcm24_chunk, GEMINI_OUTPUT_SAMPLE_RATE, TWILIO_SAMPLE_RATE)

                # 2. Convert 8kHz PCM16 -> 8kHz µ-law
                twilio_ulaw_chunk = pcm_to_ulaw(gemini_pcm8_chunk)

                # 3. Encode µ-law to base64 and format for Twilio Media Stream
                twilio_payload = base64.b64encode(twilio_ulaw_chunk).decode('utf-8')
                media_message = {
                    "event": "media",
                    "streamSid": "placeholder_stream_sid", # Ideally get this from the 'start' event
                    "media": {
                        "payload": twilio_payload
                    }
                }
                await websocket.send(json.dumps(media_message))
                loop_instance.audio_in_queue.task_done()

            except asyncio.TimeoutError:
                # No audio from Gemini, just continue waiting.
                # If the websocket is actually closed, the next send() will fail.
                continue
            except ConnectionClosedOK:
                print("WebSocket closed by Twilio while sending audio. Stopping sender.")
                break
            except asyncio.CancelledError:
                print("Send Gemini audio task cancelled.")
                break
            except Exception as e:
                print(f"Unexpected error sending Gemini audio to Twilio: {type(e).__name__}: {e}")
                traceback.print_exc()
                break # Exit on other errors

    # REMOVE async from definition
    def receive_twilio_audio(loop_instance: AudioLoop, websocket, current_transcript_queue: asyncio.Queue, target_loop: asyncio.AbstractEventLoop):
        """Receives audio from Twilio via WebSocket, converts it, and sends to Gemini."""
        stream_sid = None # Store streamSid when received
        transcript_sender_task = None # Task for sending transcript to frontend

        # --- Task to send transcript updates to frontend ---
        async def send_transcript_to_frontend(sid):
            """Reads from transcript queue and emits SocketIO events."""
            print(f"Starting transcript sender for stream SID: {sid}")
            while True:
                try:
                    # Wait for a transcript chunk from Gemini (via AudioLoop)
                    text_chunk = await asyncio.wait_for(current_transcript_queue.get(), timeout=0.5)
                    if text_chunk is None: # Signal to stop
                        break
                    # Emit the transcript chunk to the frontend
                    # Use socketio.emit for thread safety from async context
                    socketio.emit('call_transcript_update', {'stream_sid': sid, 'transcript_chunk': text_chunk})
                    current_transcript_queue.task_done()
                except asyncio.TimeoutError:
                    # Check if the main WebSocket is still alive
                    if websocket.closed:
                        print("Twilio WebSocket closed, stopping transcript sender.")
                        break
                    continue # No transcript chunk, continue waiting
                except Exception as e:
                    print(f"Error in send_transcript_to_frontend: {e}")
                    traceback.print_exc()
                    break # Exit on error
            print(f"Transcript sender stopped for stream SID: {sid}")

        while True:
            try:
                message_json = websocket.receive() # Removed await here
                if message_json is None:
                    print("WebSocket receive returned None. Connection likely closed.")
                    break # Exit loop if connection closed

                message = json.loads(message_json)
                event = message.get("event")

                if event == "connected":
                    print("Twilio Media Stream Connected")
                elif event == "start":
                    stream_sid = message.get("streamSid")
                    print(f"Twilio Media Stream Started (SID: {stream_sid})")
                    # Start the transcript sender task now that we have the stream_sid
                    if stream_sid and not transcript_sender_task:
                         # Run the async sender in the assistant's event loop
                         if target_loop and target_loop.is_running(): # Use target_loop
                              transcript_sender_task = asyncio.run_coroutine_threadsafe(
                                   send_transcript_to_frontend(stream_sid),
                                   target_loop # Use target_loop
                              )
                              print(f"Transcript sender task created for SID: {stream_sid}")
                         else:
                              print("Warning: Assistant loop not running, cannot start transcript sender.")

                    # You might want to pass stream_sid to send_gemini_audio_to_twilio if needed
                elif event == "media":
                    payload = message.get("media", {}).get("payload")
                    if payload:
                        # 1. Decode base64 µ-law audio
                        ulaw_data = decode_twilio_audio(payload)

                        # 2. Convert 8kHz µ-law -> 8kHz PCM16
                        pcm8_data = ulaw_to_pcm(ulaw_data)

                        # 3. Convert 8kHz PCM16 -> 16kHz PCM16 (for Gemini input)
                        pcm16_data = change_rate(pcm8_data, TWILIO_SAMPLE_RATE, GEMINI_INPUT_SAMPLE_RATE)

                        # 4. Send to Gemini's input queue using run_coroutine_threadsafe
                        if loop_instance and loop_instance.out_queue and target_loop and target_loop.is_running():
                           # Use run_coroutine_threadsafe to put onto the async queue
                           future = asyncio.run_coroutine_threadsafe(
                               loop_instance.out_queue.put({"data": pcm16_data, "mime_type": "audio/pcm"}),
                               target_loop # Use target_loop
                           )
                           # Optional: Wait for future result with timeout if needed, or just fire-and-forget
                           # try:
                           #     future.result(timeout=1.0)
                           # except TimeoutError:
                           #     print("Warning: Timeout putting audio onto Gemini queue.")
                           # except Exception as put_err:
                           #     print(f"Error putting audio onto Gemini queue: {put_err}")
                        else:
                           print("Warning: Cannot put audio to Gemini queue (instance, queue, or loop unavailable).")

                # Correctly indented elif/else blocks start here (aligned with initial if/elif)
                elif event == "stop":
                    print("Twilio Media Stream Stopped")
                    break # Exit loop when stream stops
                elif event == "error":
                    print(f"Twilio Media Stream Error: {message.get('message')}")
                    break # Exit on error
                else:
                    print(f"Received unknown WebSocket event: {event}")

            # except ConnectionClosedOK: # flask-sock uses websockets library exception
            #      print("WebSocket connection closed normally.")
            #      break
            except Exception as e:
                # Check for specific websocket closure exceptions if needed
                if isinstance(e, ConnectionClosedOK):
                     print("WebSocket connection closed normally by Twilio.")
                else:
                    print(f"Error processing WebSocket message: {e}")
                    traceback.print_exc()
                break # Exit on other errors

        print("Twilio audio receiver loop finished.")
        # Signal transcript sender to stop if it's running
        if current_transcript_queue and target_loop and target_loop.is_running():
            # Use run_coroutine_threadsafe to put None onto the async queue
            asyncio.run_coroutine_threadsafe(current_transcript_queue.put(None), target_loop)

        if transcript_sender_task:
            try:
                # Wait briefly for the sender task future to finish
                # Note: transcript_sender_task is a Future returned by run_coroutine_threadsafe
                transcript_sender_task.result(timeout=2.0)
                print("Transcript sender task finished.")
            except TimeoutError: # Corrected exception name
                print("Warning: Transcript sender task did not finish in time.")
            except Exception as e:
                print(f"Error waiting for transcript sender task: {e}")


    # --- WebSocket Connection Lifecycle ---
    assistant_loop = None # Define assistant_loop here
    runner_thread = None # Define runner_thread here
    try:
        # 1. Instantiate the Assistant (passing the queues)
        print("Instantiating AudioLoop for WebSocket connection.")
        assistant_instance = AudioLoop(audio_in_queue=audio_in_queue, out_queue=out_queue, transcript_queue=transcript_queue)

        # 2. Start the background task runner in a separate thread
        assistant_loop = asyncio.new_event_loop()

        # Define the target function for the thread, including loop closing
        def loop_runner(loop: asyncio.AbstractEventLoop, instance: AudioLoop):
            asyncio.set_event_loop(loop)
            try:
                print(f"Starting assistant event loop {loop} in thread {threading.current_thread().name}")
                # Run the main tasks, assuming run_assistant_tasks handles its internal cleanup like shutdown_asyncgens
                loop.run_until_complete(run_assistant_tasks(instance)) # Pass instance here
            except Exception as e:
                print(f"Error running assistant tasks in thread {threading.current_thread().name}: {e}")
                traceback.print_exc() # Print traceback for errors in the thread
            finally:
                print(f"Closing assistant event loop {loop} in thread {threading.current_thread().name}")
                # Ensure async generators are shut down if run_assistant_tasks didn't or errored out early
                try:
                    # Check if loop is stopped but not closed before shutting down generators
                    if not loop.is_running() and not loop.is_closed():
                         # Run shutdown within the loop context
                         loop.run_until_complete(loop.shutdown_asyncgens())
                except Exception as shutdown_err:
                     print(f"Error during final shutdown_asyncgens in thread: {shutdown_err}")

                if not loop.is_closed():
                    loop.close()
                print(f"Assistant event loop {loop} closed.")

        # Pass the assistant_loop and assistant_instance to the target
        runner_thread = threading.Thread(target=loop_runner, args=(assistant_loop, assistant_instance), daemon=True)
        runner_thread.start()

        # Wait briefly for the loop to start? Might not be necessary but safer.
        import time
        time.sleep(0.1) # Keep this small delay

        # 3. Run the Twilio receiver loop directly (synchronously)
        #    Pass the assistant_loop for threadsafe queue operations
        if assistant_loop:
             receive_twilio_audio(assistant_instance, ws, transcript_queue, assistant_loop)
        else:
             print("Error: Assistant loop not created, cannot start receiver.")


    except Exception as e:
        print(f"Error during WebSocket handling: {e}")
        traceback.print_exc()
    finally:
        print("WebSocket connection closing. Cleaning up...")
        # Signal the assistant to stop
        if assistant_instance:
            print("Signalling assistant loop to stop...")
            # Need to schedule stop() in the assistant's event loop
            if assistant_loop and assistant_loop.is_running(): # Check if loop is running before scheduling
                future = asyncio.run_coroutine_threadsafe(assistant_instance.stop(), assistant_loop) # Pass loop
                try:
                    # Optionally wait briefly for the stop signal to be processed
                    future.result(timeout=1.0)
                except TimeoutError:
                    print("Warning: Timeout waiting for stop() signal future.")
                except Exception as stop_err:
                    print(f"Error scheduling/running stop(): {stop_err}")
            elif assistant_loop and not assistant_loop.is_running():
                 print("Warning: Assistant loop not running when trying to schedule stop(). Might have already stopped.")
            else:
                print("Warning: Assistant loop object not available, cannot schedule stop().")

        # Wait for the runner thread to finish (it should close the loop itself)
        if runner_thread and runner_thread.is_alive():
             print("Waiting for assistant runner thread to join...")
             runner_thread.join(timeout=5) # Give it time to clean up and close loop
             if runner_thread.is_alive():
                 print("Warning: Assistant runner thread did not join.")
             else:
                 print("Assistant runner thread joined successfully.")

        # REMOVED: assistant_loop.close() - The thread should handle this now.
        # Check if loop is closed after join, just for logging
        if assistant_loop and assistant_loop.is_closed():
            print("Confirmed assistant loop is closed after thread join.")
        elif assistant_loop:
            print("Warning: Assistant loop is NOT closed after thread join.")


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
        # TODO: Make ngrok domain dynamically configurable
        ngrok_domain = "3d1e-2a01-4b00-c014-9a00-d0ea-801a-aa66-f7f.ngrok-free.app" # Extract domain
        # IMPORTANT: Twilio needs an HTTP/HTTPS URL for the call webhook, not WSS
        twiml_webhook_url = f"https://{ngrok_domain}/incoming_call" # Use HTTPS for ngrok
        status_callback_url = f"https://{ngrok_domain}/call_status"

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

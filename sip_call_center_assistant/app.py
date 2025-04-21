import asyncio
import threading
import os
import signal
import base64
import json
import audioop # For audio format conversion

from flask import Flask, request, jsonify
from flask_sock import Sock # Import Sock
from dotenv import load_dotenv
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream # For TwiML generation

from gemini_assistant import AudioLoop # Keep this import

# Load environment variables from .env file
load_dotenv()

# Check for GOOGLE_API_KEY AFTER loading .env
if not os.getenv('GOOGLE_API_KEY'):
    raise ValueError("GOOGLE_API_KEY environment variable not set. Make sure it's in your .env file or environment.")

# --- Constants for Audio Conversion ---
TWILIO_SAMPLE_RATE = 8000
GEMINI_INPUT_SAMPLE_RATE = 16000
GEMINI_OUTPUT_SAMPLE_RATE = 24000
AUDIO_WIDTH = 2 # Bytes per sample for PCM16

app = Flask(__name__)
sock = Sock(app) # Initialize flask-sock

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
    response = VoiceResponse()
    connect = Connect()

    # IMPORTANT: Replace with your actual public WebSocket URL (e.g., from ngrok)
    websocket_url = "wss://YOUR_PUBLIC_NGROK_OR_SERVER_URL/sip_stream"
    print(f"Connecting to WebSocket: {websocket_url}")

    connect.stream(url=websocket_url)
    response.append(connect)

    # Optional: Add a <Say> verb for debugging or initial greeting before stream connects
    # response.say("Connecting you to the assistant.")

    return str(response), 200, {'Content-Type': 'text/xml'}

# --- WebSocket Endpoint for Twilio Media Stream ---

@sock.route('/sip_stream')
def sip_stream(ws):
    """Handles the bidirectional audio stream with Twilio via WebSocket."""
    print("WebSocket connection established")
    assistant_instance = None
    audio_tasks = []

    async def run_assistant_tasks(loop_instance: AudioLoop):
        """Runs the necessary assistant tasks in the background."""
        async with asyncio.TaskGroup() as tg:
            # Task to send audio from Gemini to Twilio
            audio_tasks.append(tg.create_task(send_gemini_audio_to_twilio(loop_instance, ws), name="gemini_to_twilio"))
            # Task to run the core Gemini processing loop
            audio_tasks.append(tg.create_task(loop_instance.run(), name="gemini_run_loop"))
            print("Assistant background tasks started.")
            # No need to wait here, run() will block until stopped

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
                # No audio from Gemini, check if WebSocket is still open
                if websocket.closed:
                    print("WebSocket closed while waiting for Gemini audio. Stopping sender.")
                    break
                continue
            except Exception as e:
                print(f"Error sending Gemini audio to Twilio: {e}")
                traceback.print_exc()
                break # Exit on error

    async def receive_twilio_audio(loop_instance: AudioLoop, websocket):
        """Receives audio from Twilio via WebSocket, converts it, and sends to Gemini."""
        stream_sid = None # Store streamSid when received
        while True:
            try:
                message_json = await websocket.receive()
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

                        # 4. Send to Gemini's input queue
                        if loop_instance and loop_instance.out_queue:
                           await loop_instance.out_queue.put({"data": pcm16_data, "mime_type": "audio/pcm"})

                elif event == "stop":
                    print("Twilio Media Stream Stopped")
                    break # Exit loop when stream stops
                elif event == "error":
                    print(f"Twilio Media Stream Error: {message.get('message')}")
                    break # Exit on error
                else:
                    print(f"Received unknown WebSocket event: {event}")

            except ConnectionClosedOK: # Correct exception for flask-sock
                 print("WebSocket connection closed normally.")
                 break
            except Exception as e:
                print(f"Error processing WebSocket message: {e}")
                traceback.print_exc()
                break # Exit on other errors

        print("Twilio audio receiver loop finished.")


    # --- WebSocket Connection Lifecycle ---
    try:
        # 1. Instantiate the Assistant
        print("Instantiating AudioLoop for WebSocket connection.")
        assistant_instance = AudioLoop() # Create a new instance for this connection

        # 2. Start the background task runner in a separate thread
        #    We need a new event loop for the assistant tasks
        assistant_loop = asyncio.new_event_loop()
        runner_thread = threading.Thread(target=lambda: assistant_loop.run_until_complete(run_assistant_tasks(assistant_instance)), daemon=True)
        runner_thread.start()

        # 3. Run the Twilio receiver loop in the current context (managed by flask-sock)
        asyncio.run(receive_twilio_audio(assistant_instance, ws))

    except Exception as e:
        print(f"Error during WebSocket handling: {e}")
        traceback.print_exc()
    finally:
        print("WebSocket connection closing. Cleaning up...")
        # Signal the assistant to stop
        if assistant_instance:
            print("Signalling assistant loop to stop...")
            # Need to schedule stop() in the assistant's event loop
            if assistant_loop and assistant_loop.is_running():
                asyncio.run_coroutine_threadsafe(assistant_instance.stop(), assistant_loop)
            else:
                print("Warning: Assistant loop not running, cannot schedule stop().")

        # Wait for the runner thread to finish (optional, depends on cleanup needs)
        if runner_thread and runner_thread.is_alive():
             print("Waiting for assistant runner thread to join...")
             runner_thread.join(timeout=5)
             if runner_thread.is_alive():
                 print("Warning: Assistant runner thread did not join.")

        # Close the assistant's event loop
        if assistant_loop and not assistant_loop.is_closed():
             print("Closing assistant event loop.")
             # Run pending tasks and shutdown async generators
             assistant_loop.run_until_complete(assistant_loop.shutdown_asyncgens())
             assistant_loop.close()

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

if __name__ == '__main__':
    # Note: Use `flask run --host=0.0.0.0 --port=8089` or similar in production/development
    # For simple testing (ensure debug=False if using multiple threads/asyncio loops extensively):
    print("Starting Flask app for SIP Assistant...")
    app.run(host='0.0.0.0', port=8081, debug=False) # debug=True can cause issues with multiple loops/threads

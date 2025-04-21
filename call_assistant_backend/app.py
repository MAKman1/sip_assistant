import asyncio
import threading
import os
import signal
from flask import Flask, request, jsonify
from dotenv import load_dotenv # Import load_dotenv
from gemini_assistant import AudioLoop, DEFAULT_MODE

# Load environment variables from .env file
load_dotenv()

# Check for GOOGLE_API_KEY AFTER loading .env
if not os.getenv('GOOGLE_API_KEY'):
    raise ValueError("GOOGLE_API_KEY environment variable not set. Make sure it's in your .env file or environment.")

app = Flask(__name__)

# Global state to hold the assistant instance and its thread
assistant_instance: AudioLoop | None = None
assistant_thread: threading.Thread | None = None
assistant_lock = threading.Lock() # To protect access to global state

def run_assistant_loop(loop_instance: AudioLoop):
    """Creates and runs the asyncio event loop for the assistant in its own thread."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        print(f"Starting assistant event loop {loop} in thread {threading.current_thread().name}")
        loop.run_until_complete(loop_instance.run())
    except Exception as e:
        print(f"Error running assistant loop in thread {threading.current_thread().name}: {e}")
        # Optionally, update global state here if the thread crashes (already handled in run())
        global assistant_instance, assistant_thread
        # Global state update is now primarily handled within AudioLoop.run's finally block
        # but we can ensure it's cleared here too if the loop itself fails catastrophically.
        global assistant_instance, assistant_thread
        with assistant_lock:
            # Check if it wasn't already cleared by a graceful shutdown via /stop
            if assistant_instance is loop_instance:
                 assistant_instance = None
                 assistant_thread = None
    finally:
        print(f"Closing assistant event loop {loop} in thread {threading.current_thread().name}")
        # Ensure loop resources are cleaned up
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()

@app.route('/start', methods=['POST'])
def start_assistant():
    """
    Starts the Gemini assistant (Audio Only) in a background thread.
    """
    global assistant_instance, assistant_thread
    with assistant_lock:
        if assistant_thread and assistant_thread.is_alive():
            return jsonify({"error": "Assistant is already running"}), 400

        # Removed mode handling
        # data = request.get_json() or {}
        # video_mode = data.get('mode', DEFAULT_MODE)
        # if video_mode not in ['camera', 'screen', 'none']:
        #     return jsonify({"error": f"Invalid mode '{video_mode}'. Must be 'camera', 'screen', or 'none'."}), 400

        print(f"Starting assistant (Audio Only)")
        # Instantiate AudioLoop without video_mode
        assistant_instance = AudioLoop()
        # Use daemon=True so thread doesn't block app exit if not stopped gracefully
        assistant_thread = threading.Thread(target=run_assistant_loop, args=(assistant_instance,), daemon=True)
        assistant_thread.start()

        return jsonify({"status": "Assistant starting"}), 200 # Removed mode from response

@app.route('/send_message', methods=['POST'])
def send_message():
    """
    Sends a text message to the running assistant.
    Accepts 'message' in JSON body.
    """
    with assistant_lock:
        if not assistant_instance or not assistant_thread or not assistant_thread.is_alive():
            return jsonify({"error": "Assistant is not running"}), 400

        data = request.get_json()
        if not data or 'message' not in data:
            return jsonify({"error": "Missing 'message' in request body"}), 400

        message = data['message']
        print(f"Received message via API: {message}")

        # Put the message into the assistant's input queue
        # We need to run this within the assistant's event loop
        # Use asyncio.run_coroutine_threadsafe for thread safety
        if assistant_instance.text_in_queue:
             # Get the loop the assistant is running on (if possible, might need adjustment)
             # For simplicity, we assume the queue put is thread-safe enough for this demo
             # A more robust solution might involve run_coroutine_threadsafe if the loop was accessible
             try:
                 # Directly putting might be okay if Queue is thread-safe, but let's use run_coroutine_threadsafe
                 # Need the loop object for run_coroutine_threadsafe. This is tricky as it's run in a separate thread.
                 # Let's try putting directly for now, assuming asyncio.Queue handles basic thread safety for put_nowait.
                 # If issues arise, this needs refinement (e.g., passing the loop object around).
                 # Using put_nowait as we are calling from a different thread.
                 assistant_instance.text_in_queue.put_nowait(message)
                 return jsonify({"status": "Message sent"}), 200
             except asyncio.QueueFull:
                 return jsonify({"error": "Message queue is full"}), 503
             except Exception as e:
                 print(f"Error putting message in queue: {e}")
                 return jsonify({"error": "Failed to queue message"}), 500
        else:
             return jsonify({"error": "Assistant instance not fully initialized"}), 500


@app.route('/stop', methods=['POST'])
def stop_assistant():
    """Stops the running assistant."""
    global assistant_instance, assistant_thread
    stopped = False
    with assistant_lock:
        if assistant_instance and assistant_thread and assistant_thread.is_alive():
            print("Stopping assistant...")
            # Signal the assistant to stop using its own async stop method
            # Get the loop the assistant is running on, which we stored
            loop = assistant_instance.loop
            if loop and loop.is_running():
                 print(f"Found running assistant loop {loop}. Scheduling stop() coroutine.")
                 future = asyncio.run_coroutine_threadsafe(assistant_instance.stop(), loop)
                 try:
                     # Wait briefly for the coroutine to potentially complete or raise an immediate error.
                     # The main goal is that stop() sets the shutdown_event.
                     future.result(timeout=2) # Reduced timeout - just checking for quick completion/errors.
                     print("Assistant stop() coroutine likely completed.")
                 except asyncio.TimeoutError:
                     print("Assistant stop() coroutine did not complete within timeout (expected if shutdown takes time).")
                     # The shutdown_event should have been set within stop() anyway.
                 except Exception as e:
                     # This catches errors *within* the stop() coroutine itself.
                     print(f"Error occurred within assistant stop() coroutine execution: {e}")
                     # Even with an error, the shutdown_event might have been set in stop()'s finally block.
                     # We'll rely on the thread join below.
            else:
                 # If loop isn't available or running, we can't schedule stop().
                 # Try setting the event directly if the instance exists.
                 print(f"Warning: Assistant loop not found or not running (Loop: {loop}). Attempting to set shutdown_event directly.")
                 if assistant_instance and hasattr(assistant_instance, 'shutdown_event'):
                     # This is a fallback and might not trigger cleanup in stop()
                     assistant_instance.shutdown_event.set()
                 else:
                     print("Error: Cannot set shutdown_event, assistant instance invalid.")
                     # If the instance is gone, the thread might already be dead.

            # --- Always attempt to join the thread ---
            print(f"Waiting for assistant thread {assistant_thread.name} to join...")


            # Wait for the thread to finish
            assistant_thread.join(timeout=10) # Wait up to 10 seconds
            if assistant_thread.is_alive():
                print("Warning: Assistant thread did not stop gracefully.")
                # Forceful termination is generally bad, but could be considered
                # as a last resort if the thread hangs indefinitely.
            else:
                print("Assistant thread joined.")
                stopped = True

            assistant_instance = None
            assistant_thread = None
        else:
            return jsonify({"status": "Assistant is not running"}), 200 # Not an error if already stopped

    if stopped:
        return jsonify({"status": "Assistant stopped successfully"}), 200
    else:
        return jsonify({"status": "Assistant stop initiated, but thread may still be running"}), 202


# Optional: Add a simple root route
@app.route('/')
def index():
    return "Gemini Call Assistant Backend is running."

if __name__ == '__main__':
    # Note: Use `flask run` in production/development instead of app.run()
    # For simple testing:
    app.run(debug=True, host='0.0.0.0', port=8089) # Use 0.0.0.0 to be accessible externally if needed

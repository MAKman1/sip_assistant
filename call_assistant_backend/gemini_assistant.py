# -*- coding: utf-8 -*-
# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Core Gemini Live API assistant logic adapted for backend use (Audio Only).
"""

import asyncio
# import base64 # No longer needed
import io
import os
import sys
import traceback

# import cv2 # No longer needed
import pyaudio
# import PIL.Image # No longer needed
# import mss # No longer needed

from google import genai
from google.genai import types # Import types for FunctionDeclaration

# Conditional imports for older Python versions
if sys.version_info < (3, 11, 0):
    import taskgroup, exceptiongroup
    asyncio.TaskGroup = taskgroup.TaskGroup
    asyncio.ExceptionGroup = exceptiongroup.ExceptionGroup

FORMAT = pyaudio.paInt16
CHANNELS = 1
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE = 1024

MODEL = "models/gemini-2.0-flash-live-001"

DEFAULT_MODE = "none" # Default to none for backend usage

# --- System Instructions ---
SYSTEM_INSTRUCTIONS = """
**System Instructions: Arham Khan Persona**

**Role:** You are Arham Khan, a 30-year-old male.

**Do NOT deviate from the following details under any circumstances.**

**Do NOT engage in discussions that are irrelevant to the core topic of finding out if my company holds any pension**

**If you see the conversation deviating away from the core topic, politely bring it back to the point**

**Do not share the NI number, address and date of birth until you're specifically asked for these**

**Personal Details:**

* **Full Name:** Arham Khan
* **Gender:** Male
* **Date of Birth:** 24 September 1994
* **Address:** 64 Adagio Point, Greenwich, London, UK
* **National Insurance Number:** YI98302D
* **Phone Number:** 03339293020
* **Email Address:** james@mak.com (Use this if absolutely necessary, but prioritize phone for contact)
* **Resident:** UK

**Objective:**

You are talking to a customer support assistant at a pension's company called Wagestream Pensions limited. Your primary goal is to inquire about any outstanding pension policies held with my company from your previous employment. You want to find out if a pension exists, and if so, what its approximate current value is.

**You are NOT the customer support assistant, you are Arham Khan who is looking to learn more about his past pension policies**

**Past Employment History (for Pension Inquiries):**

You have approximately 20 years of experience as a Software Engineer in the UK. Use the following employment history when discussing your past work with pension providers. You may not remember exact start and end dates, so approximate within a reasonable timeframe.

1.  **Company:** Global Tech Solutions Ltd (Large IT Firm)
    * **Location:** London, UK
    * **Job Title:** Senior Software Engineer
    * **Approximate Dates:** 2014 – 2021 (7 years)
    * **Reason for Leaving:** Sought a new challenge/Company restructuring (be vague)

2.  **Company:** Innovate Software PLC (Medium-sized Software Company)
    * **Location:** Reading, UK
    * **Job Title:** Software Engineer
    * **Approximate Dates:** 2007 – 2014 (7 years)
    * **Reason for Leaving:** Relocated back to London

3.  **Company:** Web Dynamics Ltd (Small Web Development Agency)
    * **Location:** Croydon, UK
    * **Job Title:** Junior Software Developer/Software Developer
    * **Approximate Dates:** 2001 – 2007 (6 years)
    * **Reason for Leaving:** Wanted to move into a more corporate environment

**Call Guidelines:**

1.  **Introduction:** Start the call with a casual opening: "Hey... how's it going?"
2.  **Purpose:** Follow with a clear statement of your intent, for example: "I'm looking to get details of my pension with you"
4.  **Provide Details:** Be ready to provide your personal details (name, DOB, NI number, address, phone number) to help them locate any potential records.
5.  **Employment Details:** If asked, provide the name(s) of your previous employer(s) and your approximate dates of employment with them.
6.  **Inquire about Value:** If a pension policy is found, politely ask for the current value or the process to obtain a valuation. For example, "Could you please tell me the current value of the pension?" or "What is the process for me to get an up-to-date valuation?"
7.  **Be Patient and Polite:** You might be transferred or put on hold. Maintain a polite and patient demeanor throughout the call.
8.  **Slight Uncertainty:** It's natural to be slightly uncertain about exact employment dates or the precise name of the pension scheme from years ago. Don't be afraid to say things like, "I believe it was around [year]" or "I don't recall the exact name of the scheme."
9.  **Do NOT Invent Information:** If you genuinely don't know something, state that. Do not guess or make up details about pension policies or values.
10. **Contact Preference:** If they need to send you information, state your preference is via post to your address (64 Adagio Point, Greenwich, London). You can provide your email (james@mak.com) if necessary, but postal mail is preferred.
11. **Tone:** While the opening is casual, maintain a generally polite and respectful tone appropriate for someone making an official inquiry, especially after the opening. Use appropriate British English phrasing and politeness (e.g., "Could you possibly...", "Thank you very much").

**You are not the customer support assistant, you are the customer. You must NOT say things like: 'how can I help you' or other things a customer support assistant might say**

Respond in a very human format and include relevant breaks and pauses in your responses. You must not give long answers unless necessary

Any time you are provided with some information about your pension or data associated to your pension, call the "render_db_call" function I have provided you. Save as many key facts about the pension as possible via the function call. Things like, 'found a pension' or 'no pension found' are also valid notes.
"""

# --- Function Declaration ---
render_db_call_func = types.FunctionDeclaration(
    name='render_db_call',
    description='Call this function when specific pension information is provided or received (e.g., policy found/not found, value, provider details, dates). Pass the information as the pension_detail argument.',
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            'pension_detail': types.Schema(type=types.Type.STRING, description="A concise summary of the specific pension information.")
        },
        required=['pension_detail']
    )
)

# --- Tool Configuration ---
TOOLS = [
    {'google_search': {}},
    {'function_declarations': [render_db_call_func]}
    # Note: Code Execution is implicitly available when other tools are used in Gemini 2 Live API
]

client = genai.Client(http_options={"api_version": "v1beta"})

# --- Updated Config ---
CONFIG = {
    "system_instruction": SYSTEM_INSTRUCTIONS,
    "tools": TOOLS,
    "response_modalities": ["AUDIO"] # Keep audio response
}

pya = pyaudio.PyAudio()


class AudioLoop:
    # Removed video_mode parameter
    def __init__(self):
        # self.video_mode = video_mode # Removed
        self.audio_in_queue = None # Queue for incoming audio from Gemini
        self.out_queue = None # Queue for outgoing audio/text to Gemini
        self.text_in_queue = asyncio.Queue() # Queue for incoming text messages from Flask
        self.shutdown_event = asyncio.Event() # Event to signal shutdown
        self.session = None
        self.audio_stream = None # Reference to the input audio stream
        self.loop = None # To store the event loop this instance runs on

    async def send_text(self):
        """Reads text messages from the text_in_queue and sends them to Gemini."""
        while not self.shutdown_event.is_set():
            try:
                # Wait for a message with a timeout to allow checking the shutdown event
                text = await asyncio.wait_for(self.text_in_queue.get(), timeout=0.5)
                if text is None: # Use None as a signal to potentially break, though shutdown_event is preferred
                    break
                if self.session:
                    print(f"Sending text: {text}") # Log sending
                    await self.session.send(input=text or ".", end_of_turn=True)
                self.text_in_queue.task_done()
            except asyncio.TimeoutError:
                continue # No message, loop back and check shutdown_event
            except Exception as e:
                print(f"Error in send_text: {e}")
                traceback.print_exc()
                break # Exit on other errors

    # Removed _get_frame, get_frames, _get_screen, get_screen methods

    async def send_realtime(self):
        """Reads from the out_queue and sends data (audio/text) to Gemini."""
        while not self.shutdown_event.is_set():
            try:
                # Wait for an item with a timeout to allow checking the shutdown event
                msg = await asyncio.wait_for(self.out_queue.get(), timeout=0.5)
                if self.session:
                    await self.session.send(input=msg)
                self.out_queue.task_done()
            except asyncio.TimeoutError:
                continue # No item, loop back and check shutdown_event
            except Exception as e:
                print(f"Error in send_realtime: {e}")
                traceback.print_exc()
                # Decide if we should break or continue based on error type
                if isinstance(e, (ConnectionError, asyncio.CancelledError)):
                     break
        print("Send realtime task stopped.")


    async def listen_audio(self):
        """Listens to the microphone and puts audio chunks in the out_queue."""
        mic_info = None
        try:
            mic_info = await asyncio.to_thread(pya.get_default_input_device_info)
            self.audio_stream = await asyncio.to_thread(
                pya.open,
                format=FORMAT,
                channels=CHANNELS,
                rate=SEND_SAMPLE_RATE,
                input=True,
                input_device_index=mic_info["index"],
                frames_per_buffer=CHUNK_SIZE,
            )
            print("Microphone stream opened.")
            if __debug__:
                kwargs = {"exception_on_overflow": False}
            else:
                kwargs = {}

            while not self.shutdown_event.is_set():
                try:
                    # Read with a small timeout indirectly via loop timing
                    data = await asyncio.to_thread(self.audio_stream.read, CHUNK_SIZE, **kwargs)
                    if self.out_queue.full():
                        # print("Warning: Out queue is full, dropping audio frame.")
                        await self.out_queue.get() # Drop oldest frame if full to prioritize recent audio
                    await self.out_queue.put({"data": data, "mime_type": "audio/pcm"})
                except IOError as e:
                    # Handle potential overflows if not handled by exception_on_overflow=False
                    if e.errno == pyaudio.paInputOverflowed:
                        print("Warning: Input overflowed. Dropping audio frame.")
                    else:
                        raise # Re-raise other IOErrors
                await asyncio.sleep(0.01) # Small sleep to prevent tight loop if read fails quickly

        except Exception as e:
            print(f"Error in listen_audio: {e}")
            if mic_info:
                 print(f"Using mic: {mic_info.get('name', 'Unknown')}")
            else:
                 print("Could not get default mic info.")
            traceback.print_exc()
        finally:
            if self.audio_stream and self.audio_stream.is_active():
                await asyncio.to_thread(self.audio_stream.stop_stream)
                await asyncio.to_thread(self.audio_stream.close)
            print("Microphone stream closed.")


    async def receive_audio(self):
        """Receives responses from Gemini and handles audio/text."""
        while not self.shutdown_event.is_set():
            try:
                if not self.session:
                    await asyncio.sleep(0.1)
                    continue

                turn = self.session.receive() # This might block until a turn starts
                async for response in turn:
                    if self.shutdown_event.is_set():
                        break # Check event after potentially blocking receive
                    if data := response.data:
                        self.audio_in_queue.put_nowait(data)
                    if text := response.text:
                        # In a backend, printing might not be ideal.
                        # Consider logging or putting text into another queue for Flask.
                        print(f"Received text: {text}", end="") # Keep print for now

                # If you interrupt the model, it sends a turn_complete.
                # Empty out the audio queue because it may have loaded
                # much more audio than has played yet.
                while not self.audio_in_queue.empty():
                    self.audio_in_queue.get_nowait()

            except asyncio.CancelledError:
                print("Receive audio task cancelled.")
                break
            except Exception as e:
                print(f"Error receiving audio/text/tool_call: {e}")
                traceback.print_exc()
                # Decide if we should break or continue
                if isinstance(e, (ConnectionError, asyncio.CancelledError)):
                    break
                await asyncio.sleep(1) # Wait a bit before retrying on other errors

            # --- Handle Tool Calls ---
            if tool_call := response.tool_call:
                await self.handle_tool_call(tool_call)

        print("Receive audio task stopped.")

    async def handle_tool_call(self, tool_call):
        """Handles function calls received from the Gemini API."""
        function_responses = []
        for fc in tool_call.function_calls:
            print(f"--- Received Tool Call: {fc.name} ---")
            if fc.name == 'render_db_call':
                pension_detail = fc.args.get('pension_detail', 'No detail provided')
                # --- Execute Local Function ---
                print(f"--- RENDER DB CALL --- Pension Detail: {pension_detail} ---")
                # --- Prepare Response ---
                function_responses.append(types.FunctionResponse(
                    id=fc.id,
                    name=fc.name,
                    response={'result': f'Pension detail logged: {pension_detail}'},
                ))
            else:
                # Handle other potential function calls if added later
                print(f"Warning: Received unhandled function call: {fc.name}")
                function_responses.append(types.FunctionResponse(
                    id=fc.id,
                    name=fc.name,
                    response={'error': f'Function {fc.name} not implemented'},
                ))

        # --- Send Tool Response Back to API ---
        if function_responses and self.session:
            tool_response = types.LiveClientToolResponse(function_responses=function_responses)
            print(f"Sending tool response: {tool_response}")
            await self.session.send(input=tool_response)


    async def play_audio(self):
        """Plays audio received from Gemini."""
        stream = None
        try:
            stream = await asyncio.to_thread(
                pya.open,
                format=FORMAT,
                channels=CHANNELS,
                rate=RECEIVE_SAMPLE_RATE,
                output=True,
            )
            print("Audio output stream opened.")
            while not self.shutdown_event.is_set():
                try:
                    # Wait for audio data with a timeout
                    bytestream = await asyncio.wait_for(self.audio_in_queue.get(), timeout=0.5)
                    await asyncio.to_thread(stream.write, bytestream)
                    self.audio_in_queue.task_done()
                except asyncio.TimeoutError:
                    continue # No audio data, check shutdown event
                except Exception as e:
                    print(f"Error playing audio chunk: {e}")
                    # Don't break the loop for minor playback errors, just log.
            print("Audio playback loop finished.")
        except Exception as e:
            print(f"Error initializing or during play_audio: {e}")
            traceback.print_exc()
        finally:
            if stream and stream.is_active():
                await asyncio.to_thread(stream.stop_stream)
                await asyncio.to_thread(stream.close)
            # Ensure queue is clear if loop exits unexpectedly
            while not self.audio_in_queue.empty():
                self.audio_in_queue.get_nowait()
            print("Audio output stream closed.")

    async def stop(self):
        """Signals all tasks to shut down."""
        print("Initiating shutdown...")
        self.shutdown_event.set()
        # Optionally put a sentinel value in queues if tasks might block indefinitely
        await self.text_in_queue.put(None) # Signal send_text to potentially exit faster


    async def run(self):
        """Main execution loop for the assistant."""
        all_tasks = []
        try:
            # Get the current event loop for this thread/task
            self.loop = asyncio.get_running_loop()
            print(f"AudioLoop running on event loop: {self.loop}")

            async with client.aio.live.connect(model=MODEL, config=CONFIG) as session:
                self.session = session
                print("Gemini Live session connected.")

                self.audio_in_queue = asyncio.Queue()
                self.out_queue = asyncio.Queue(maxsize=20) # Increased size slightly

                async with asyncio.TaskGroup() as tg:
                    print("Starting background tasks...")
                    # Core tasks
                    all_tasks.append(tg.create_task(self.send_text(), name="send_text"))
                    all_tasks.append(tg.create_task(self.send_realtime(), name="send_realtime"))
                    all_tasks.append(tg.create_task(self.listen_audio(), name="listen_audio"))
                    all_tasks.append(tg.create_task(self.receive_audio(), name="receive_audio"))
                    all_tasks.append(tg.create_task(self.play_audio(), name="play_audio"))

                    # Removed conditional video tasks
                    print("Audio/Text tasks started. Waiting for shutdown signal...")
                    # Wait until shutdown is signaled
                    await self.shutdown_event.wait()
                    print("Shutdown signal received. Cancelling tasks...")

        except asyncio.CancelledError:
            print("Run loop cancelled externally.")
        except ExceptionGroup as eg:
            print("Exception Group caught in run:")
            for i, exc in enumerate(eg.exceptions):
                 print(f"  Exception {i+1}/{len(eg.exceptions)}: {type(exc).__name__}: {exc}")
                 # traceback.print_exception(type(exc), exc, exc.__traceback__) # More detail if needed
        except Exception as e:
            print(f"Unexpected error in run: {type(e).__name__}: {e}")
            traceback.print_exc()
        finally:
            print("Run loop finished. Cleaning up...")
            # Ensure shutdown event is set even if loop exited unexpectedly
            self.shutdown_event.set()

            # Explicitly cancel any tasks that might still be running
            # (TaskGroup should handle this, but belt-and-suspenders)
            for task in all_tasks:
                if not task.done():
                    task.cancel()
            # Give cancelled tasks a moment to finish cleanup
            await asyncio.sleep(0.5)

            # Close PyAudio instance
            if pya:
                await asyncio.to_thread(pya.terminate)
            print("PyAudio terminated.")
            print("Assistant stopped.")

# Note: The if __name__ == "__main__": block is removed as this
# will be controlled by the Flask app.

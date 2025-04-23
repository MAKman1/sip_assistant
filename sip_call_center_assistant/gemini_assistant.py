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
Core Gemini Live API assistant logic adapted for backend use (Audio Only via Queues).
"""

import asyncio
import io
import os
import sys
import traceback
import queue # Added for thread-safe queue

# import pyaudio # Removed - Audio handled by app.py via WebSocket
from google import genai
from google.genai import types # Import types for FunctionDeclaration

# Conditional imports for older Python versions
if sys.version_info < (3, 11, 0):
    import taskgroup, exceptiongroup
    asyncio.TaskGroup = taskgroup.TaskGroup
    asyncio.ExceptionGroup = exceptiongroup.ExceptionGroup

# --- Constants Removed (Handled in app.py or Gemini specific) ---
# FORMAT = pyaudio.paInt16
# CHANNELS = 1
# SEND_SAMPLE_RATE = 16000 # Input rate to Gemini (set in app.py conversion)
# RECEIVE_SAMPLE_RATE = 24000 # Output rate from Gemini (set in app.py conversion)
# CHUNK_SIZE = 1024

# MODEL = "models/gemini-2.0-flash-live-001"
MODEL = "models/gemini-2.0-flash-live-001"

# --- System Instructions (Keep as is) ---
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

# --- Function Declaration (Keep as is) ---
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

# --- Tool Configuration (Keep as is) ---
TOOLS = [
    {'google_search': {}},
    {'function_declarations': [render_db_call_func]}
]

client = genai.Client(http_options={"api_version": "v1beta"})

# --- Updated Config (Keep as is) ---
CONFIG = {
    "system_instruction": SYSTEM_INSTRUCTIONS,
    "tools": TOOLS,
    "response_modalities": ["AUDIO"], # Keep audio response
    "speech_config": types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Fenrir")
        ),
        language_code="en-GB"
    )
}

# pya = pyaudio.PyAudio() # Removed


class AudioLoop:
    # Added transcript_queue to init parameters
    def __init__(self, audio_in_queue: asyncio.Queue, out_queue: asyncio.Queue, transcript_queue: asyncio.Queue):
        self.audio_in_queue = audio_in_queue # Queue for incoming audio FROM Gemini (to be sent to Twilio by app.py)
        self.out_queue = out_queue # Queue for outgoing audio/text TO Gemini (received from Twilio via app.py)
        self.transcript_queue = transcript_queue # Queue for sending transcript text back to app.py
        self.text_in_queue = asyncio.Queue() # Queue for incoming text messages (e.g., from future API endpoint if needed)
        self.shutdown_event = asyncio.Event() # Event to signal shutdown
        self.session = None
        # self.audio_stream = None # Removed - No direct audio stream handling here
        self.loop = None # To store the event loop this instance runs on

    async def send_text(self):
        """Reads text messages from the text_in_queue and sends them to Gemini."""
        # This might be less relevant if text input isn't used in the SIP flow, but keep for potential future use.
        while not self.shutdown_event.is_set():
            try:
                text = await asyncio.wait_for(self.text_in_queue.get(), timeout=0.5)
                if text is None:
                    break
                # Check session before sending
                if self.session is not None:
                    try:
                        print(f"Sending text to Gemini: {text}") # Log sending
                        await self.session.send(input=text or ".", end_of_turn=True)
                    except (TypeError, AttributeError) as e:
                        print(f"Send Text: Session became invalid during send: {e}")
                        self.shutdown_event.set() # Signal shutdown
                        break
                    except Exception as e:
                        print(f"Send Text: Unexpected error during send: {e}")
                        self.shutdown_event.set() # Signal shutdown
                        break
                else:
                    print("Send Text: Session is None, stopping.")
                    self.shutdown_event.set() # Signal shutdown
                    break
                self.text_in_queue.task_done()
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                print(f"Error in send_text: {e}")
                traceback.print_exc()
                break
        print("Send text task stopped.")


    async def send_realtime(self):
        """Reads from the out_queue (audio from Twilio) and sends data to Gemini."""
        while not self.shutdown_event.is_set():
            try:
                # Wait for an item (audio chunk from Twilio via app.py)
                msg = await asyncio.wait_for(self.out_queue.get(), timeout=0.5)

                # Check session before sending
                if self.session is not None:
                    try:
                        # msg should be {"data": pcm16_data, "mime_type": "audio/pcm"} as prepared in app.py
                        await self.session.send(input=msg)
                    except (TypeError, AttributeError) as e:
                        print(f"Send Realtime: Session became invalid during send: {e}")
                        self.shutdown_event.set() # Signal shutdown
                        break
                    except Exception as e:
                        print(f"Send Realtime: Unexpected error during send: {e}")
                        self.shutdown_event.set() # Signal shutdown
                        break
                else:
                     print("Send Realtime: Session is None, stopping.")
                     self.shutdown_event.set() # Signal shutdown
                     break
                self.out_queue.task_done()
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                print(f"Error in send_realtime (sending to Gemini): {e}")
                traceback.print_exc()
                if isinstance(e, (ConnectionError, asyncio.CancelledError)):
                     break
        print("Send realtime task (to Gemini) stopped.")


    # async def listen_audio(self): # Removed - Audio input comes via out_queue from app.py
    #     """Listens to the microphone and puts audio chunks in the out_queue."""
    #     pass


    async def receive_audio(self):
        """Receives responses (audio/text/tool calls) from Gemini and puts audio in audio_in_queue."""
        while not self.shutdown_event.is_set():
            try:
                if not self.session:
                    await asyncio.sleep(0.1)
                    continue

                # Check session before receiving
                if self.session is None:
                    print("Receive Audio: Session is None, stopping.")
                    self.shutdown_event.set()
                    break

                try:
                    turn = self.session.receive()
                    async for response in turn:
                        if self.shutdown_event.is_set():
                            break
                        if data := response.data:
                            # Put received audio data into the queue for app.py to pick up
                            print(f"Gemini Receiver: Received audio chunk (size: {len(data)} bytes)") # Log audio received
                            self.audio_in_queue.put_nowait(data)
                        if text := response.text:
                            # Put received text into the transcript queue for app.py
                            # print(f"Received text from Gemini: {text}", end="") # Optional: Keep logging if desired
                            self.transcript_queue.put_nowait(text) # Send text back to app.py

                        # --- Handle Tool Calls ---
                        if tool_call := response.tool_call:
                             await self.handle_tool_call(tool_call)

                    # Clear queue on interrupt (if applicable) - Moved inside try
                    while not self.audio_in_queue.empty():
                        self.audio_in_queue.get_nowait()

                except (TypeError, AttributeError) as e:
                    print(f"Receive Audio: Session became invalid during receive: {e}")
                    self.shutdown_event.set()
                    break
                except asyncio.CancelledError:
                    # This specific exception should be handled by the outer block
                    raise
                except Exception as e:
                    # Catch other errors during receive/iteration
                    print(f"Receive Audio: Error during receive/iteration: {e}")
                    traceback.print_exc()
                    self.shutdown_event.set()
                    break

            except asyncio.CancelledError:
                print("Receive audio task cancelled.")
                break
            except Exception as e:
                print(f"Error receiving from Gemini: {e}")
                traceback.print_exc()
                if isinstance(e, (ConnectionError, asyncio.CancelledError)):
                    break
                await asyncio.sleep(1)

        print("Receive audio task (from Gemini) stopped.")

    async def handle_tool_call(self, tool_call):
        """Handles function calls received from the Gemini API."""
        # (Keep this method as is)
        function_responses = []
        for fc in tool_call.function_calls:
            print(f"--- Received Tool Call: {fc.name} ---")
            if fc.name == 'render_db_call':
                pension_detail = fc.args.get('pension_detail', 'No detail provided')
                print(f"--- RENDER DB CALL --- Pension Detail: {pension_detail} ---")
                function_responses.append(types.FunctionResponse(
                    id=fc.id,
                    name=fc.name,
                    response={'result': f'Pension detail logged: {pension_detail}'},
                ))
            else:
                print(f"Warning: Received unhandled function call: {fc.name}")
                function_responses.append(types.FunctionResponse(
                    id=fc.id,
                    name=fc.name,
                    response={'error': f'Function {fc.name} not implemented'},
                ))

        if function_responses:
            # Check session before sending tool response
            if self.session is not None:
                try:
                    tool_response = types.LiveClientToolResponse(function_responses=function_responses)
                    print(f"Sending tool response to Gemini: {tool_response}")
                    await self.session.send(input=tool_response)
                except (TypeError, AttributeError) as e:
                    print(f"Handle Tool Call: Session became invalid during send: {e}")
                    self.shutdown_event.set() # Signal shutdown
                except Exception as e:
                    print(f"Handle Tool Call: Unexpected error during send: {e}")
                    self.shutdown_event.set() # Signal shutdown
            else:
                print("Handle Tool Call: Session is None, cannot send tool response.")
                self.shutdown_event.set() # Signal shutdown


    # async def play_audio(self): # Removed - Audio output sent via audio_in_queue to app.py
    #     """Plays audio received from Gemini."""
    #     pass


    async def stop(self):
        """Signals all tasks to shut down."""
        print("Initiating Gemini Assistant shutdown...")
        self.shutdown_event.set()
        # Signal send_text to potentially exit faster
        await self.text_in_queue.put(None)


    async def run(self):
        """Main execution loop for the assistant (connects to Gemini, manages queues)."""
        all_tasks = []
        try:
            self.loop = asyncio.get_running_loop()
            print(f"AudioLoop running on event loop: {self.loop}")

            print("Attempting to connect to Gemini Live API...") # Added log
            async with client.aio.live.connect(model=MODEL, config=CONFIG) as session:
                self.session = session
                print("Gemini Live session connected.") # Existing log

                # Queues are now created and managed by app.py, but we need references
                # For simplicity, let's assume they are created before run() is called
                # and potentially passed in or set externally.
                # Queues (audio_in_queue, out_queue, transcript_queue) are now expected
                # to be passed in during initialization by app.py.
                # No need to create them here anymore.
                if not all([self.audio_in_queue, self.out_queue, self.transcript_queue]):
                     print("Error: AudioLoop queues not initialized before run().")
                     # Consider raising an exception or handling this error appropriately
                     return # Stop execution if queues are missing

                async with asyncio.TaskGroup() as tg:
                    print("Starting Gemini background tasks...")
                    # Core tasks interacting with Gemini API and queues
                    all_tasks.append(tg.create_task(self.send_text(), name="send_text_to_gemini"))
                    all_tasks.append(tg.create_task(self.send_realtime(), name="send_realtime_to_gemini"))
                    all_tasks.append(tg.create_task(self.receive_audio(), name="receive_from_gemini"))
                    # Removed listen_audio and play_audio tasks

                    print("Gemini tasks started. Waiting for shutdown signal...")
                    await self.shutdown_event.wait()
                    print("Shutdown signal received by Gemini loop. Cancelling tasks...")

            print("Exited Gemini Live API connection block.") # Added log

        except asyncio.CancelledError:
            print("Gemini run loop cancelled externally.")
        except ExceptionGroup as eg:
            print("Exception Group caught in Gemini run:")
            for i, exc in enumerate(eg.exceptions):
                 print(f"  Exception {i+1}/{len(eg.exceptions)}: {type(exc).__name__}: {exc}")
                 traceback.print_exc() # Added traceback here
        except Exception as e:
            print(f"Unexpected error in Gemini run: {type(e).__name__}: {e}")
            traceback.print_exc() # Ensure traceback is printed here too
        finally:
            print("Gemini run loop finished. Cleaning up...")
            self.shutdown_event.set() # Ensure it's set

            for task in all_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.sleep(0.5) # Allow cancellations

            # Removed PyAudio termination
            # if pya:
            #     await asyncio.to_thread(pya.terminate)
            # print("PyAudio terminated.") # Removed
            print("Gemini Assistant stopped.")

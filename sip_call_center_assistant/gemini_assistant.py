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
System Instructions: Starling Bank Customer Support Agent

1. Role:

You are a friendly, helpful, and highly competent Customer Support Agent for Starling Bank, a leading digital bank in the UK. You are the first point of contact for customers calling for assistance.

2. Brand Identity & Tone:

Friendly & Approachable: Your tone should be warm, welcoming, and conversational, reflecting Starling's modern and customer-centric approach. Use positive language.

Human & Empathetic: Respond like a real person, showing understanding and empathy towards the customer's situation. Avoid robotic or overly formal language. Use natural pauses and phrasing.

Helpful & Eager: Convey a genuine desire to assist the customer and resolve their query efficiently. Be proactive in offering solutions or next steps.

Professional & Knowledgeable: While friendly, maintain professionalism. Demonstrate a strong understanding of Starling Bank's products, services, app features, and common procedures.

Clear & Concise: Provide information and instructions in a way that is easy to understand. Avoid jargon where possible, or explain it clearly if necessary.

UK Context: Use British English spelling and terminology (e.g., "current account," "sort code," "cheque," "PIN"). Be aware of UK banking practices.

3. Core Objective:

Your primary goal is to provide outstanding customer support by:

Answering customer questions accurately and efficiently.

Guiding customers on how to use the Starling Bank app and online services.

Assisting with account management tasks and resolving issues where possible via simulated "phone banking."

Ensuring the customer feels heard, valued, and satisfied with the interaction.

4. Key Responsibilities & Capabilities:

Initial Contact & Verification:

Greet the caller warmly (e.g., "Hello, thanks for calling Starling Bank! My name is [Your Agent Name], how can I help you today?").

Crucially, early in the conversation, politely request the caller's full name and Starling account number to access their (simulated) details and personalize the service. (e.g., "To help me access your details, could I please take your full name and Starling account number?"). You may also need to ask for other security details if relevant to the specific task, simulating standard banking security procedures (but do NOT ask for full PINs, passwords, or CVV codes).

Query Handling:

Listen carefully to understand the customer's needs.

Answer questions about Starling features (Spaces, Bills Manager, transaction history, card controls, international payments, fees, etc.).

Explain how to perform actions within the Starling app (e.g., "You can freeze your card directly in the app under the 'Card' section. Would you like me to guide you through the steps?").

Provide information on standard banking processes (Direct Debits, Standing Orders, Faster Payments, CHAPS, cheque imaging).

Simulated Phone Banking Actions:

Act as if you can perform certain actions for the customer after successful verification. Examples include:

Checking account balances and recent transactions.

Confirming if a specific payment has been sent or received.

Explaining transaction details or fees.

Placing temporary blocks on cards (simulated).

Ordering a replacement card (guiding them through the process or simulating initiation).

Updating contact details (simulating the process after verification).

Limitations: Clearly state when an action must be performed by the customer themselves via the app or website for security reasons (e.g., changing passwords, making payments, setting up new payees). You cannot actually move money or change sensitive security credentials.

Guidance & Routing:

If a customer needs to perform an action in the app, provide clear, step-by-step instructions.

If the query requires a specialist team (e.g., complex fraud, business banking specifics, formal complaints), explain this clearly and outline the process for transferring them or how the customer can contact that team.

5. Conversation Management & Boundaries:

Stay Focused: Keep the conversation centered on Starling Bank products, services, and the customer's query.

Polite Redirection: If the customer brings up irrelevant topics (e.g., politics, religion, other banks not related to their query, personal opinions on unrelated matters), politely steer the conversation back. Use phrases like:

"That's an interesting point, but to make sure I can help you fully with your Starling account today, could we focus back on [original query]?"

"I understand you feel strongly about that, however, my role here is to assist you with your Starling banking needs. Were you still needing help with [original query]?"

"I'm not really equipped to discuss [off-topic subject], but I can help you with..."

Objectivity & Neutrality: Do NOT express personal opinions, biases, or engage in discussions about sensitive, controversial, political, or religious topics. Remain neutral and objective, focusing solely on providing factual banking support.

Do Not Speculate: If you don't know the answer to something, admit it and offer to find out (simulated) or guide the customer to where they can find the information. Do not guess.

6. Human Interaction:

Use the customer's name occasionally (once verified) to personalize the interaction.

Show empathy (e.g., "I understand that must be frustrating," "I can see why you'd ask that").

Use filler words and natural pauses typical of human conversation, but maintain clarity.

7. Security:

Adhere strictly to simulated security protocols.

Never ask for full passwords, PINs, or the 3-digit CVV code from the back of the card. You may simulate asking for specific characters from a password or memorable information as part of a verification script if relevant to the task.

Remind customers not to share sensitive details unnecessarily.

8. Closing:

Summarize the resolution or next steps.

Ask if there's anything else you can help with today.

End the call politely (e.g., "Thanks again for calling Starling Bank, [Customer Name]. Have a great day!").
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

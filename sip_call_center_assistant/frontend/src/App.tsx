import React, { useState, useEffect } from 'react';
import { io, Socket } from 'socket.io-client';

// Define the structure of a call object
interface Call {
  call_sid: string; // Using call_sid as the primary identifier
  from_number: string;
  status?: string; // e.g., 'ringing', 'active', 'ended'
  transcript: string; // Store transcript as a single string
}

// Interface for transcript update event data
interface TranscriptUpdate {
  stream_sid: string; // Backend sends stream_sid, we'll map it to call_sid
  transcript_chunk: string;
}

// Interface for call status update event data
interface CallStatusUpdate {
  call_sid: string;
  status: string; // e.g., 'ringing', 'in-progress', 'completed', 'failed'
}

// Define the backend URL (adjust if your Flask app runs elsewhere)
const SOCKET_URL = 'http://localhost:8081';

function App() {
  const [isConnected, setIsConnected] = useState(false);
  const [calls, setCalls] = useState<Call[]>([]);
  const [selectedCallSid, setSelectedCallSid] = useState<string | null>(null); // Track selected call
  const [socket, setSocket] = useState<Socket | null>(null);
  const [phoneNumber, setPhoneNumber] = useState<string>(''); // State for phone number input
  const [callStatusMessage, setCallStatusMessage] = useState<string>(''); // State for call status feedback

  // Function to handle initiating the call
  const handleInitiateCall = () => {
    if (!socket || !isConnected) {
      setCallStatusMessage('Error: Not connected to backend.');
      return;
    }
    if (!phoneNumber.trim()) {
      setCallStatusMessage('Error: Please enter a phone number.');
      console.log('[handleInitiateCall] Validation failed: Not connected.');
      setCallStatusMessage('Error: Not connected to backend.');
      return;
    }
    if (!phoneNumber.trim()) {
      console.log('[handleInitiateCall] Validation failed: No phone number.');
      setCallStatusMessage('Error: Please enter a phone number.');
      return;
    }
    console.log(`[handleInitiateCall] Emitting 'initiate_call' for number: ${phoneNumber}`);
    setCallStatusMessage(`Initiating call to ${phoneNumber}...`);
    socket.emit('initiate_call', { to_number: phoneNumber });
    console.log("[handleInitiateCall] 'initiate_call' emitted.");
  };


  useEffect(() => {
    // Initialize Socket.IO connection
    const newSocket = io(SOCKET_URL);
    setSocket(newSocket);

    newSocket.on('connect', () => {
      console.log('Connected to backend:', newSocket.id);
      setIsConnected(true);
    });

    newSocket.on('disconnect', () => {
      console.log('Disconnected from backend');
      setIsConnected(false);
    });

    // Listen for new call events from the backend
    newSocket.on('new_call', (callData: Call) => {
      console.log('New call received:', callData);
      setCalls((prevCalls) => {
        // Avoid adding duplicate calls if event fires multiple times
        if (prevCalls.some(call => call.call_sid === callData.call_sid)) {
          return prevCalls;
        }
        // Initialize transcript as empty string
        return [...prevCalls, { ...callData, status: 'ringing', transcript: '' }];
      });
    });

    // Listen for transcript updates
    newSocket.on('call_transcript_update', (updateData: TranscriptUpdate) => {
      console.log('Transcript update:', updateData);
      setCalls((prevCalls) =>
        prevCalls.map((call) => {
          // Assuming stream_sid from backend corresponds to call_sid for now
          if (call.call_sid === updateData.stream_sid) {
            // Append the new chunk to the existing transcript
            return { ...call, transcript: call.transcript + updateData.transcript_chunk };
          }
          return call;
        })
      );
      // TODO: Auto-scroll transcript view if the updated call is selected
    });

    // Listen for call status updates
    newSocket.on('call_status_update', (updateData: CallStatusUpdate) => {
      console.log('Call status update:', updateData);
      setCalls((prevCalls) =>
        prevCalls.map((call) =>
          call.call_sid === updateData.call_sid
            ? { ...call, status: updateData.status } // Update status
            : call
        )
      );
      // If the call ended, maybe deselect it or move it to a history list later
      if (['completed', 'failed', 'busy', 'no-answer'].includes(updateData.status) && selectedCallSid === updateData.call_sid) {
          // Optionally deselect ended call
          // setSelectedCallSid(null);
      }
    });

    // Listen for call initiation success
    newSocket.on('call_initiated', (data: { call_sid: string; to_number: string }) => {
      console.log('[Socket Event] Received call_initiated:', data);
      setCallStatusMessage(`Call to ${data.to_number} initiated (SID: ${data.call_sid}). Waiting for status updates...`);
      // Optionally add the outbound call to the list immediately with 'initiating' status
      setCalls((prevCalls) => {
         if (prevCalls.some(call => call.call_sid === data.call_sid)) {
           return prevCalls; // Avoid duplicates if backend sends new_call too
         }
         return [...prevCalls, { call_sid: data.call_sid, from_number: 'Outbound', status: 'initiating', transcript: '' }];
       });
       setPhoneNumber(''); // Clear input field
    });

    // Listen for call initiation errors
    newSocket.on('call_initiation_error', (data: { error: string }) => {
      console.error('[Socket Event] Received call_initiation_error:', data.error);
      setCallStatusMessage(`Error initiating call: ${data.error}`);
    });


    // Cleanup on component unmount
    return () => {
      console.log('Disconnecting socket...');
      newSocket.disconnect();
    };
  }, []); // Empty dependency array ensures this runs only once on mount

  return (
    <div className="p-6 bg-gray-100 min-h-screen">
      <h1 className="text-3xl font-bold text-blue-700 mb-4">
        SIP Call Center Assistant Interface
      </h1>
      <div className="mb-4">
        <span className={`px-3 py-1 rounded-full text-sm font-semibold ${isConnected ? 'bg-green-200 text-green-800' : 'bg-red-200 text-red-800'}`}>
          {isConnected ? 'Connected' : 'Disconnected'} to Backend
        </span>
      </div>

      {/* Outbound Call Section */}
      <div className="mb-6 bg-white p-4 rounded shadow">
        <h2 className="text-xl font-semibold mb-3">Make Outbound Call</h2>
        <div className="flex items-center space-x-2">
          <input
            type="tel"
            value={phoneNumber}
            onChange={(e) => setPhoneNumber(e.target.value)}
            placeholder="Enter phone number (e.g., +1234567890)"
            className="flex-grow p-2 border rounded focus:outline-none focus:ring-2 focus:ring-blue-500"
            disabled={!isConnected} // Disable if not connected
          />
          <button
            onClick={handleInitiateCall}
            className={`px-4 py-2 rounded text-white font-semibold transition-colors ${
              !isConnected || !phoneNumber.trim()
                ? 'bg-gray-400 cursor-not-allowed'
                : 'bg-blue-600 hover:bg-blue-700'
            }`}
            disabled={!isConnected || !phoneNumber.trim()} // Disable if not connected or no number
          >
            Call
          </button>
        </div>
        {callStatusMessage && (
          <p className={`mt-2 text-sm ${callStatusMessage.startsWith('Error:') ? 'text-red-600' : 'text-gray-600'}`}>
            {callStatusMessage}
          </p>
        )}
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Incoming/Active Calls List */}
        <div className="bg-white p-4 rounded shadow">
          <h2 className="text-xl font-semibold mb-3 border-b pb-2">Incoming/Active Calls</h2>
          {calls.length === 0 ? (
            <p className="text-gray-500">No active calls.</p>
          ) : (
            <ul className="space-y-2 max-h-96 overflow-y-auto"> {/* Added max height and scroll */}
              {calls.map((call) => (
                <li
                  key={call.call_sid}
                  className={`p-3 border rounded cursor-pointer transition-colors ${selectedCallSid === call.call_sid ? 'bg-blue-100 border-blue-300' : 'bg-gray-50 hover:bg-gray-100'}`}
                  onClick={() => setSelectedCallSid(call.call_sid)} // Select call on click
                >
                  <p><strong>From:</strong> {call.from_number}</p>
                  <p className="text-xs text-gray-500">SID: {call.call_sid}</p>
                  <p><strong>Status:</strong> <span className="font-medium capitalize">{call.status || 'Unknown'}</span></p>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Transcript View */}
        <div className="bg-white p-4 rounded shadow">
          <h2 className="text-xl font-semibold mb-3 border-b pb-2">
            Transcript {selectedCallSid ? `(Call: ${calls.find(c => c.call_sid === selectedCallSid)?.from_number})` : ''}
          </h2>
          <div className="h-96 overflow-y-auto bg-gray-50 p-3 border rounded whitespace-pre-wrap"> {/* Added height, scroll, padding, wrap */}
            {selectedCallSid ? (
              calls.find(c => c.call_sid === selectedCallSid)?.transcript || <p className="text-gray-400 italic">No transcript yet...</p>
            ) : (
              <p className="text-gray-400 italic">Select a call to view transcript...</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export default App;
